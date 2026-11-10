import asyncio
import logging
import time
from typing import Any, Type, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from src.config import gemini_model_chain, resolve_gemini_keys
from src.db.profile_repo import ProfileRepository

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

MAX_ATTEMPTS_PER_CALL = 6  # minimum cap; raised automatically to len(keys) x len(models)
DEAD_PAIR_TTL_S = 1800  # a (key, model) pair that answered 404 is skipped for 30 minutes
RATE_MINUTE_COOLDOWN_S = 65  # per-minute 429: that pair is skipped for just over a minute
RATE_DAY_COOLDOWN_S = 3600  # per-day 429: skip the pair for an hour (re-checked hourly, no hammering)
CALL_TIMEOUT_S = 40.0  # one generate_content attempt (text); a hung socket must never freeze the chat
AUDIO_TIMEOUT_S = 75.0  # voice transcription attempt
HTTP_TIMEOUT_MS = 60_000  # SDK-level timeout, a second safety net below the asyncio one


class QuotaExceededError(RuntimeError):
    """Raised when the user's own daily free-tier budget is exhausted."""


class LLMUnavailableError(RuntimeError):
    """Raised when every configured Gemini key/model failed. The message is safe to show to learners."""

    def __init__(self, reason: str = "") -> None:
        super().__init__(
            "I'm having trouble reaching my language brain right now (shared free-tier limit or a short outage). "
            "Please try again in a minute."
        )
        self.reason = reason


def _classify(exc: Exception) -> str:
    """Return 'rate' | 'model' | 'auth' | 'transient' | 'other' for a failed Gemini call."""
    text = f"{type(exc).__name__} {exc}".lower()
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429 or "resource_exhausted" in text or "quota" in text or "rate limit" in text:
        return "rate"
    if code == 404 or "not_found" in text or "is not found" in text or "no longer available" in text:
        return "model"
    if code in (401, 403) or "api key" in text or "api_key" in text or "permission_denied" in text:
        return "auth"
    if code in (500, 502, 503, 504) or "unavailable" in text or "overloaded" in text or "timeout" in text:
        return "transient"
    return "other"


def _thinking_config_for(model: str):
    """Per-model 'think less, answer fast' config. Returns None when the model family is unknown.

    * gemini-2.5-flash / 2.5-flash-lite accept thinking_budget=0 (thinking off).
    * gemini-3.x models use thinking_level; 'low' is accepted across the 3.x line.
    * Anything else gets no thinking config at all (an unsupported field is a 400, not a speed-up).
    VERIFY: names/levels against https://ai.google.dev/gemini-api/docs/thinking when Google changes models.
    """
    if not hasattr(types, "ThinkingConfig"):
        return None
    name = model.lower()
    try:
        if name.startswith("gemini-2.5-flash"):
            return types.ThinkingConfig(thinking_budget=0)
        if name.startswith("gemini-3"):
            return types.ThinkingConfig(thinking_level="low")
    except Exception:  # noqa: BLE001 - older SDK without the field
        return None
    return None


def _rate_cooldown(exc: Exception) -> float:
    """429 text mentions PerDay or PerMinute quota ids; day-limits cool down much longer."""
    text = str(exc).lower()
    if "perday" in text or "per day" in text or "daily" in text:
        return RATE_DAY_COOLDOWN_S
    return RATE_MINUTE_COOLDOWN_S


def _is_thinking_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "thinking" in text and ("400" in text or "invalid" in text or "not support" in text or "unsupported" in text)


class GeminiService:
    def __init__(self, repo: ProfileRepository, api_key: str | None = None) -> None:
        self.repo = repo
        keys = [api_key] if api_key else resolve_gemini_keys()
        self.clients = [self._make_client(k) for k in keys if k]
        self.client = self.clients[0] if self.clients else None  # backwards-compatible alias
        self._dead_pairs: dict[tuple[int, str], float] = {}
        self._cooldown_until: dict[tuple[int, str], float] = {}
        self._no_thinking: set[tuple[int, str]] = set()
        self._cursor = 0  # round-robin start so friends' traffic spreads over all keys

    @staticmethod
    def _make_client(key: str):
        try:
            return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=HTTP_TIMEOUT_MS))
        except Exception:  # noqa: BLE001 - older SDK: fall back to the default client
            return genai.Client(api_key=key)

    @property
    def configured(self) -> bool:
        return bool(self.clients)

    def _refund(self, user_id: int) -> None:
        try:
            self.repo.refund_quota(user_id)
        except Exception:  # noqa: BLE001 - refund is best-effort
            logger.exception("quota refund failed")

    async def _call(
        self,
        contents: Any,
        config: types.GenerateContentConfig | None = None,
        *,
        thinking: bool = False,
        timeout_s: float | None = None,
    ) -> str:
        """Try every (key, model) pair with failover. Never raises anything but LLMUnavailableError.

        thinking=True adds a model-appropriate ThinkingConfig (see _thinking_config_for); on a 400 that mentions
        'thinking' the same pair is retried once without it, so one wrong model/field combination cannot burn
        the whole chain.
        """
        if not self.clients:
            raise LLMUnavailableError("no GEMINI_API_KEY configured")
        timeout_s = CALL_TIMEOUT_S if timeout_s is None else timeout_s
        models = gemini_model_chain()
        start = self._cursor % len(self.clients)
        self._cursor += 1
        dead_keys: set[int] = set()
        attempts = 0
        max_attempts = max(MAX_ATTEMPTS_PER_CALL, len(self.clients) * len(models))
        last_reason = ""

        for model_name in models:
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
                        cfg = config
                        if use_thinking:
                            tc = _thinking_config_for(model_name)
                            if tc is not None and config is not None:
                                cfg = config.model_copy(update={"thinking_config": tc}) if hasattr(
                                    config, "model_copy"
                                ) else config
                        kwargs: dict[str, Any] = {"model": model_name, "contents": contents}
                        if cfg is not None:
                            kwargs["config"] = cfg
                        resp = await asyncio.wait_for(
                            asyncio.to_thread(self.clients[idx].models.generate_content, **kwargs),
                            timeout=timeout_s,
                        )
                        text = getattr(resp, "text", None)
                        if text:
                            return text.strip()
                        last_reason = f"empty response from {model_name}"
                        logger.warning("Gemini returned an empty response (%s, key #%d)", model_name, idx)
                        break
                    except asyncio.TimeoutError:
                        last_reason = f"timeout after {timeout_s:.0f}s on {model_name}"
                        logger.warning("Gemini timeout on %s (key #%d)", model_name, idx)
                        break  # next key/model immediately: the user is already waiting
                    except Exception as exc:  # noqa: BLE001 - classify and fail over
                        kind = _classify(exc)
                        last_reason = f"{kind}: {exc}"
                        logger.warning("Gemini %s failure on %s (key #%d): %s", kind, model_name, idx, exc)
                        if use_thinking and _pass == 0 and _is_thinking_error(exc):
                            self._no_thinking.add((idx, model_name))
                            use_thinking = False
                            continue  # same pair, once, without thinking config
                        if kind == "model":
                            # "no longer available to new users" can differ per key/project: block only this pair
                            self._dead_pairs[(idx, model_name)] = time.monotonic()
                        elif kind == "auth":
                            dead_keys.add(idx)
                        elif kind == "rate":
                            self._cooldown_until[(idx, model_name)] = time.monotonic() + _rate_cooldown(exc)
                        elif kind == "transient":
                            await asyncio.sleep(0.8)
                        break
        raise LLMUnavailableError(last_reason)

    def _charge(self, user_id: int) -> None:
        allowed, used = self.repo.check_and_increment_quota(user_id)
        if not allowed:
            raise QuotaExceededError(f"Daily free quota reached ({used} calls). Try again tomorrow.")

    async def generate_text(
        self, user_id: int, system_prompt: str, user_prompt: str, temperature: float = 0.7
    ) -> str:
        self._charge(user_id)
        cfg = types.GenerateContentConfig(system_instruction=system_prompt, temperature=temperature)
        try:
            return await self._call(user_prompt, cfg, thinking=True)
        except LLMUnavailableError:
            self._refund(user_id)  # a failed call must not eat the learner's daily budget
            raise

    async def generate_structured(
        self,
        user_id: int,
        system_prompt: str,
        user_prompt: str,
        schema_cls: Type[T],
        fallback_instance: T,
    ) -> T:
        self._charge(user_id)
        if not self.clients:
            self._refund(user_id)
            return fallback_instance
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=schema_cls,
            temperature=0.2,
        )
        for _ in range(2):  # second pass covers schema-validation failures
            try:
                raw = await self._call(user_prompt, config, thinking=True)
                return schema_cls.model_validate_json(raw)
            except LLMUnavailableError as exc:
                logger.warning("Structured call unavailable, using engine fallback: %s", exc.reason)
                self._refund(user_id)
                return fallback_instance
            except Exception as exc:  # noqa: BLE001 - invalid JSON / schema mismatch
                logger.warning("Structured output failed validation: %s", exc)
        self._refund(user_id)
        return fallback_instance

    async def transcribe_voice(self, user_id: int, audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
        self._charge(user_id)
        prompt = (
            "Transcribe this spoken English audio verbatim, preserving natural contractions, "
            "hesitations, and any grammar or preposition slips without auto-correcting them. "
            "Return only the transcript."
        )
        part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        try:
            return await self._call([part, prompt], timeout_s=AUDIO_TIMEOUT_S)
        except LLMUnavailableError:
            self._refund(user_id)
            raise
