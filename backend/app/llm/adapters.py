"""Sync LLM shims for the ported services.

The ported sync modules (section_mapper, rewriter, doc_understanding) call
``chat_json``/``chat_text``/``llm_available`` exactly as in tests/llm_client.py.
This re-implements them against the synchronous AzureOpenAI client so the
ported code runs unchanged inside ``run_sync`` worker threads. Returns ``None``
when Azure is unavailable so callers fall back to deterministic heuristics.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from app.core.config import settings


def llm_available() -> bool:
    return settings.azure_openai_configured()


def _client():
    from openai import AzureOpenAI

    return AzureOpenAI(
        api_key=settings.azure_openai_key,
        api_version=settings.azure_openai_api_version,
        azure_endpoint=settings.azure_openai_endpoint,
    )


def chat_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 1500,
) -> Optional[dict[str, Any]]:
    if not llm_available():
        return None
    try:
        client = _client()
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
    if not llm_available():
        return None
    try:
        client = _client()
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
