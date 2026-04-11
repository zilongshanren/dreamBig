"""Bilibili video comment scraper — review proxy for WeChat mini-games.

WeChat's own review tab is a closed ecosystem, so we proxy user opinion via
Bilibili comments: for a given game name we find the most-viewed videos
tagged with the name, then pull the top comments from each via Bilibili's
public reply API. Comments are stored as Review rows against the game's
platform_listing, then flow through the existing sentiment / topic
clustering pipeline.

Bilibili anti-bot notes (as of 2024):
  - ``/x/web-interface/search/type`` is protected by WBI signing. Unsigned
    requests get ``412 Precondition Failed`` after a handful of calls.
  - ``/x/v2/reply`` (comments) is public but expects a realistic browser
    session (buvid3 cookie from bilibili.com homepage) and occasionally
    returns non-zero ``code`` even on HTTP 200.

We handle both by (1) bootstrapping cookies from a real GET on
www.bilibili.com, (2) fetching WBI keys from ``/nav`` and signing search
requests, and (3) logging Bilibili's JSON ``code`` / ``message`` at INFO so
failures don't disappear into debug silence.

Pipeline:
  1. Bootstrap cookies (once per scraper instance).
  2. Search Bilibili (WBI-signed) for videos matching the game name.
  3. For the top N videos (by play count), fetch top M comments each.
  4. Emit ReviewEntry objects. ``external_id`` is bvid:reply_id so the
     same comment won't be inserted twice.
"""

from __future__ import annotations

import hashlib
import logging
import time
import urllib.parse
from datetime import datetime
from typing import Any

from .base import BaseReviewScraper, ReviewEntry

logger = logging.getLogger(__name__)


# How many Bilibili videos to pull comments from per game
DEFAULT_VIDEOS_PER_GAME = 5
# How many comments to pull per video
DEFAULT_COMMENTS_PER_VIDEO = 40
# Minimum comment length to bother indexing (filters "666" / "好" etc).
# 3 chars is lenient enough for Chinese短评 like "真好玩".
MIN_COMMENT_LEN = 3


# WBI signing — fixed permutation table published in Bilibili's web client
_MIXIN_KEY_ENC_TAB: list[int] = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def _get_mixin_key(orig: str) -> str:
    """Apply the fixed permutation to (img_key + sub_key) and take first 32."""
    return "".join(orig[i] for i in _MIXIN_KEY_ENC_TAB if i < len(orig))[:32]


def _wbi_sign(params: dict[str, Any], img_key: str, sub_key: str) -> dict[str, str]:
    """Sign a params dict for Bilibili's wbi-protected endpoints.

    Adds ``wts`` (timestamp) and ``w_rid`` (md5 signature). Returns a new
    dict with all values coerced to strings — ready for httpx params.
    """
    mixin_key = _get_mixin_key(img_key + sub_key)
    # Strip characters Bilibili's JS rejects before signing
    bad_chars = "!'()*"

    def clean(v: Any) -> str:
        return str(v).translate(str.maketrans("", "", bad_chars))

    signed = {k: clean(v) for k, v in params.items()}
    signed["wts"] = str(int(time.time()))

    query = urllib.parse.urlencode(sorted(signed.items()))
    signed["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    return signed


class BilibiliReviewScraper(BaseReviewScraper):
    """Scrapes Bilibili comments as a proxy for WeChat mini-game reviews."""

    platform = "bilibili_review"
    rate_limit = 2.5

    def __init__(self, proxy_url: str | None = None) -> None:
        super().__init__(proxy_url)
        self._wbi_keys: tuple[str, str] | None = None
        self._bootstrapped: bool = False

    # ------------------------------------------------------------------
    # Session bootstrapping
    # ------------------------------------------------------------------
    async def _bootstrap(self) -> None:
        """Visit bilibili.com once to collect cookies (buvid3 etc.)."""
        if self._bootstrapped:
            return
        client = await self.get_client()
        try:
            await client.get(
                "https://www.bilibili.com/",
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                timeout=15,
            )
        except Exception as e:
            logger.warning(f"[bilibili_review] cookie bootstrap failed: {e}")
        else:
            logger.info("[bilibili_review] cookies bootstrapped")
        self._bootstrapped = True

    async def _get_wbi_keys(self) -> tuple[str, str] | None:
        """Fetch WBI img_key / sub_key from bilibili's nav API.

        Keys rotate daily but are cached per scraper instance. Returns
        None on failure — the caller should skip the signed path.
        """
        if self._wbi_keys:
            return self._wbi_keys
        client = await self.get_client()
        try:
            resp = await client.get(
                "https://api.bilibili.com/x/web-interface/nav",
                headers={
                    "Accept": "application/json",
                    "Referer": "https://www.bilibili.com/",
                    "Origin": "https://www.bilibili.com",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[bilibili_review] nav fetch failed: {e}")
            return None

        wbi_img = (data.get("data") or {}).get("wbi_img") or {}
        img_url = wbi_img.get("img_url") or ""
        sub_url = wbi_img.get("sub_url") or ""
        if not img_url or not sub_url:
            logger.warning(
                f"[bilibili_review] nav returned no wbi_img "
                f"(code={data.get('code')}, message={data.get('message')!r})"
            )
            return None

        img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
        sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
        self._wbi_keys = (img_key, sub_key)
        logger.info(f"[bilibili_review] got WBI keys (img={img_key[:8]}..., sub={sub_key[:8]}...)")
        return self._wbi_keys

    # ------------------------------------------------------------------
    # Main scraper entry point
    # ------------------------------------------------------------------
    async def scrape_reviews(
        self,
        platform_id: str,
        region: str = "CN",
        limit: int = DEFAULT_COMMENTS_PER_VIDEO * DEFAULT_VIDEOS_PER_GAME,
        lang: str = "zh",
    ) -> list[ReviewEntry]:
        """Scrape Bilibili video comments for a given game.

        ``platform_id`` here is the game name (Chinese preferred) — not a
        numeric ID, because Bilibili has no concept of a "game row".
        """
        game_name = platform_id.strip()
        if not game_name:
            return []

        await self._bootstrap()

        videos_per_game = min(
            DEFAULT_VIDEOS_PER_GAME,
            max(1, limit // DEFAULT_COMMENTS_PER_VIDEO),
        )
        comments_per_video = max(10, limit // max(1, videos_per_game))

        videos = await self._search_videos(game_name, limit=videos_per_game)
        if not videos:
            logger.info(f"[bilibili_review] no videos found for '{game_name}'")
            return []

        all_reviews: list[ReviewEntry] = []
        for v in videos:
            aid = v.get("aid")
            bvid = v.get("bvid")
            if not aid or not bvid:
                continue
            try:
                rv = await self._fetch_video_comments(
                    aid=int(aid),
                    bvid=str(bvid),
                    video_play=int(v.get("play") or 0),
                    video_title=str(v.get("title") or ""),
                    limit=comments_per_video,
                )
                all_reviews.extend(rv)
            except Exception as e:
                logger.warning(
                    f"[bilibili_review] comments fetch failed for {bvid}: {e}"
                )
            if len(all_reviews) >= limit:
                break

        logger.info(
            f"[bilibili_review] '{game_name}': {len(all_reviews)} comments "
            f"across {len(videos)} videos"
        )
        return all_reviews[:limit]

    # ------------------------------------------------------------------
    # Search (WBI-signed)
    # ------------------------------------------------------------------
    async def _search_videos(
        self, keyword: str, limit: int
    ) -> list[dict[str, Any]]:
        """Search Bilibili for videos matching `keyword`.

        Signs the request with WBI so we don't get 412'd after a handful
        of calls. Falls back to unsigned request if key fetch fails —
        first call may still work even unsigned.
        """
        import re

        client = await self.get_client()
        await self.throttle()

        params = {
            "keyword": f"{keyword} 小游戏",
            "search_type": "video",
            "order": "click",  # most viewed
            "page": 1,
        }

        keys = await self._get_wbi_keys()
        if keys:
            img_key, sub_key = keys
            params = _wbi_sign(params, img_key, sub_key)

        try:
            resp = await client.get(
                "https://api.bilibili.com/x/web-interface/wbi/search/type",
                params=params,
                headers={
                    "Accept": "application/json",
                    "Referer": "https://www.bilibili.com/",
                    "Origin": "https://www.bilibili.com",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[bilibili_review] search failed for '{keyword}': {e}")
            return []

        code = data.get("code", -1)
        if code != 0:
            logger.warning(
                f"[bilibili_review] search non-zero for '{keyword}': "
                f"code={code} message={data.get('message', '')!r}"
            )
            return []

        results = (data.get("data") or {}).get("result") or []
        for r in results:
            if isinstance(r.get("title"), str):
                r["title"] = re.sub(r"<[^>]+>", "", r["title"])
        logger.info(
            f"[bilibili_review] search '{keyword}' → {len(results)} videos"
        )
        return results[:limit]

    # ------------------------------------------------------------------
    # Comment fetch (no WBI needed — public endpoint)
    # ------------------------------------------------------------------
    async def _fetch_video_comments(
        self,
        aid: int,
        bvid: str,
        video_play: int,
        video_title: str,
        limit: int,
    ) -> list[ReviewEntry]:
        """Fetch top comments of a single video.

        Tries the legacy ``/x/v2/reply`` endpoint first, then the newer
        ``/x/v2/reply/main`` endpoint as a fallback — different videos
        respond to different endpoints depending on their config.
        """
        client = await self.get_client()
        replies_raw: list[dict[str, Any]] = []

        # --- attempt 1: /x/v2/reply (sorted by likes) ---
        await self.throttle()
        try:
            resp = await client.get(
                "https://api.bilibili.com/x/v2/reply",
                params={
                    "type": 1,  # video
                    "oid": aid,
                    "pn": 1,
                    "ps": min(limit, 49),
                    "sort": 2,  # by likes
                },
                headers={
                    "Accept": "application/json",
                    "Referer": f"https://www.bilibili.com/video/{bvid}",
                    "Origin": "https://www.bilibili.com",
                },
                timeout=15,
            )
            data = resp.json()
            code = data.get("code", -1)
            if code == 0:
                replies_raw = (data.get("data") or {}).get("replies") or []
            else:
                logger.info(
                    f"[bilibili_review] /reply code={code} for {bvid}, "
                    f"trying /reply/main"
                )
        except Exception as e:
            logger.info(f"[bilibili_review] /reply error for {bvid}: {e}")

        # --- attempt 2: /x/v2/reply/main (newer endpoint) ---
        if not replies_raw:
            await self.throttle()
            try:
                resp = await client.get(
                    "https://api.bilibili.com/x/v2/reply/main",
                    params={
                        "type": 1,
                        "oid": aid,
                        "mode": 3,  # hot
                        "next": 0,
                        "ps": min(limit, 20),
                    },
                    headers={
                        "Accept": "application/json",
                        "Referer": f"https://www.bilibili.com/video/{bvid}",
                        "Origin": "https://www.bilibili.com",
                    },
                    timeout=15,
                )
                data = resp.json()
                code = data.get("code", -1)
                if code != 0:
                    logger.info(
                        f"[bilibili_review] /reply/main code={code} "
                        f"message={data.get('message', '')!r} for {bvid}"
                    )
                    return []
                replies_raw = (data.get("data") or {}).get("replies") or []
            except Exception as e:
                logger.info(f"[bilibili_review] /reply/main error for {bvid}: {e}")
                return []

        out: list[ReviewEntry] = []
        for r in replies_raw[:limit]:
            content = (r.get("content") or {}).get("message") or ""
            content = content.strip()
            if len(content) < MIN_COMMENT_LEN:
                continue

            reply_id = str(r.get("rpid") or "")
            if not reply_id:
                continue

            try:
                ctime = datetime.fromtimestamp(int(r.get("ctime") or 0))
            except (TypeError, ValueError):
                ctime = datetime.now()

            member = r.get("member") or {}
            out.append(
                ReviewEntry(
                    external_id=f"bili:{bvid}:{reply_id}",
                    rating=None,
                    content=content[:4000],
                    author_name=member.get("uname") or None,
                    helpful_count=int(r.get("like") or 0),
                    language="zh",
                    posted_at=ctime,
                    metadata={
                        "source": "bilibili",
                        "video_bvid": bvid,
                        "video_title": video_title[:200],
                        "video_play": video_play,
                    },
                )
            )
        logger.info(
            f"[bilibili_review] {bvid}: {len(out)} comments kept "
            f"(from {len(replies_raw)} raw, video plays={video_play})"
        )
        return out
