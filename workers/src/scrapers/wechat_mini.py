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


# Chart type → Tencent ranking page path.
# The `tag_*` entries aren't strictly rankings — they're category listings
# (ordered by editorial / downloads) — but we treat them as charts so the
# usual ranking_snapshots pipeline captures them consistently.
_CHART_PATHS: dict[str, str] = {
    # Ranking charts
    "hot": "/wechat-game/popular-game-rank",
    "top_grossing": "/wechat-game/best-sell-game-rank",
    "new": "/wechat-game/new-game-rank",
    "featured": "/wechat-game/choice-game-list",
    # Category listings (tag-based). genre in _CHART_GENRE_MAP below.
    "tag_puzzle": "/wechat-game-tag/xiuxianyizhi",       # 休闲益智
    "tag_rpg": "/wechat-game-tag/rpg",                   # 角色扮演
    "tag_board": "/wechat-game-tag/chess",               # 棋牌
    "tag_strategy": "/wechat-game-tag/slg02",            # 策略
    "tag_adventure": "/wechat-game-tag/avg",             # 动作冒险
    "tag_singleplayer": "/wechat-game-tag/danji",        # 单机
}

# If a chart is a category listing, this maps its chart_type to the canonical
# genre key from shared/genres.json. Used to backfill the `genre` column on
# newly-created Game rows so dashboard genre filters work for mini-games.
_CHART_GENRE_MAP: dict[str, str] = {
    "tag_puzzle": "puzzle",
    "tag_rpg": "rpg",
    "tag_board": "board",
    "tag_strategy": "strategy",
    "tag_adventure": "adventure",
    # singleplayer is not a genre, so no mapping — genre stays NULL.
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
            # Genre precedence: chart-derived (from tag URL) > item's own cate_name_new.
            # Tag charts are the more reliable genre signal because they come
            # from Tencent's curated category pages.
            cate = (
                _CHART_GENRE_MAP.get(chart_type)
                or (raw.get("cate_name_new") or raw.get("cate_name") or "")
                or None
            )
            rating_raw = raw.get("average_rating")
            rating: float | None = None
            try:
                if rating_raw and float(rating_raw) > 0:
                    rating = float(rating_raw)
            except (TypeError, ValueError):
                rating = None

            pkg_name = (raw.get("pkg_name") or "").strip() or None
            # The real Tencent detail page uses /appdetail/{pkg_name} (wx-prefixed),
            # not /appdetail/{app_id}. /app/{app_id} is also valid but less info.
            detail_url = f"{_BASE}/appdetail/{pkg_name}" if pkg_name else None
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
                    url=detail_url,
                    metadata={
                        "source": "sj.qq.com",
                        "editor_intro": (raw.get("editor_intro") or "")[:300] or None,
                        "pkg_name": pkg_name,
                        "username": raw.get("username") or None,
                        "tags": raw.get("tags") or None,
                    },
                )
            )

        logger.info(f"[wechat_mini] {chart_type}: {len(results)} ranked entries")
        return results

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Fetch the detail page for a mini-game.

        `platform_id` is the numeric Tencent app_id (stored in PlatformListing).
        To reach the real detail page we need the wx-prefixed ``pkg_name``,
        so we first look it up from a previous ranking_snapshots row's
        platform_listings.metadata. If that's missing we fall back to
        /app/{app_id} which exists but has less data.
        """
        import psycopg as _pg
        import os as _os

        pkg_name: str | None = None
        try:
            with _pg.connect(_os.environ.get("DATABASE_URL", "")) as c:
                row = c.execute(
                    """
                    SELECT metadata->>'pkg_name'
                    FROM platform_listings
                    WHERE platform = 'wechat_mini' AND platform_id = %s
                    """,
                    (platform_id,),
                ).fetchone()
                if row and row[0]:
                    pkg_name = row[0]
        except Exception:
            pass

        if pkg_name:
            url = f"{_BASE}/appdetail/{pkg_name}"
        else:
            url = f"{_BASE}/app/{platform_id}"

        try:
            client = await self.get_client()
            await self.throttle()
            resp = await client.get(
                url,
                headers={
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Referer": f"{_BASE}/wechat-game",
                },
                timeout=20,
            )
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logger.debug(f"[wechat_mini] detail fetch failed for {platform_id}: {e}")
            return None

        next_data = _extract_next_data(html)
        if not next_data:
            return None

        # Detail pages put the app blob inside the first component of
        # dynamicCardResponse — usually yybn_game_basic_info.itemData[0].
        app = _find_detail_app(next_data)
        if not app:
            return None

        return GameDetails(
            platform_id=platform_id,
            name=(app.get("app_name") or app.get("name") or "").strip(),
            developer=(app.get("developer") or app.get("cp_name") or None),
            description=(
                app.get("editor_intro")
                or app.get("description")
                or None
            ),
            genre=(app.get("cate_name_new") or app.get("cate_name") or None),
            icon_url=(app.get("icon") or None),
            screenshots=_parse_snap_shots(app),
            url=url,
            metadata={
                "source": "sj.qq.com",
                "pkg_name": app.get("pkg_name"),
                "username": app.get("username"),
                "tags": app.get("tags"),
                "ms_store_id": app.get("ms_store_id"),
            },
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
    """Extract screenshot URLs from an sj.qq.com app blob (deprecated path)."""
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


def _parse_snap_shots(app: dict) -> list[str]:
    """Extract screenshot URLs from the real sj.qq.com detail page.

    sj.qq.com returns `snap_shots` as a **comma-separated string** of URLs,
    not a list. Empty strings and placeholder entries are filtered out.
    """
    v = app.get("snap_shots") or app.get("audited_snapshots")
    if not v:
        return []
    if isinstance(v, str):
        parts = [p.strip() for p in v.split(",")]
    elif isinstance(v, list):
        parts = [str(p).strip() for p in v if p]
    else:
        return []
    return [p for p in parts if p and p.startswith("http")][:10]


def _find_detail_app(next_data: dict) -> dict | None:
    """Walk a detail-page __NEXT_DATA__ and return the basic_info app item.

    The interesting blob is usually at:
      pageProps.dynamicCardResponse.data.components[N].data.itemData[0]
    where the card's ID contains 'game_basic_info' or similar.
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
        return None

    # Prefer the basic_info card if present
    for comp in components:
        card_id = (comp.get("cardId") or "").lower()
        if "basic_info" in card_id or "game_detail" in card_id:
            items = (comp.get("data") or {}).get("itemData") or []
            for it in items:
                if isinstance(it, dict) and (it.get("app_id") or it.get("pkg_name")):
                    return it

    # Fallback: the first component's first item that looks like an app
    for comp in components:
        items = (comp.get("data") or {}).get("itemData") or []
        for it in items:
            if isinstance(it, dict) and (
                it.get("app_id") and (it.get("app_name") or it.get("name"))
            ):
                return it
    return None
