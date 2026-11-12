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
AUDIO_TIMEOUT_S = 38.0
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
        if low.startswith("gemini-2.5-flash"):
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

    @staticmethod
    def _build_sdk_client(key: str):
        try:
            return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=HTTP_TIMEOUT_MS))
        except Exception:
            return genai.Client(api_key=key)

    @property
    def configured(self) -> bool:
        return bool(self.clients)

    @staticmethod
    async def _invoke(client_obj: Any, kwargs: dict[str, Any], timeout_s: float) -> Any:
        aio = getattr(client_obj, "aio", None)
        aio_models = getattr(aio, "models", None) if aio is not None else None
        fn = getattr(aio_models, "generate_content", None) if aio_models is not None else None
        coro = fn(**kwargs) if callable(fn) else asyncio.to_thread(client_obj.models.generate_content, **kwargs)
        return await asyncio.wait_for(coro, timeout=timeout_s)

    async def _call(
        self,
        contents: Any,
        config: types.GenerateContentConfig | None = None,
        *,
        prefer_deep_chat: bool = True,
        thinking: bool = True,
        timeout_s: float = CALL_TIMEOUT_S,
    ) -> str:
        if not self.clients:
            raise LLMUnavailableError("no_keys")
        models = gemini_model_chain(prefer_deep_chat=prefer_deep_chat)
        start = self._cursor % len(self.clients)
        self._cursor += 1
        dead_keys: set[int] = set()
        overload_threshold = min(2, len(self.clients))
        max_attempts = max(6, len(self.clients) * len(models))
        attempts = 0
        last_reason = ""

        now0 = time.monotonic()
        any_uncooled = any(now0 >= self._model_cooldown_until.get(m, 0.0) for m in models)

        for model_name in models:
            if any_uncooled and time.monotonic() < self._model_cooldown_until.get(model_name, 0.0):
                continue
            consec_overload = 0
            for offset in range(len(self.clients)):
                idx = (start + offset) % len(self.clients)
                if idx in dead_keys or attempts >= max_attempts:
                    continue
                if time.monotonic() < self._cooldown_until.get((idx, model_name), 0.0):
                    continue
                if time.monotonic() - self._dead_pairs.get((idx, model_name), -DEAD_PAIR_TTL_S) < DEAD_PAIR_TTL_S:
                    continue
                attempts += 1
                use_thinking = thinking and (idx, model_name) not in self._no_thinking
                for _pass in (0, 1):
                    try:
                        cfg = _with_http_timeout(config, timeout_s)
                        if use_thinking:
                            tc = _thinking_cfg(model_name)
                            if tc is not None and cfg is not None and hasattr(cfg, "model_copy"):
                                cfg = cfg.model_copy(update={"thinking_config": tc})
                        kwargs: dict[str, Any] = {"model": model_name, "contents": contents}
                        if cfg is not None:
                            kwargs["config"] = cfg
                        resp = await self._invoke(self.clients[idx], kwargs, timeout_s)
                        txt = getattr(resp, "text", None)
                        if txt and txt.strip():
                            return txt.strip()
                        last_reason = f"empty:{model_name}"
                        break
                    except asyncio.TimeoutError:
                        last_reason = f"timeout:{model_name}"
                        consec_overload += 1
                        self._cooldown_until[(idx, model_name)] = time.monotonic() + TRANSIENT_COOLDOWN_S
                        break
                    except Exception as exc:
                        kind = _classify(exc)
                        last_reason = f"{kind}:{_redact(str(exc))}"
                        logger.warning("Gemini %s on %s (key #%d): %s", kind, model_name, idx, _redact(str(exc)))
                        if use_thinking and _pass == 0 and _is_thinking_err(exc):
                            self._no_thinking.add((idx, model_name))
                            use_thinking = False
                            continue
                        if kind == "model":
                            self._dead_pairs[(idx, model_name)] = time.monotonic()
                        elif kind == "auth":
                            dead_keys.add(idx)
                        elif kind == "rate":
                            cd = RATE_DAY_COOLDOWN_S if "day" in str(exc).lower() else RATE_MIN_COOLDOWN_S
                            self._cooldown_until[(idx, model_name)] = time.monotonic() + cd
                        elif kind == "transient":
                            consec_overload += 1
                            self._cooldown_until[(idx, model_name)] = time.monotonic() + TRANSIENT_COOLDOWN_S
                            await asyncio.sleep(0.25)
                        break
                if consec_overload >= overload_threshold:
                    self._model_cooldown_until[model_name] = time.monotonic() + OVERLOAD_MODEL_COOLDOWN_S
                    break
        raise LLMUnavailableError(last_reason)

    def _charge(self, user_id: int) -> None:
        ok, used = self.repo.check_and_increment_quota(user_id)
        if not ok:
            raise QuotaExceededError(f"Daily free limit reached ({used} turns). Fresh quota unlocks tomorrow UTC.")

    async def generate_text(
        self,
        user_id: int,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.9,
        deep_chat: bool = True,
    ) -> str:
        self._charge(user_id)
        cfg = types.GenerateContentConfig(system_instruction=system_prompt, temperature=temperature)
        try:
            return await self._call(user_prompt, cfg, prefer_deep_chat=deep_chat, thinking=True)
        except LLMUnavailableError:
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
        for _ in range(2):
            try:
                raw = await self._call(
                    user_prompt, cfg, prefer_deep_chat=deep_chat, thinking=True, timeout_s=STRUCTURED_TIMEOUT_S
                )
                return schema_cls.model_validate_json(raw)
            except LLMUnavailableError:
                self.repo.refund_quota(user_id)
                return fallback
            except Exception as exc:
                logger.warning("Structured parse warning: %s", _redact(str(exc)))
        self.repo.refund_quota(user_id)
        return fallback

    async def generate_voice_turn(
        self,
        user_id: int,
        system_prompt: str,
        audio_bytes: bytes,
        user_prompt: str,
        *,
        mime_type: str = "audio/ogg",
        temperature: float = 0.82,
    ) -> str:
        self._charge(user_id)
        part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        cfg = types.GenerateContentConfig(system_instruction=system_prompt, temperature=temperature)
        try:
            return await self._call(
                [part, user_prompt], cfg, prefer_deep_chat=True, thinking=True, timeout_s=AUDIO_TIMEOUT_S
            )
        except LLMUnavailableError:
            self.repo.refund_quota(user_id)
            raise

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
        base_kw: dict[str, Any] = {
            "system_instruction": system_prompt,
            "response_mime_type": "application/json",
            "response_schema": schema_cls,
            "temperature": 0.15,
        }
        configs: list[tuple[types.GenerateContentConfig, bool]] = []
        if hasattr(types, "ThinkingConfig"):
            try:
                tk_kw = dict(base_kw)
                tk_kw["thinking_config"] = types.ThinkingConfig(thinking_budget=512)
                configs.append((types.GenerateContentConfig(**tk_kw), False))
            except Exception:
                pass
        configs.append((types.GenerateContentConfig(**base_kw), True))

        last_err: Exception | None = None
        for cfg, fast_think in configs:
            try:
                raw = await self._call(
                    [part, user_prompt],
                    cfg,
                    prefer_deep_chat=True,
                    thinking=fast_think,
                    timeout_s=AUDIO_TIMEOUT_S,
                )
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                return schema_cls.model_validate_json(cleaned)
            except Exception as exc:
                last_err = exc
                logger.warning("Prosody audit attempt retry: %s", _redact(str(exc)))

        logger.warning("Prosody audit exhausted retries: %s", _redact(str(last_err)))
        self.repo.refund_quota(user_id)
        return fallback

    async def transcribe_voice(self, user_id: int, audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
        self._charge(user_id)
        prompt = (
            "Transcribe this spoken audio verbatim. Preserve natural contractions, hesitations, "
            "any grammar or preposition slips, and any Arabic or French words spoken mid-sentence "
            "exactly as uttered—never auto-correct or polish. Return ONLY the transcript."
        )
        part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        try:
            return await self._call(
                [part, prompt], prefer_deep_chat=False, thinking=True, timeout_s=AUDIO_TIMEOUT_S
            )
        except LLMUnavailableError:
            self.repo.refund_quota(user_id)
            raise
