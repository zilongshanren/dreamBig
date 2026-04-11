"""OpenAI vision client — used for game asset analysis (GPT-4o-mini multimodal).

Poe API does not reliably expose vision. We use OpenAI directly (OPENAI_API_KEY env).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

DEFAULT_VISION_MODEL = "gpt-4o-mini"  # has vision, $0.15/1M input, $0.60/1M output
VISION_PRICE_IN = 0.15 / 1_000_000
VISION_PRICE_OUT = 0.60 / 1_000_000


@dataclass
class VisionUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class VisionClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_VISION_MODEL):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self._client: AsyncOpenAI | None = None
        self.usage = VisionUsage()

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("OPENAI_API_KEY not configured")
            self._client = AsyncOpenAI(api_key=self.api_key)
        return self._client

    async def analyze_image(
        self,
        image_url: str,
        system_prompt: str,
        user_prompt: str,
        response_format: dict | None = None,  # {"type": "json_object"}
        max_tokens: int = 800,
    ) -> dict:
        """Single image analysis. Returns {content: str, tokens_in: int, tokens_out: int}."""
        client = self._get_client()
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url, "detail": "low"},
                    },  # low detail = cheaper
                ],
            },
        ]

        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format

        resp = await client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        tokens_in = resp.usage.prompt_tokens if resp.usage else 0
        tokens_out = resp.usage.completion_tokens if resp.usage else 0

        self.usage.input_tokens += tokens_in
        self.usage.output_tokens += tokens_out
        self.usage.cost_usd += (
            tokens_in * VISION_PRICE_IN + tokens_out * VISION_PRICE_OUT
        )

        return {"content": content, "tokens_in": tokens_in, "tokens_out": tokens_out}

    async def analyze_images(
        self,
        image_urls: list[str],
        system_prompt: str,
        user_prompt: str,
        response_format: dict | None = None,  # {"type": "json_object"}
        max_tokens: int = 800,
        detail: str = "low",
    ) -> dict:
        """Multi-image analysis (e.g. a handful of trailer frames in one call).

        Each entry in ``image_urls`` may be a regular https URL or a
        ``data:image/jpeg;base64,...`` data URL — both are accepted by
        OpenAI's chat completions API. Returns the same shape as
        :meth:`analyze_image`.
        """
        if not image_urls:
            raise ValueError("analyze_images requires at least one image URL")

        client = self._get_client()
        content_parts: list[dict] = [{"type": "text", "text": user_prompt}]
        for url in image_urls:
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": url, "detail": detail},
                }
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_parts},
        ]

        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format

        resp = await client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        tokens_in = resp.usage.prompt_tokens if resp.usage else 0
        tokens_out = resp.usage.completion_tokens if resp.usage else 0

        self.usage.input_tokens += tokens_in
        self.usage.output_tokens += tokens_out
        self.usage.cost_usd += (
            tokens_in * VISION_PRICE_IN + tokens_out * VISION_PRICE_OUT
        )

        return {"content": content, "tokens_in": tokens_in, "tokens_out": tokens_out}

    async def close(self):
        if self._client:
            await self._client.close()
            self._client = None
