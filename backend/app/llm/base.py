"""Provider-agnostic LLM protocol + payloads (Azure is the only impl for now)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str


@dataclass
class Completion:
    text: str
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    raw: Any = None


class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1500,
        json_mode: bool = False,
    ) -> Completion: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
