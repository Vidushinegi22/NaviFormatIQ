"""
Azure OpenAI client wrapper with a deterministic offline fallback.

All LLM calls in this project go through ``chat_json()``. If Azure OpenAI is
not configured, the fallback returns ``None`` so callers can fall back to
deterministic heuristics — the pipeline must still run end-to-end without
network access (e.g. in CI / hackathon demo without keys).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from config import settings


def llm_available() -> bool:
    return settings.azure_openai_configured()


def chat_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 1500,
) -> Optional[dict[str, Any]]:
    """Send a chat completion and return parsed JSON, or None if unavailable.

    The model is instructed to respond with a single JSON object via the
    ``response_format={"type": "json_object"}`` flag.
    """
    if not llm_available():
        return None

    try:
        from openai import AzureOpenAI
    except ImportError:
        return None

    client = AzureOpenAI(
        api_key=settings.azure_openai_key,
        api_version=settings.azure_openai_api_version,
        azure_endpoint=settings.azure_openai_endpoint,
    )

    try:
        resp = client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        return json.loads(raw)
    except Exception:
        return None


def chat_text(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 1500,
) -> Optional[str]:
    """Send a chat completion and return raw text, or None if unavailable."""
    if not llm_available():
        return None

    try:
        from openai import AzureOpenAI
    except ImportError:
        return None

    client = AzureOpenAI(
        api_key=settings.azure_openai_key,
        api_version=settings.azure_openai_api_version,
        azure_endpoint=settings.azure_openai_endpoint,
    )

    try:
        resp = client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return None
