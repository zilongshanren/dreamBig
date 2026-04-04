"""Proxy management utilities."""

from __future__ import annotations

import logging
import os
import random

logger = logging.getLogger(__name__)


def get_proxy_url() -> str | None:
    """Get configured proxy URL from environment."""
    return os.environ.get("SCRAPE_PROXY_URL")


def get_proxy_list() -> list[str]:
    """Get list of proxy URLs if configured.

    Set SCRAPE_PROXY_LIST as comma-separated URLs in .env:
    SCRAPE_PROXY_LIST=http://proxy1:8080,http://proxy2:8080
    """
    proxy_list = os.environ.get("SCRAPE_PROXY_LIST", "")
    if not proxy_list:
        single = get_proxy_url()
        return [single] if single else []
    return [p.strip() for p in proxy_list.split(",") if p.strip()]


def get_random_proxy() -> str | None:
    """Get a random proxy from the pool."""
    proxies = get_proxy_list()
    return random.choice(proxies) if proxies else None
