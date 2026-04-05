"""Retry decorator for async LLM calls.

Catches rate limits, timeouts, and transient connection errors from the
openai SDK and underlying httpx layer. Exponential backoff with jitter
prevents thundering-herd retries from multiple concurrent workers.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


# Error classes we consider "transient" and worth retrying.
# openai may not be importable at module load time in some contexts
# (e.g., CI without the dep), so we resolve it lazily inside the decorator.
def _transient_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
        asyncio.TimeoutError,
    ]
    try:
        import openai

        errors.extend([
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.InternalServerError,
        ])
    except ImportError:
        logger.debug("openai not importable during retry setup; using httpx errors only")
    return tuple(errors)


def with_retry(
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    max_backoff: float = 60.0,
    jitter: float = 0.3,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorate an async function with exponential backoff retry.

    Args:
        max_attempts: total attempts including the first call.
        backoff_base: seconds for the first retry; doubles each attempt.
        max_backoff: cap on any single sleep interval.
        jitter: ±fraction of random jitter applied to each sleep.
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            transient = _transient_errors()
            last_exc: BaseException | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except transient as exc:
                    last_exc = exc
                    if attempt >= max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {exc}"
                        )
                        raise

                    raw_delay = min(max_backoff, backoff_base * (2 ** (attempt - 1)))
                    jittered = raw_delay * random.uniform(1 - jitter, 1 + jitter)
                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed "
                        f"({type(exc).__name__}: {exc}); retrying in {jittered:.2f}s"
                    )
                    await asyncio.sleep(jittered)

            # Unreachable — either we return or raise above.
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
