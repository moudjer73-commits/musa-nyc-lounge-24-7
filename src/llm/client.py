import asyncio
import logging
import re
import time
from typing import Any, Type, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from src.config import gemini_model_chain, resolve_gemini_keys
from src.db.repo import TwinRepository

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

CALL_TIMEOUT_S = 14.0
STRUCTURED_TIMEOUT_S = 11.0
AUDIO_TIMEOUT_S = 20.0
HTTP_TIMEOUT_MS = 65_000
DEAD_PAIR_TTL_S = 1800.0
RATE_MIN_COOLDOWN_S = 65.0
RATE_DAY_COOLDOWN_S = 3600.0
TRANSIENT_COOLDOWN_S = 60.0
OVERLOAD_MODEL_COOLDOWN_S = 180.0


class QuotaExceededError(RuntimeError):
    """Raised when a user reaches their daily free-tier call limit."""


class LLMUnavailableError(RuntimeError):
    """Raised when all Gemini keys and fallback models fail."""

    def __init__(self, reason: str = "") -> None:
        super().__init__(
            "Connection to the language engine is momentarily busy. Give it a few seconds and send again."
        )
        self.reason = reason


def _redact(text: str) -> str:
    return re.sub(r"AIza[0-9A-Za-z_-]{20,}", "<REDACTED_KEY>", str(text))


def _classify(exc: Exception) -> str:
    low = f"{type(exc).__name__} {exc}".lower()
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429 or "resource_exhausted" in low or "quota" in low or "rate limit" in low:
        return "rate"
    if code == 404 or "not_found" in low or "is not found" in low or "no longer available" in low:
        return "model"
    if code in (401, 403) or "api key" in low or "api_key" in low or "permission_denied" in low:
        return "auth"
    if code in (500, 502, 503, 504) or "unavailable" in low or "overloaded" in low or "timeout" in low:
        return "transient"
    return "other"


def _thinking_cfg(model_name: str):
    if not hasattr(types, "ThinkingConfig"):
        return None
    low = model_name.lower()
    try:
        if low.startswith("gemini-3.8-flash"):
            return types.ThinkingConfig(thinking_budget=0)
        if low.startswith("gemini-3"):
            return types.ThinkingConfig(thinking_level="low")
    except Exception:
        return None
    return None


def _is_thinking_err(exc: Exception) -> bool:
    low = str(exc).lower()
    return "thinking" in low and ("400" in low or "invalid" in low or "not support" in low or "unsupported" in low)


def _with_http_timeout(cfg: types.GenerateContentConfig | None, timeout_s: float) -> types.GenerateContentConfig | None:
    if not hasattr(types, "GenerateContentConfig") or not hasattr(types, "HttpOptions"):
        return cfg
    if "http_options" not in getattr(types.GenerateContentConfig, "model_fields", {}):
        return cfg
    req_ms = int((timeout_s + 3.0) * 1000)
    try:
        opts = types.HttpOptions(timeout=req_ms)
        if cfg is None:
            return types.GenerateContentConfig(http_options=opts)
        if hasattr(cfg, "model_copy"):
            return cfg.model_copy(update={"http_options": opts})
    except Exception:
        return cfg
    return cfg


class GeminiClient:
    def __init__(self, repo: TwinRepository, api_keys: list[str] | None = None) -> None:
        self.repo = repo
        keys = api_keys if api_keys is not None else resolve_gemini_keys()
        self.clients = [self._build_sdk_client(k) for k in keys if k]
        self._dead_pairs: dict[tuple[int, str], float] = {}
        self._cooldown_until: dict[tuple[int, str], float] = {}
        self._model_cooldown_until: dict[str, float] = {}
        self._no_thinking: set[tuple[int, str]] = set()
        self._cursor = 0

    def configured(self) -> bool:
        return bool(self.clients)

    def _charge(self, user_id: int) -> None:
        ok, used = self.repo.check_and_increment_quota(user_id)
        if not ok:
            raise QuotaExceededError(f"Daily free limit reached ({used} turns). Fresh quota unlocks tomorrow UTC.")

    PRIMARY_FLASH_MODELS = ("gemini-3.8-flash", "gemini-3.5-flash-lite")

    def _apply_thinking_policy(
        self,
        base_cfg: types.GenerateContentConfig,
        model_name: str,
        want_thinking: bool,
    ) -> types.GenerateContentConfig:
        no_think = getattr(self, "_no_thinking_models", set())
        if model_name in no_think or not hasattr(types, "ThinkingConfig"):
            return base_cfg
        fields = getattr(types.ThinkingConfig, "model_fields", {})
        try:
            tc = None
            if model_name.startswith("gemini-3") and "thinking_level" in fields:
                lvl = "LOW" if want_thinking else "MINIMAL"
                if hasattr(types, "ThinkingLevel"):
                    lvl = getattr(types.ThinkingLevel, lvl, lvl)
                tc = types.ThinkingConfig(thinking_level=lvl)
            elif not want_thinking and "thinking_budget" in fields:
                tc = types.ThinkingConfig(thinking_budget=0)
            if tc is not None and hasattr(base_cfg, "model_copy"):
                return base_cfg.model_copy(update={"thinking_config": tc})
        except Exception:
            pass
        return base_cfg

    def _strip_thinking(self, cfg: types.GenerateContentConfig) -> types.GenerateContentConfig:
        if hasattr(cfg, "model_copy"):
            return cfg.model_copy(update={"thinking_config": None})
        return cfg

    def _candidate_models(self, prefer_deep_chat: bool = False) -> list[str]:
        dead = getattr(self, "_dead_models", set())
        lead_attr = "deep_model" if prefer_deep_chat else "flash_model"
        raw_chain: list[str] = []
        lead_val = getattr(self, lead_attr, "")
        if isinstance(lead_val, str) and lead_val.startswith("gemini-3"):
            raw_chain.append(lead_val)
        for m in ("gemini-3.8-flash", "gemini-3.5-flash-lite"):
            if m not in raw_chain:
                raw_chain.append(m)
        for attr in ("flash_model", "deep_model", "lite_model"):
            val = getattr(self, attr, "")
            if isinstance(val, str) and val.startswith("gemini-3") and val not in raw_chain:
                raw_chain.append(val)
        active = [m for m in raw_chain if m not in dead]
        return active or ["gemini-3.8-flash", "gemini-3.5-flash-lite"]

    async def _invoke_sdk(
        self, client_obj: Any, model: str, contents: Any, cfg: types.GenerateContentConfig
    ) -> str:
        c = client_obj[1] if isinstance(client_obj, tuple) else client_obj
        if hasattr(c, "aio") and hasattr(c.aio, "models"):
            resp = await c.aio.models.generate_content(
                model=model, contents=contents, config=cfg
            )
        else:
            resp = await asyncio.to_thread(
                c.models.generate_content, model=model, contents=contents, config=cfg
            )
        txt = getattr(resp, "text", None)
        if txt:
            return txt
        cands = getattr(resp, "candidates", None) or []
        for cand in cands:
            content = getattr(cand, "content", None)
            for p in (getattr(content, "parts", None) or []):
                pt = getattr(p, "text", None)
                if pt:
                    return pt
        return ""

    async def _call(
        self,
        contents: Any,
        cfg: types.GenerateContentConfig,
        *,
        prefer_deep_chat: bool = False,
        thinking: bool = False,
        timeout_s: float = 14.0,
    ) -> str:
        if not self.clients:
            raise LLMUnavailableError("No Gemini API keys configured.")
        if not hasattr(self, "_dead_models"):
            self._dead_models: set[str] = set()
        if not hasattr(self, "_no_thinking_models"):
            self._no_thinking_models: set[str] = set()

        n_keys = len(self.clients)
        start_k = getattr(self, "_rr_idx", 0) % n_keys
        self._rr_idx = (start_k + 1) % n_keys
        last_err: Exception | None = None

        for model in self._candidate_models(prefer_deep_chat=prefer_deep_chat):
            eff_cfg = self._apply_thinking_policy(cfg, model, want_thinking=thinking)
            for offset in range(n_keys):
                k_idx = (start_k + offset) % n_keys
                client_obj = self.clients[k_idx]
                try:
                    out = await asyncio.wait_for(
                        self._invoke_sdk(client_obj, model, contents, eff_cfg),
                        timeout=timeout_s,
                    )
                    if out and out.strip():
                        return out
                except asyncio.TimeoutError as tex:
                    last_err = tex
                    logger.warning("Gemini timeout on %s (key #%d, %.1fs)", model, k_idx, timeout_s)
                    # Do not stall 4x on the same slow model—try at most 1 more key then failover to lite
                    if offset >= 1:
                        break
                except Exception as exc:
                    last_err = exc
                    emsg = _redact(str(exc))
                    up = emsg.upper()
                    if "404" in up or "NOT_FOUND" in up or "NO LONGER AVAILABLE" in up:
                        self._dead_models.add(model)
                        logger.warning("Blacklisted retired model %s immediately: %s", model, emsg[:140])
                        break
                    if ("400" in up or "INVALID_ARGUMENT" in up) and getattr(eff_cfg, "thinking_config", None) is not None:
                        self._no_thinking_models.add(model)
                        eff_cfg = self._strip_thinking(eff_cfg)
                        try:
                            out2 = await asyncio.wait_for(
                                self._invoke_sdk(client_obj, model, contents, eff_cfg),
                                timeout=timeout_s,
                            )
                            if out2 and out2.strip():
                                return out2
                        except Exception as exc2:
                            last_err = exc2
                        continue
                    logger.warning("Gemini retry on %s (key #%d): %s", model, k_idx, emsg[:160])
                    continue

        raise LLMUnavailableError(f"All Gemini 3.x keys busy ({_redact(str(last_err))[:120]}).")

    async def generate_text(
        self,
        user_id: int,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.62,
        deep_chat: bool = True,
    ) -> str:
        if not self.clients:
            raise LLMUnavailableError("Gemini API keys are not configured.")
        self._charge(user_id)
        cfg = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
        )
        try:
            return await self._call(
                user_prompt,
                cfg,
                prefer_deep_chat=deep_chat,
                thinking=False,
                timeout_s=14.0,
            )
        except Exception:
            self.repo.refund_quota(user_id)
            raise

    async def generate_structured(
        self,
        user_id: int,
        system_prompt: str,
        user_prompt: str,
        schema_cls: Type[T],
        fallback: T,
        *,
        deep_chat: bool = False,
        temperature: float = 0.25,
    ) -> T:
        if not self.clients:
            return fallback
        self._charge(user_id)
        cfg = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=schema_cls,
            temperature=temperature,
        )
        try:
            raw = await self._call(
                user_prompt,
                cfg,
                prefer_deep_chat=deep_chat,
                thinking=False,
                timeout_s=14.0,
            )
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return schema_cls.model_validate_json(cleaned)
        except Exception as exc:
            logger.warning("Structured output fallback: %s", _redact(str(exc)))
            self.repo.refund_quota(user_id)
            return fallback

    async def generate_fast_multimodal(
        self,
        user_id: int,
        system_prompt: str,
        audio_bytes: bytes,
        user_prompt: str,
        *,
        mime_type: str = "audio/ogg",
        temperature: float = 0.25,
    ) -> str:
        if not self.clients:
            raise LLMUnavailableError("Gemini API keys are not configured.")
        self._charge(user_id)
        part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        cfg = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
        )
        try:
            return await self._call(
                [part, user_prompt],
                cfg,
                prefer_deep_chat=False,
                thinking=False,
                timeout_s=13.0,
            )
        except Exception as exc:
            self.repo.refund_quota(user_id)
            raise LLMUnavailableError(f"Voice analysis busy: {_redact(str(exc))}") from exc

    async def generate_voice_turn(
        self,
        user_id: int,
        system_prompt: str,
        audio_bytes: bytes,
        user_prompt: str,
        *,
        mime_type: str = "audio/ogg",
        temperature: float = 0.5,
    ) -> str:
        return await self.generate_fast_multimodal(
            user_id, system_prompt, audio_bytes, user_prompt,
            mime_type=mime_type, temperature=temperature,
        )

    async def audit_voice_prosody(
        self,
        user_id: int,
        system_prompt: str,
        audio_bytes: bytes,
        user_prompt: str,
        schema_cls: Type[T],
        fallback: T,
        *,
        mime_type: str = "audio/ogg",
    ) -> T:
        if not self.clients:
            return fallback
        self._charge(user_id)
        part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        cfg = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=schema_cls,
            temperature=0.15,
        )
        try:
            raw = await self._call(
                [part, user_prompt],
                cfg,
                prefer_deep_chat=False,
                thinking=False,
                timeout_s=14.0,
            )
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return schema_cls.model_validate_json(cleaned)
        except Exception as exc:
            logger.warning("Structured audio fallback: %s", _redact(str(exc)))
            self.repo.refund_quota(user_id)
            return fallback

    async def transcribe_voice(
        self, user_id: int, audio_bytes: bytes, *, mime_type: str = "audio/ogg"
    ) -> str:
        raw = await self.generate_fast_multimodal(
            user_id,
            "Transcribe the exact English words spoken. Write [silence] if empty.",
            audio_bytes,
            "Transcribe verbatim:",
            mime_type=mime_type,
            temperature=0.1,
        )
        return raw.strip()
