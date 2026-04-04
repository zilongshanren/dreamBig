"""Playwright browser management for JS-heavy scrapers."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def get_browser(headless: bool = True):
    """Get a Playwright browser instance.

    Usage:
        async with get_browser() as browser:
            page = await browser.new_page()
            await page.goto("https://example.com")
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        try:
            yield browser
        finally:
            await browser.close()


@asynccontextmanager
async def get_page(headless: bool = True, locale: str = "zh-CN"):
    """Get a Playwright page with sensible defaults."""
    async with get_browser(headless) as browser:
        context = await browser.new_context(
            locale=locale,
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        try:
            yield page
        finally:
            await context.close()
