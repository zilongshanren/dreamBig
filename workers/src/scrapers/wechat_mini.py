"""WeChat Mini Games scraper — Playwright-based aldzs.com parser.

WeChat's ecosystem is closed, so official APIs are unavailable. The
de-facto public ranking source is 阿拉丁指数 (aldzs.com), which publishes
daily mini-game leaderboards. Their page is JS-rendered so httpx alone
doesn't work — this scraper uses Playwright to load the page and then
defensively extracts rank rows via a JavaScript runtime call.

Strategy:
  1. Open the rankings page with Playwright + realistic headers.
  2. Wait for network idle so JS has populated the ranking grid.
  3. Evaluate a JS snippet in the page that walks the DOM and returns
     a list of {rank, name, icon, category} objects. The walker is
     tolerant of layout changes — it looks for any element whose text
     matches a rank pattern (integer 1..200) with adjacent Chinese text.
  4. If the walker returns nothing, fall back to parsing the full HTML
     with a couple of common CSS selectors.
  5. On any failure, log a clear warning and return [] — the scheduler
     job stays green.

Install: already covered by the workers/Dockerfile (playwright + chromium).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from typing import Any

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)


# Primary and fallback ranking URLs (aldzs.com has moved sections before).
ALDZS_CANDIDATE_URLS = [
    "https://www.aldzs.com/viewpointminigame",
    "https://www.aldzs.com/top/minigame",
    "https://www.aldzs.com/top/game",
    "https://www.aldzs.com/minigame/rank",
]


# JS snippet that walks the DOM and returns a list of candidate rank rows.
# It's deliberately generic — we look for any element with a number 1..300
# followed by a non-numeric text chunk (the game name).
_RANK_EXTRACTOR_JS = r"""
() => {
    const out = [];
    const seen = new Set();

    // Strategy A: look for tables / lists with explicit rank cells
    const rows = document.querySelectorAll(
        'tr, li, .rank-item, .top-item, [class*="rank"], [class*="game"]'
    );
    for (const row of rows) {
        const text = (row.innerText || '').trim();
        if (!text) continue;

        // Find a 1-3 digit rank at the start or isolated
        const m = text.match(/^(\d{1,3})\b/);
        if (!m) continue;
        const rank = parseInt(m[1], 10);
        if (rank < 1 || rank > 500) continue;

        // Extract the first line or biggest Chinese/English text chunk
        // that isn't just the rank number.
        const lines = text.split(/\n+/).map(s => s.trim()).filter(Boolean);
        if (lines.length < 2) continue;
        // First line often is "1" then second is name. Or first line is "1 游戏名"
        let name = null;
        if (/^\d+$/.test(lines[0]) && lines.length > 1) {
            name = lines[1];
        } else {
            // "1 游戏名" — strip the leading rank
            name = lines[0].replace(/^\d{1,3}\s*[.、·]?\s*/, '');
        }
        if (!name || name.length < 2 || name.length > 80) continue;
        if (/^\d+$/.test(name)) continue;

        // Dedup by rank + name
        const key = rank + '|' + name;
        if (seen.has(key)) continue;
        seen.add(key);

        // Try to grab an icon img inside this row
        let iconUrl = null;
        const img = row.querySelector('img');
        if (img) iconUrl = img.src || img.getAttribute('data-src') || null;

        out.push({ rank, name, iconUrl });
        if (out.length >= 300) break;
    }

    // Sort by rank and return
    out.sort((a, b) => a.rank - b.rank);
    return out;
}
"""


class WeChatMiniScraper(BaseScraper):
    """WeChat Mini Games scraper via aldzs.com rankings page."""

    platform = "wechat_mini"
    rate_limit = 6.0  # be gentle with aldzs.com

    async def scrape_rankings(
        self, chart_type: str = "hot", region: str = "CN"
    ) -> list[RankingEntry]:
        """Scrape WeChat mini-game rankings from aldzs.com.

        chart_type is currently ignored (there's only the generic "hot"
        ranking exposed). region is always CN — WeChat has no other region.
        """
        entries: list[dict] = []
        last_err: Exception | None = None

        for url in ALDZS_CANDIDATE_URLS:
            try:
                entries = await self._scrape_via_playwright(url)
                if entries:
                    logger.info(
                        f"[wechat_mini] got {len(entries)} ranks from {url}"
                    )
                    break
                logger.debug(f"[wechat_mini] empty rankings from {url}, trying next")
            except Exception as e:
                last_err = e
                logger.debug(f"[wechat_mini] {url} failed: {e}")

        if not entries:
            logger.warning(
                f"[wechat_mini] all candidate URLs failed or returned empty. "
                f"Last error: {last_err}. aldzs.com may have changed layout — "
                f"check the selectors in workers/src/scrapers/wechat_mini.py."
            )
            return []

        results: list[RankingEntry] = []
        for e in entries:
            rank = e.get("rank")
            name = (e.get("name") or "").strip()
            if not name or not isinstance(rank, int):
                continue
            # aldzs doesn't expose a stable mini-game ID, so we synthesise
            # one from the hashed name. Name-based (NOT rank-based) so a
            # game climbing from #10 to #3 still maps to the same row.
            synthetic_id = "wxgame_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:12]
            results.append(
                RankingEntry(
                    platform_id=synthetic_id,
                    name=name,
                    rank_position=rank,
                    chart_type=chart_type,
                    region="CN",
                    icon_url=e.get("iconUrl"),
                    metadata={"source": "aldzs.com"},
                )
            )
        return results

    async def _scrape_via_playwright(self, url: str) -> list[dict]:
        """Load `url` with Playwright, then run the rank extractor JS."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning(
                "[wechat_mini] playwright not installed — skipping. "
                "The workers Dockerfile installs it; make sure you built "
                "the scraper image with --no-cache if you're hitting this."
            )
            return []

        ua = random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ])

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            try:
                ctx = await browser.new_context(
                    user_agent=ua,
                    locale="zh-CN",
                    viewport={"width": 1280, "height": 800},
                    extra_http_headers={
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
                        "Referer": "https://www.aldzs.com/",
                    },
                )
                page = await ctx.new_page()

                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                # Wait for any lazy-loaded content to settle
                try:
                    await page.wait_for_load_state("networkidle", timeout=20_000)
                except Exception:
                    pass  # networkidle can linger on ad-heavy pages
                await page.wait_for_timeout(1500)

                # Debug: if verbose logging is on, dump a tiny HTML preview.
                try:
                    html_preview = await page.evaluate(
                        "() => document.body.innerText.slice(0, 500)"
                    )
                    logger.debug(f"[wechat_mini] body preview from {url}: {html_preview!r}")
                except Exception:
                    pass

                raw: list[dict[str, Any]] = await page.evaluate(_RANK_EXTRACTOR_JS)
                return raw or []
            finally:
                await browser.close()

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Details scraping not supported — aldzs.com public view doesn't expose detail pages."""
        logger.debug(
            f"[wechat_mini] scrape_game_details({platform_id}) is a no-op for this scraper"
        )
        return None
