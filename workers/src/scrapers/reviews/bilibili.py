"""Bilibili video comment scraper — review proxy for WeChat mini-games.

WeChat's own review tab is a closed ecosystem, so we proxy user opinion
via Bilibili comments: for a given game name we find the most-viewed
videos tagged with the name, then pull the top comments from each via
Bilibili's public reply API. Comments are stored as Review rows against
the game's platform_listing, then flow through the existing sentiment /
topic clustering pipeline.

No authentication required — all endpoints are publicly accessible.

Pipeline:
  1. Search Bilibili for videos matching the game name.
  2. For the top N videos (by play count), fetch top M comments each.
  3. Emit ReviewEntry objects. `external_id` is bvid:reply_id so the
     same comment won't be inserted twice.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .base import BaseReviewScraper, ReviewEntry

logger = logging.getLogger(__name__)


# How many Bilibili videos to pull comments from per game
DEFAULT_VIDEOS_PER_GAME = 5
# How many comments to pull per video
DEFAULT_COMMENTS_PER_VIDEO = 40
# Minimum comment length to bother indexing (filters "666" / "好看" etc)
MIN_COMMENT_LEN = 6


class BilibiliReviewScraper(BaseReviewScraper):
    """Scrapes Bilibili comments as a proxy for WeChat mini-game reviews."""

    platform = "bilibili_review"
    rate_limit = 1.5

    async def scrape_reviews(
        self,
        platform_id: str,
        region: str = "CN",
        limit: int = DEFAULT_COMMENTS_PER_VIDEO * DEFAULT_VIDEOS_PER_GAME,
        lang: str = "zh",
    ) -> list[ReviewEntry]:
        """Scrape Bilibili video comments for a given game.

        ``platform_id`` here is the game name (Chinese preferred) — not a
        numeric ID, because Bilibili has no concept of a "game row". The
        caller is responsible for passing the Chinese name.
        """
        game_name = platform_id.strip()
        if not game_name:
            return []

        # Derive video count / comment count from the overall limit
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
            # Stop early if we've hit the overall cap
            if len(all_reviews) >= limit:
                break

        return all_reviews[:limit]

    async def _search_videos(
        self, keyword: str, limit: int
    ) -> list[dict[str, Any]]:
        """Use Bilibili's public search API to find videos for a keyword."""
        import re

        client = await self.get_client()
        await self.throttle()
        try:
            resp = await client.get(
                "https://api.bilibili.com/x/web-interface/search/type",
                params={
                    "keyword": f"{keyword} 小游戏",
                    "search_type": "video",
                    "order": "click",  # sort by play count (most-watched)
                    "page": 1,
                },
                headers={"Referer": "https://www.bilibili.com/"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[bilibili_review] search failed for '{keyword}': {e}")
            return []

        if data.get("code") != 0:
            logger.debug(
                f"[bilibili_review] search non-zero code={data.get('code')} "
                f"message={data.get('message')!r}"
            )
            return []

        results = (data.get("data") or {}).get("result") or []
        # Strip HTML em tags from the title snippet
        for r in results:
            if isinstance(r.get("title"), str):
                r["title"] = re.sub(r"<[^>]+>", "", r["title"])
        return results[:limit]

    async def _fetch_video_comments(
        self,
        aid: int,
        bvid: str,
        video_play: int,
        video_title: str,
        limit: int,
    ) -> list[ReviewEntry]:
        """Fetch top comments of a single video via Bilibili reply API."""
        client = await self.get_client()
        await self.throttle()
        try:
            resp = await client.get(
                "https://api.bilibili.com/x/v2/reply",
                params={
                    "type": 1,  # 1 = video
                    "oid": aid,
                    "pn": 1,
                    "ps": min(limit, 49),  # API caps at 49 per page
                    "sort": 2,  # sort by likes
                },
                headers={"Referer": f"https://www.bilibili.com/video/{bvid}"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug(f"[bilibili_review] reply fetch failed for {bvid}: {e}")
            return []

        if data.get("code") != 0:
            logger.debug(
                f"[bilibili_review] reply non-zero code={data.get('code')} "
                f"bvid={bvid}"
            )
            return []

        replies = ((data.get("data") or {}).get("replies") or [])[:limit]

        out: list[ReviewEntry] = []
        for r in replies:
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
                    rating=None,  # Bilibili comments have no star rating
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
            f"[bilibili_review] {bvid}: {len(out)} comments "
            f"(video plays={video_play})"
        )
        return out
