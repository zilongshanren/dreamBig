"""OpenAI embedding client (separate from Poe — Poe lacks embedding models).

Used to generate pgvector embeddings for the Game similarity search feature.
Uses OpenAI's text-embedding-3-small model (1536 dims, very cheap: $0.02/1M tokens).
"""

from __future__ import annotations

import logging
import os

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-3-small"  # 1536 dims, cheap
DIM = 1536
MAX_INPUT_CHARS = 8000  # safe trim to stay under token limits


class EmbeddingClient:
    """Thin async wrapper around OpenAI's /embeddings endpoint."""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("OPENAI_API_KEY not configured")
            self._client = AsyncOpenAI(api_key=self.api_key)
        return self._client

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string, returns a list of floats (length 1536)."""
        client = self._get_client()
        resp = await client.embeddings.create(
            model=self.model,
            input=text[:MAX_INPUT_CHARS],
        )
        return resp.data[0].embedding

    async def embed_batch(
        self, texts: list[str], batch_size: int = 100
    ) -> list[list[float]]:
        """Batch embed, chunks input to stay under API limits.

        OpenAI accepts up to 2048 inputs per call, but we keep batches small
        for safety and to log progress.
        """
        results: list[list[float]] = []
        if not texts:
            return results

        client = self._get_client()
        for i in range(0, len(texts), batch_size):
            chunk = [t[:MAX_INPUT_CHARS] for t in texts[i : i + batch_size]]
            resp = await client.embeddings.create(model=self.model, input=chunk)
            results.extend(item.embedding for item in resp.data)
            logger.info(
                f"Embedded batch {i // batch_size + 1}: {len(chunk)} texts"
            )
        return results

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
