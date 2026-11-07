import asyncio, os, logging
from typing import Type, TypeVar
from google import genai
from google.genai import types
from pydantic import BaseModel
from src.config import FALLBACK_GEMINI_MODEL, PRIMARY_GEMINI_MODEL, resolve_gemini_keys
from src.db.profile_repo import ProfileRepository

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class QuotaExceededError(RuntimeError):
    """Raised when the user's daily free-tier budget is exhausted."""


class GeminiService:
    def __init__(self, repo: ProfileRepository, api_key: str | None = None) -> None:
        self.repo = repo
        keys = resolve_gemini_keys()
        resolved_key = api_key or (keys[0] if keys else "")
        self.client = genai.Client(api_key=resolved_key) if resolved_key else None

    async def generate_text(
        self, user_id: int, system_prompt: str, user_prompt: str, temperature: float = 0.7
    ) -> str:
        allowed, used = self.repo.check_and_increment_quota(user_id)
        if not allowed:
            raise QuotaExceededError(f"Daily free quota reached ({used} calls). Try again tomorrow.")
        if not self.client:
            raise RuntimeError("GEMINI_API_KEY is not configured.")

        for model_name in (PRIMARY_GEMINI_MODEL, FALLBACK_GEMINI_MODEL):
            for attempt in range(2):
                try:
                    resp = await asyncio.to_thread(
                        self.client.models.generate_content,
                        model=model_name,
                        contents=user_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            temperature=temperature,
                        ),
                    )
                    if resp and resp.text:
                        return resp.text.strip()
                except Exception as exc:
                    logger.warning("Gemini call failed on %s (attempt %d): %s", model_name, attempt + 1, exc)
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError("All Gemini models failed after retries.")

    async def generate_structured(
        self,
        user_id: int,
        system_prompt: str,
        user_prompt: str,
        schema_cls: Type[T],
        fallback_instance: T,
    ) -> T:
        allowed, _ = self.repo.check_and_increment_quota(user_id)
        if not allowed:
            raise QuotaExceededError("Daily free quota reached.")
        if not self.client:
            return fallback_instance

        for attempt in range(2):
            try:
                resp = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=PRIMARY_GEMINI_MODEL if attempt == 0 else FALLBACK_GEMINI_MODEL,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        response_schema=schema_cls,
                        temperature=0.2,
                    ),
                )
                if resp and resp.text:
                    return schema_cls.model_validate_json(resp.text)
            except Exception as exc:
                logger.warning("Structured JSON validation/call failed (attempt %d): %s", attempt + 1, exc)
                await asyncio.sleep(1.0)
        return fallback_instance

    async def transcribe_voice(self, user_id: int, audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
        allowed, used = self.repo.check_and_increment_quota(user_id)
        if not allowed:
            raise QuotaExceededError(f"Daily free quota reached ({used} calls).")
        if not self.client:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        prompt = (
            "Transcribe this spoken English audio verbatim, preserving natural contractions, "
            "hesitations, and any grammar or preposition slips without auto-correcting them."
        )
        for model_name in (PRIMARY_GEMINI_MODEL, FALLBACK_GEMINI_MODEL):
            try:
                part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
                resp = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=model_name,
                    contents=[part, prompt],
                )
                if resp and resp.text:
                    return resp.text.strip()
            except Exception as exc:
                logger.warning("Voice transcription failed on %s: %s", model_name, exc)
        raise RuntimeError("Voice transcription unavailable; fallback to text.")
