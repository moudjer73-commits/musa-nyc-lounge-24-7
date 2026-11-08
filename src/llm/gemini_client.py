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


class GeminiService:
    def __init__(self, repo: ProfileRepository, api_key: str | None = None) -> None:
        self.repo = repo
        keys = [api_key] if api_key else resolve_gemini_keys()
        self.clients = [genai.Client(api_key=k) for k in keys if k]
        self.client = self.clients[0] if self.clients else None  # backwards-compatible alias
        self._dead_pairs: dict[tuple[int, str], float] = {}
        self._cursor = 0  # round-robin start so friends' traffic spreads over all keys

    @property
    def configured(self) -> bool:
        return bool(self.clients)

    async def _call(self, contents: Any, config: types.GenerateContentConfig | None = None) -> str:
        """Try every (key, model) pair with failover. Never raises anything but LLMUnavailableError."""
        if not self.clients:
            raise LLMUnavailableError("no GEMINI_API_KEY configured")
        models = gemini_model_chain()
        start = self._cursor % len(self.clients)
        self._cursor += 1
        dead_keys: set[int] = set()
        attempts = 0
        max_attempts = max(MAX_ATTEMPTS_PER_CALL, len(self.clients) * len(models))
        now = time.monotonic()
        last_reason = ""

        for model_name in models:
            for offset in range(len(self.clients)):
                idx = (start + offset) % len(self.clients)
                if idx in dead_keys or attempts >= max_attempts:
                    continue
                if now - self._dead_pairs.get((idx, model_name), -DEAD_PAIR_TTL_S) < DEAD_PAIR_TTL_S:
                    continue
                attempts += 1
                try:
                    kwargs: dict[str, Any] = {"model": model_name, "contents": contents}
                    if config is not None:
                        kwargs["config"] = config
                    resp = await asyncio.to_thread(self.clients[idx].models.generate_content, **kwargs)
                    text = getattr(resp, "text", None)
                    if text:
                        return text.strip()
                    last_reason = f"empty response from {model_name}"
                    logger.warning("Gemini returned an empty response (%s, key #%d)", model_name, idx)
                except Exception as exc:  # noqa: BLE001 - classify and fail over
                    kind = _classify(exc)
                    last_reason = f"{kind}: {exc}"
                    logger.warning("Gemini %s failure on %s (key #%d): %s", kind, model_name, idx, exc)
                    if kind == "model":
                        # "no longer available to new users" can differ per key/project, so only this pair is blocked
                        self._dead_pairs[(idx, model_name)] = time.monotonic()
                    elif kind == "auth":
                        dead_keys.add(idx)
                    elif kind in ("transient", "other"):
                        await asyncio.sleep(0.8)
                    # 'rate' -> immediately try the next key / model
        raise LLMUnavailableError(last_reason)

    def _charge(self, user_id: int) -> None:
        allowed, used = self.repo.check_and_increment_quota(user_id)
        if not allowed:
            raise QuotaExceededError(f"Daily free quota reached ({used} calls). Try again tomorrow.")

    async def generate_text(
        self, user_id: int, system_prompt: str, user_prompt: str, temperature: float = 0.7
    ) -> str:
        self._charge(user_id)
        return await self._call(
            user_prompt,
            types.GenerateContentConfig(system_instruction=system_prompt, temperature=temperature),
        )

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
            return fallback_instance
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=schema_cls,
            temperature=0.2,
        )
        for _ in range(2):  # second pass covers schema-validation failures
            try:
                raw = await self._call(user_prompt, config)
                return schema_cls.model_validate_json(raw)
            except LLMUnavailableError as exc:
                logger.warning("Structured call unavailable, using engine fallback: %s", exc.reason)
                return fallback_instance
            except Exception as exc:  # noqa: BLE001 - invalid JSON / schema mismatch
                logger.warning("Structured output failed validation: %s", exc)
        return fallback_instance

    async def transcribe_voice(self, user_id: int, audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
        self._charge(user_id)
        prompt = (
            "Transcribe this spoken English audio verbatim, preserving natural contractions, "
            "hesitations, and any grammar or preposition slips without auto-correcting them. "
            "Return only the transcript."
        )
        part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        return await self._call([part, prompt])
