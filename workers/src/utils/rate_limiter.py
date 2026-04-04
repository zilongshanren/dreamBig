"""Rate limiter and retry utilities for scrapers."""

from __future__ import annotations

import asyncio
import logging
from functools import wraps

logger = logging.getLogger(__name__)


async def retry_with_backoff(
    coro_fn,
    max_retries: int = 3,
    base_delay: float = 60.0,
    max_delay: float = 1800.0,
):
    """Retry an async function with exponential backoff.

    Delays: 60s, 300s, 1800s (1min, 5min, 30min)
    """
    last_error = None

    for attempt in range(max_retries):
        try:
            return await coro_fn()
        except Exception as e:
            last_error = e
            delay = min(base_delay * (5 ** attempt), max_delay)
            logger.warning(
                f"Attempt {attempt + 1}/{max_retries} failed: {e}. "
                f"Retrying in {delay}s..."
            )
            await asyncio.sleep(delay)

    raise last_error  # type: ignore
