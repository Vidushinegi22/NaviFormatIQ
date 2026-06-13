"""get_llm() — singleton async provider (Azure)."""
from __future__ import annotations

from functools import lru_cache

from app.llm.azure_provider import AzureOpenAIProvider
from app.llm.base import LLMProvider


@lru_cache(maxsize=1)
def get_llm() -> LLMProvider:
    return AzureOpenAIProvider()
