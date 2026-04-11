"""WeChat Mini Games scraper via Tencent Ying Yong Bao (sj.qq.com).

Uses the Tencent YYB pagination API (``/v2/dc_pcyyb_official``) to pull
up to 400 ranked games per chart. The public sj.qq.com pages are Next.js
SPAs that call this same API client-side on scroll; we just POST to it
directly with the right ``layout`` + cursor and skip the browser.

Three pagination styles:
  - **rank**  ( popular / best-sell / new )
        Server expects ``listI.exposed_appids.repInt`` — a running list
        of app_ids already shown. We accumulate it across pages and
        send it back on each request. Cleanly gets us 400 rows.
  - **tag**   ( xiuxianyizhi / rpg / chess / slg02 / avg / danji )
        Shared ``layout='YYB_HOME_WECHAT_GAME_CATEGORY'``; the tag is
        passed in ``listS.tag_alias.repStr``. No cursor — server
        paginates by offset alone. Exhausts on small tags (~15-30) and
        saturates at ~180-400 on big ones.
  - **list**  ( choice_game_list, hot_game_list )
        Curated editorial lists. Tiny (~8 items). Plain offset paging.

All charts are CN-region only. Results are returned as ``RankingEntry``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)


# Full chart configuration. Each entry controls which API request shape we
# build. `tag_alias` is only set for the 6 tag charts; `cursor` is True for
# the three rank charts that need exposed_appids.
_CHARTS: dict[str, dict[str, Any]] = {
    # --- Ranking charts (use exposed_appids cursor) ---
    "hot": {
        "layout": "wechat-popularrank-game-list",
        "ref_path": "/wechat-game/popular-game-rank",
        "label": "热门榜",
        "cursor": True,
    },
    "top_grossing": {
        "layout": "wechat-bestsellrank-game-list",
        "ref_path": "/wechat-game/best-sell-game-rank",
        "label": "畅销榜",
        "cursor": True,
    },
    "new": {
        "layout": "wechat-newrank-game-list",
        "ref_path": "/wechat-game/new-game-rank",
        "label": "新游榜",
        "cursor": True,
    },
    # --- Curated lists (no cursor, small N) ---
    "featured": {
        "layout": "wechat_choice_game_list",
        "ref_path": "/wechat-game/choice-game-list",
        "label": "小游戏精选榜",
        "cursor": False,
    },
    # --- Category listings (shared layout + tag_alias) ---
    "tag_puzzle": {
        "layout": "YYB_HOME_WECHAT_GAME_CATEGORY",
        "ref_path": "/wechat-game-tag/xiuxianyizhi",
        "label": "休闲益智",
        "tag_alias": "xiuxianyizhi",
        "cursor": False,
    },
    "tag_rpg": {
        "layout": "YYB_HOME_WECHAT_GAME_CATEGORY",
        "ref_path": "/wechat-game-tag/rpg",
        "label": "角色扮演",
        "tag_alias": "rpg",
        "cursor": False,
    },
    "tag_board": {
        "layout": "YYB_HOME_WECHAT_GAME_CATEGORY",
        "ref_path": "/wechat-game-tag/chess",
        "label": "棋牌",
        "tag_alias": "chess",
        "cursor": False,
    },
    "tag_strategy": {
        "layout": "YYB_HOME_WECHAT_GAME_CATEGORY",
        "ref_path": "/wechat-game-tag/slg02",
        "label": "策略",
        "tag_alias": "slg02",
        "cursor": False,
    },
    "tag_adventure": {
        "layout": "YYB_HOME_WECHAT_GAME_CATEGORY",
        "ref_path": "/wechat-game-tag/avg",
        "label": "动作冒险",
        "tag_alias": "avg",
        "cursor": False,
    },
    "tag_singleplayer": {
        "layout": "YYB_HOME_WECHAT_GAME_CATEGORY",
        "ref_path": "/wechat-game-tag/danji",
        "label": "单机",
        "tag_alias": "danji",
        "cursor": False,
    },
}


_API_URL = "https://yybadaccess.3g.qq.com/v2/dc_pcyyb_official"

# How many games we want per chart (soft cap; small charts exhaust earlier)
_TARGET_PER_CHART = 400
# Max pages to walk before giving up (safety net, 20 items/page)
_MAX_PAGES = 30

_BASE = "https://sj.qq.com"


# Matches the hydration blob Next.js emits. Tolerant to whitespace / attrs.
_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


class WeChatMiniScraper(BaseScraper):
    """Scrapes WeChat mini-game rankings via Tencent YYB paginated API."""

    platform = "wechat_mini"
    rate_limit = 1.2  # be polite to the API, 20 games/page, ~20 pages/chart

    def __init__(self, proxy_url: str | None = None) -> None:
        super().__init__(proxy_url)
        # Fresh guid per instance — Tencent's YYB API rate-limits on the
        # userInfo.guid field. A fixed guid gets throttled to 0 items
        # after a few minutes on tag charts. A per-instance random UUID
        # keeps each scraper run on a clean budget.
        self._guid: str = str(uuid.uuid4())

    async def scrape_rankings(
        self, chart_type: str = "hot", region: str = "CN"
    ) -> list[RankingEntry]:
        """Fetch up to 400 mini-games for a given chart.

        Always CN-region. Unknown chart_type returns empty.
        """
        meta = _CHARTS.get(chart_type)
        if not meta:
            logger.warning(f"[wechat_mini] unknown chart_type {chart_type!r}")
            return []

        layout: str = meta["layout"]
        ref_path: str = meta["ref_path"]
        tag_alias: str | None = meta.get("tag_alias")
        needs_cursor: bool = bool(meta.get("cursor"))

        client = await self.get_client()
        referer = f"{_BASE}{ref_path}"

        all_items: dict[str, dict[str, Any]] = {}  # app_id → raw item
        exposed_appids: list[str] = []

        # Rank charts use an `exposed_appids` cursor and naturally start at
        # page 2 (page 1 is the SSR load). Tag / list charts use offset-only
        # paging and need to start at page 1 on cold calls, otherwise the
        # server returns 0 thinking we already consumed page 1.
        start_page = 2 if needs_cursor else 1

        for page in range(start_page, start_page + _MAX_PAGES):
            body = _build_api_body(
                layout,
                page,
                exposed_appids=exposed_appids if needs_cursor else None,
                tag_alias=tag_alias,
                guid=self._guid,
            )
            try:
                await self.throttle()
                resp = await client.post(
                    _API_URL,
                    content=body,
                    headers={
                        "Content-Type": "text/plain;charset=UTF-8",
                        "Origin": _BASE,
                        "Referer": referer,
                        "Accept": "application/json",
                        "Accept-Language": "zh-CN,zh;q=0.9",
                    },
                    timeout=20,
                )
            except Exception as e:
                logger.warning(
                    f"[wechat_mini] {chart_type} page={page} request failed: {e}"
                )
                break

            if resp.status_code != 200:
                logger.warning(
                    f"[wechat_mini] {chart_type} page={page} http={resp.status_code}"
                )
                break

            try:
                data = resp.json()
            except Exception:
                logger.warning(
                    f"[wechat_mini] {chart_type} page={page} non-JSON response"
                )
                break

            if data.get("ret") != 0:
                logger.warning(
                    f"[wechat_mini] {chart_type} page={page} ret={data.get('ret')} "
                    f"msg={data.get('msg', '')!r}"
                )
                break

            components = data.get("data", {}).get("components") or []
            items = (
                components[0].get("data", {}).get("itemData") or [] if components else []
            )
            if not items:
                break

            new_on_page = 0
            for raw in items:
                app_id = str(raw.get("app_id") or "").strip()
                if not app_id or app_id in all_items:
                    continue
                all_items[app_id] = raw
                new_on_page += 1
                if needs_cursor:
                    exposed_appids.append(app_id)

            if new_on_page == 0:
                # Server is repeating pages, we've exhausted the list
                break
            if len(all_items) >= _TARGET_PER_CHART:
                break

        results: list[RankingEntry] = []
        for rank, (app_id, raw) in enumerate(all_items.items(), start=1):
            if rank > _TARGET_PER_CHART:
                break
            entry = _item_to_ranking_entry(raw, rank, chart_type)
            if entry:
                results.append(entry)

        logger.info(
            f"[wechat_mini] {chart_type} ({meta['label']}): "
            f"{len(results)} ranked entries"
        )
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


# Genre backfill for tag charts — map chart_type to canonical genres.json key.
_CHART_GENRE_MAP: dict[str, str] = {
    "tag_puzzle": "puzzle",
    "tag_rpg": "rpg",
    "tag_board": "board",
    "tag_strategy": "strategy",
    "tag_adventure": "adventure",
    # singleplayer is not a genre, so no mapping — genre stays NULL.
}


def _build_api_body(
    layout: str,
    page: int,
    *,
    exposed_appids: list[str] | None = None,
    tag_alias: str | None = None,
    size: int = 20,
    guid: str = "00000000-0000-0000-0000-000000000000",
) -> bytes:
    """Build the POST body for /v2/dc_pcyyb_official.

    Shape matches what sj.qq.com's web client sends when you scroll a
    ranking page. The ``guid`` is caller-supplied so we can rotate it
    to avoid Tencent's per-guid rate limiting.
    """
    list_i: dict[str, Any] = {"offset": {"repInt": [page]}}
    if exposed_appids:
        # Cursor: list of already-shown app_ids. Wrapped in a list of lists.
        list_i["exposed_appids"] = {"repInt": [exposed_appids]}

    list_s: dict[str, Any] = {"region": {"repStr": ["CN"]}}
    if tag_alias:
        list_s["tag_alias"] = {"repStr": [tag_alias]}

    payload = {
        "head": {
            "cmd": "dc_pcyyb_official",
            "authInfo": {"businessId": "AuthName"},
            "deviceInfo": {"platformType": 1},
            "userInfo": {"guid": guid},
            "expSceneIds": "92250",
            "hostAppInfo": {"scene": "game_list"},
        },
        "body": {
            "bid": "yybhome",
            "offset": 0,
            "size": size,
            "preview": False,
            "listS": list_s,
            "layout": layout,
            "listI": list_i,
        },
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _item_to_ranking_entry(
    raw: dict, rank: int, chart_type: str
) -> RankingEntry | None:
    """Convert a raw Tencent item into our RankingEntry dataclass."""
    name = (raw.get("app_name") or raw.get("name") or "").strip()
    app_id = str(raw.get("app_id") or raw.get("appId") or "").strip()
    if not name or not app_id:
        return None

    developer = (raw.get("developer") or raw.get("cp_name") or "") or None
    if developer:
        developer = developer.strip() or None
    icon_url = (raw.get("icon") or raw.get("icon_url") or "") or None

    # Genre precedence: chart-derived (from tag URL) > item's own cate_name_new
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
    detail_url = f"{_BASE}/appdetail/{pkg_name}" if pkg_name else None

    return RankingEntry(
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
