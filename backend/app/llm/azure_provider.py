"""Async Azure OpenAI provider — chat completions + embeddings.

Reuses the same Azure resource/creds proven in tests/.env. The chat
deployment is ``AZURE_OPENAI_DEPLOYMENT``; embeddings use
``AZURE_OPENAI_EMBEDDING_DEPLOYMENT`` (text-embedding-3-large).
"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.base import Completion, Message

log = get_logger(__name__)


class AzureOpenAIProvider:
    def __init__(self) -> None:
        self._client = None

    def _async_client(self):
        if self._client is None:
            from openai import AsyncAzureOpenAI

            s = get_settings()
            self._client = AsyncAzureOpenAI(
                api_key=s.azure_openai_key,
                api_version=s.azure_openai_api_version,
                azure_endpoint=s.azure_openai_endpoint,
            )
        return self._client

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1500,
        json_mode: bool = False,
    ) -> Completion:
        s = get_settings()
        client = self._async_client()
        kwargs = dict(
            model=s.azure_openai_deployment,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = await client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        return Completion(
            text=text.strip(),
            model=s.azure_openai_deployment or "",
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            raw=resp,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        s = get_settings()
        if not s.azure_embeddings_configured():
            raise RuntimeError(
                "Azure embeddings not configured "
                "(set AZURE_OPENAI_EMBEDDING_DEPLOYMENT)."
            )
        client = self._async_client()
        resp = await client.embeddings.create(
            model=s.azure_openai_embedding_deployment, input=texts
        )
        return [d.embedding for d in resp.data]
