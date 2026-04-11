"""WeChat Mini Games scraper via Tencent Ying Yong Bao (sj.qq.com).

The page at https://sj.qq.com/wechat-game is a Next.js SPA but its full
ranking data is embedded server-side in ``__NEXT_DATA__``. Each of the
three leaderboard sub-pages returns 20 ranked games with stable Tencent
``app_id`` identifiers and Chinese names. No Playwright required.

Chart mapping:
  - ``hot``           → 热门榜 /wechat-game/popular-game-rank
  - ``top_grossing``  → 畅销榜 /wechat-game/best-sell-game-rank
  - ``new``           → 新游榜 /wechat-game/new-game-rank

All charts are CN-region only (WeChat ecosystem).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)


# Chart type → Tencent ranking page path
_CHART_PATHS: dict[str, str] = {
    "hot": "/wechat-game/popular-game-rank",
    "top_grossing": "/wechat-game/best-sell-game-rank",
    "new": "/wechat-game/new-game-rank",
}

# Fallback single-page source (5 items per ranking, but all three in one HTML)
_FALLBACK_PATH = "/wechat-game"

_BASE = "https://sj.qq.com"


# Matches the hydration blob Next.js emits. Tolerant to whitespace / attrs.
_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


class WeChatMiniScraper(BaseScraper):
    """Scrapes WeChat mini-game rankings from sj.qq.com."""

    platform = "wechat_mini"
    rate_limit = 2.5

    async def scrape_rankings(
        self, chart_type: str = "hot", region: str = "CN"
    ) -> list[RankingEntry]:
        """Fetch a chart of WeChat mini-games.

        Always returns CN-region. Unknown chart_type falls back to the
        home page (5 items from 热门榜).
        """
        path = _CHART_PATHS.get(chart_type, _FALLBACK_PATH)
        url = f"{_BASE}{path}"

        try:
            client = await self.get_client()
            await self.throttle()
            resp = await client.get(
                url,
                headers={
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Referer": f"{_BASE}/",
                },
                timeout=25,
            )
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logger.warning(f"[wechat_mini] fetch failed for {url}: {e}")
            return []

        next_data = _extract_next_data(html)
        if not next_data:
            logger.warning(f"[wechat_mini] __NEXT_DATA__ not found at {url}")
            return []

        items = _collect_rank_items(next_data, chart_type)
        if not items:
            logger.warning(
                f"[wechat_mini] no ranking items extracted from {url} — "
                f"page structure may have changed"
            )
            return []

        results: list[RankingEntry] = []
        for rank, raw in enumerate(items, start=1):
            name = (raw.get("app_name") or raw.get("name") or "").strip()
            app_id = str(raw.get("app_id") or raw.get("appId") or "").strip()
            if not name or not app_id:
                continue

            developer = (raw.get("developer") or raw.get("cp_name") or "") or None
            if developer:
                developer = developer.strip() or None
            icon_url = (raw.get("icon") or raw.get("icon_url") or "") or None
            cate = (raw.get("cate_name_new") or raw.get("cate_name") or "") or None
            rating_raw = raw.get("average_rating")
            rating: float | None = None
            try:
                if rating_raw and float(rating_raw) > 0:
                    rating = float(rating_raw)
            except (TypeError, ValueError):
                rating = None

            results.append(
                RankingEntry(
                    platform_id=app_id,
                    name=name,
                    rank_position=rank,
                    chart_type=chart_type,
                    region="CN",
                    rating=rating,
                    developer=developer,
                    genre=cate,
                    icon_url=icon_url,
                    url=f"{_BASE}/appdetail/{app_id}",
                    metadata={
                        "source": "sj.qq.com",
                        "editor_intro": (raw.get("editor_intro") or "")[:300] or None,
                        "pkg_name": raw.get("pkg_name"),
                    },
                )
            )

        logger.info(f"[wechat_mini] {chart_type}: {len(results)} ranked entries")
        return results

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Fetch the detail page for a single mini-game by its Tencent app_id."""
        url = f"{_BASE}/appdetail/{platform_id}"
        try:
            client = await self.get_client()
            await self.throttle()
            resp = await client.get(url, timeout=20)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logger.debug(f"[wechat_mini] detail fetch failed for {platform_id}: {e}")
            return None

        next_data = _extract_next_data(html)
        if not next_data:
            return None

        # Detail pages put the app blob in pageProps.context.detailApp or similar
        def _walk(obj: Any) -> dict | None:
            if isinstance(obj, dict):
                if "app_id" in obj and ("app_name" in obj or "name" in obj):
                    return obj
                for v in obj.values():
                    found = _walk(v)
                    if found:
                        return found
            elif isinstance(obj, list):
                for x in obj:
                    found = _walk(x)
                    if found:
                        return found
            return None

        app = _walk(next_data) or {}
        if not app:
            return None

        return GameDetails(
            platform_id=platform_id,
            name=(app.get("app_name") or app.get("name") or "").strip(),
            developer=(app.get("developer") or app.get("cp_name") or None),
            description=(app.get("editor_intro") or app.get("description") or None),
            genre=(app.get("cate_name_new") or app.get("cate_name") or None),
            icon_url=(app.get("icon") or None),
            screenshots=_collect_screenshots(app),
            url=url,
            metadata={"source": "sj.qq.com"},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_next_data(html: str) -> dict | None:
    """Pull the __NEXT_DATA__ JSON blob out of a Next.js HTML response."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        logger.debug(f"[wechat_mini] __NEXT_DATA__ not valid JSON: {e}")
        return None


# Mapping from chart_type to the card title we're looking for on the home page.
_CHART_TO_HOME_TITLE = {
    "hot": "热门榜",
    "top_grossing": "畅销榜",
    "new": "新游榜",
}


def _collect_rank_items(next_data: dict, chart_type: str) -> list[dict]:
    """Walk __NEXT_DATA__ and return the best matching ranking item list.

    Works for both:
      - Dedicated ranking pages (/wechat-game/popular-game-rank etc): a single
        component with 20+ items.
      - The home page (/wechat-game): multiple YYB_HOME_RANK_* cards, each
        with 5 items, keyed by card title (热门榜 / 畅销榜 / 新游榜).
    """
    try:
        components = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("dynamicCardResponse", {})
            .get("data", {})
            .get("components", [])
        )
    except AttributeError:
        components = []

    if not components:
        return []

    # Case 1: single-component ranking page (20 items).
    if len(components) == 1:
        return list(components[0].get("data", {}).get("itemData") or [])

    # Case 2: home page — pick by title.
    target_title = _CHART_TO_HOME_TITLE.get(chart_type)
    if target_title:
        for comp in components:
            d = comp.get("data", {}) or {}
            if d.get("title") == target_title:
                return list(d.get("itemData") or [])

    # Fallback: the first HOT_WECHAT_GAME card.
    for comp in components:
        if comp.get("cardId") == "YYB_HOME_HOT_WECHAT_GAME":
            return list(comp.get("data", {}).get("itemData") or [])

    return []


def _collect_screenshots(app: dict) -> list[str]:
    """Extract screenshot URLs from an sj.qq.com app blob."""
    shots: list[str] = []
    for key in ("audited_snapshots", "screenshots", "image"):
        v = app.get(key)
        if isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    shots.append(x)
                elif isinstance(x, dict):
                    url = x.get("url") or x.get("image_url")
                    if url:
                        shots.append(url)
        elif isinstance(v, str):
            shots.append(v)
    return shots[:10]
