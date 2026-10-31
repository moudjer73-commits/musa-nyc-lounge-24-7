"""LLM access behind one small interface, so the pipeline never imports a vendor SDK.

* `LLMClient`   : the protocol the pipeline depends on.
* `GeminiClient`: google-genai implementation (JSON mode). NOT exercised against the live API in
                  this repository's tests: it is a thin adapter, check it with your own key first.
* `ScriptedLLM` : deterministic fake used by the tests.

Every call names a `role` ("analyst", "responder", "card", "debrief", "scene", or
"<mode>.<section>"). Roles route scripted answers in tests and let you pick a different model
or temperature per role in production.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_TEMPERATURES = {
    "responder": 0.7,   # lively characters; a starting value, tune against real conversations
    "analyst": 0.2,     # diagnosis must be consistent
    "card": 0.4,
    "debrief": 0.4,
    "scene": 0.9,
}


class LLMError(RuntimeError):
    """The model call failed, timed out, or returned something that never validated."""


class LLMClient(Protocol):
    async def generate_json(
        self, *, role: str, system: str, user: str, schema: type[T], temperature: float, timeout: float = 30.0
    ) -> T: ...


def extract_json(text: str) -> str:
    """Pull the JSON object out of a model reply that may be wrapped in code fences or prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


class GeminiClient:
    """google-genai adapter. `model` is required on purpose: model names change faster than this file."""

    def __init__(self, model: str, api_key: str | None = None, *, retries: int = 1) -> None:
        from google import genai  # imported lazily so the rest of the package needs no SDK
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key) if api_key else genai.Client()
        self.model = model
        self.retries = retries

    async def generate_json(
        self, *, role: str, system: str, user: str, schema: type[T], temperature: float, timeout: float = 30.0
    ) -> T:
        config = self._types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json",
        )
        prompt = user
        last: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                resp = await asyncio.wait_for(
                    self._client.aio.models.generate_content(model=self.model, contents=prompt, config=config),
                    timeout,
                )
                return schema.model_validate_json(extract_json(resp.text or ""))
            except ValidationError as e:
                last = e
                prompt = (
                    f"{user}\n\nYour previous reply did not match the required JSON schema "
                    f"({e.error_count()} problems). Return corrected JSON only."
                )
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001 - vendor SDK raises many types
                last = e
        raise LLMError(f"{role}: {last!r}")


@dataclass
class Call:
    role: str
    system: str
    user: str
    schema: str


@dataclass
class ScriptedLLM:
    """Deterministic stand-in. `scripts[role]` is a list consumed in order; each item is a dict /
    BaseModel / JSON string, an Exception to raise, or a callable(call) returning one of those."""

    scripts: dict[str, list[Any]] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)

    def push(self, role: str, *items: Any) -> None:
        self.scripts.setdefault(role, []).extend(items)

    def calls_for(self, role: str) -> list[Call]:
        return [c for c in self.calls if c.role == role]

    async def generate_json(
        self, *, role: str, system: str, user: str, schema: type[T], temperature: float, timeout: float = 30.0
    ) -> T:
        call = Call(role, system, user, schema.__name__)
        self.calls.append(call)
        queue = self.scripts.get(role)
        if not queue:
            raise LLMError(f"no scripted answer left for role {role!r}")
        item = queue.pop(0)
        if callable(item) and not isinstance(item, (BaseModel, dict, str)):
            item = item(call)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, BaseModel):
            item = item.model_dump()
        if isinstance(item, str):
            item = json.loads(extract_json(item))
        try:
            return schema.model_validate(item)
        except ValidationError as e:
            raise LLMError(f"{role}: scripted answer does not validate: {e}") from e
