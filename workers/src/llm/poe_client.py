"""Async Poe API client — wraps AsyncOpenAI pointed at Poe's OpenAI-compatible endpoint.

Poe exposes an OpenAI-compatible chat completions API at
https://api.poe.com/v1, so we reuse the official openai SDK and only
customize the base_url + api_key. The wrapper adds retry, a simple
circuit breaker, cost tracking, and a structured-output helper that
parses responses into Pydantic models.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from src.llm.cost import CostTracker
from src.llm.retry import with_retry

logger = logging.getLogger(__name__)


POE_BASE_URL = "https://api.poe.com/v1"
DEFAULT_TIMEOUT = 120.0  # Opus can take a while for long reports
DEFAULT_MAX_TOKENS = 4096

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Raised when the LLM call cannot be completed or parsed."""


class LLMJSONParseError(LLMError):
    """Raised when the model returns text that can't be parsed as the target schema."""


class LLMCircuitOpenError(LLMError):
    """Raised when the circuit breaker is open and we refuse to make a call."""


@dataclass
class ChatResponse:
    """A single LLM chat completion result."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    finish_reason: str | None = None
    raw: Any = None  # underlying SDK response object, for debugging


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    open_until: datetime | None = None


class PoeClient:
    """Async Poe API client with retry, circuit breaker, and cost tracking.

    Usage:
        client = PoeClient()  # reads POE_API_KEY from env
        resp = await client.chat(
            messages=[{"role": "user", "content": "Hello"}],
            model="Claude-Haiku-4.5",
        )
        print(resp.content)
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = POE_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        cost_tracker: CostTracker | None = None,
        circuit_threshold: int = 5,
        circuit_pause_minutes: int = 5,
    ):
        self.api_key = api_key or os.getenv("POE_API_KEY", "").strip()
        if not self.api_key:
            raise LLMError(
                "POE_API_KEY not set — export it in the environment or pass api_key="
            )
        self.base_url = base_url
        self.timeout = timeout
        self.cost_tracker = cost_tracker or CostTracker()
        self._circuit_threshold = circuit_threshold
        self._circuit_pause = timedelta(minutes=circuit_pause_minutes)
        self._circuit = _CircuitState()
        self._client: Any = None  # AsyncOpenAI, imported lazily

    async def _get_client(self) -> Any:
        if self._client is None:
            # Imported lazily so the module can be imported even if
            # openai isn't installed yet (e.g., during initial checks).
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    def _is_circuit_open(self) -> bool:
        if self._circuit.open_until is None:
            return False
        if datetime.now() >= self._circuit.open_until:
            logger.info("PoeClient circuit breaker reset after cooldown.")
            self._circuit = _CircuitState()
            return False
        return True

    def _record_success(self) -> None:
        self._circuit.consecutive_failures = 0

    def _record_failure(self) -> None:
        self._circuit.consecutive_failures += 1
        if self._circuit.consecutive_failures >= self._circuit_threshold:
            self._circuit.open_until = datetime.now() + self._circuit_pause
            logger.warning(
                f"PoeClient circuit breaker opened after "
                f"{self._circuit.consecutive_failures} consecutive failures. "
                f"Pausing until {self._circuit.open_until.isoformat()}."
            )

    @with_retry(max_attempts=3, backoff_base=2.0)
    async def _call_api(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> ChatResponse:
        """Raw API call — wrapped with retry decorator."""
        client = await self._get_client()
        kwargs.setdefault("max_tokens", DEFAULT_MAX_TOKENS)

        completion = await client.chat.completions.create(
            model=model,
            messages=messages,
            **kwargs,
        )

        choice = completion.choices[0]
        content = choice.message.content or ""
        usage = completion.usage
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0

        self.cost_tracker.record(model, input_tokens, output_tokens)

        return ChatResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=choice.finish_reason,
            raw=completion,
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> ChatResponse:
        """Basic chat completion with retry + circuit breaker.

        Args:
            messages: OpenAI-format message list [{"role": "user", "content": "..."}].
            model: Poe bot name, e.g. "Claude-Haiku-4.5".
            **kwargs: forwarded to the underlying OpenAI client (temperature,
                max_tokens, etc.).
        """
        if self._is_circuit_open():
            raise LLMCircuitOpenError(
                "PoeClient circuit breaker is open; refusing to call Poe API."
            )

        try:
            resp = await self._call_api(messages, model, **kwargs)
            self._record_success()
            logger.debug(
                f"[{model}] chat ok — {resp.input_tokens}in/{resp.output_tokens}out tokens"
            )
            return resp
        except Exception as exc:
            self._record_failure()
            logger.error(f"[{model}] chat failed: {exc}")
            raise

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        model: str,
        schema: type[T],
        max_retries: int = 3,
        **kwargs: Any,
    ) -> T:
        """Chat completion with structured Pydantic output validation.

        If the model returns invalid JSON or the JSON fails schema validation,
        we retry up to `max_retries` times, each time appending the error
        and asking the model to correct its output.
        """
        # Nudge models that support JSON mode.
        kwargs.setdefault("response_format", {"type": "json_object"})

        working_messages = list(messages)
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            resp = await self.chat(working_messages, model, **kwargs)
            raw = resp.content.strip()

            try:
                data = _extract_json(raw)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                logger.warning(
                    f"[{model}] chat_json attempt {attempt}/{max_retries} "
                    f"failed schema validation: {exc}"
                )
                if attempt >= max_retries:
                    break

                # Append corrective turn.
                working_messages = list(messages) + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response failed validation with: {exc}. "
                            f"Respond again with ONLY valid JSON matching the requested schema. "
                            f"Do not include any prose, code fences, or explanations."
                        ),
                    },
                ]

        raise LLMJSONParseError(
            f"Failed to parse valid {schema.__name__} after {max_retries} attempts: {last_error}"
        )

    async def chat_batch(
        self,
        items: list[Any],
        prompt_fn: Callable[[Any], list[dict[str, str]]],
        model: str,
        concurrency: int = 5,
        **kwargs: Any,
    ) -> list[ChatResponse | BaseException]:
        """Run a batch of chat calls with bounded concurrency.

        Args:
            items: list of arbitrary inputs.
            prompt_fn: maps each input to a messages list.
            model: Poe bot name to call for every item.
            concurrency: maximum in-flight requests.

        Returns:
            One entry per input, in the same order. Failed calls return
            the raised exception rather than raising, so one bad item
            doesn't kill the whole batch.
        """
        sem = asyncio.Semaphore(concurrency)

        async def _one(item: Any) -> ChatResponse | BaseException:
            async with sem:
                try:
                    return await self.chat(prompt_fn(item), model, **kwargs)
                except Exception as exc:
                    return exc

        return await asyncio.gather(*(_one(i) for i in items))

    async def chat_json_batch(
        self,
        items: list[Any],
        prompt_fn: Callable[[Any], list[dict[str, str]]],
        model: str,
        schema: type[T],
        concurrency: int = 5,
        **kwargs: Any,
    ) -> list[T | BaseException]:
        """Batch variant of chat_json — returns parsed models or exceptions."""
        sem = asyncio.Semaphore(concurrency)

        async def _one(item: Any) -> T | BaseException:
            async with sem:
                try:
                    return await self.chat_json(
                        prompt_fn(item), model, schema, **kwargs
                    )
                except Exception as exc:
                    return exc

        return await asyncio.gather(*(_one(i) for i in items))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json(text: str) -> Any:
    """Best-effort JSON extraction — strips code fences if the model added them."""
    stripped = text.strip()
    if not stripped:
        raise json.JSONDecodeError("empty content", text, 0)

    # Direct parse first.
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Try to peel out a fenced code block.
    fence_match = _JSON_FENCE_RE.search(stripped)
    if fence_match:
        return json.loads(fence_match.group(1))

    # Last resort: find the first {...} or [...] span.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = stripped.find(open_ch)
        end = stripped.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            return json.loads(stripped[start : end + 1])

    raise json.JSONDecodeError("no JSON found in response", text, 0)
