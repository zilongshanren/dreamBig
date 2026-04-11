"""Deep social content scraper — fetches individual video/post records.

Populates `social_content_samples` with titles, hashtags, view/like counts
so the downstream LLM can extract hook phrases. Complements the aggregate
SocialMediaScraper (which only counts volume), giving Phase 2 the
content-level depth described in PRD §10.4.

Platforms:
- Douyin / TikTok via TikHub (env: TIKHUB_API_KEY)
- YouTube via Data API v3 (env: YOUTUBE_API_KEY)
- Bilibili via public search (no auth)

Graceful fallback: missing API keys cause a logged skip, not a crash.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)


@dataclass
class SocialContent:
    """One video/post record scraped from a social platform."""

    platform: str           # douyin / tiktok / youtube / bilibili
    content_type: str       # "video" or "post"
    external_id: str
    title: str
    author_name: str | None = None
    hashtags: list[str] = field(default_factory=list)
    view_count: int = 0
    like_count: int | None = None
    comment_count: int | None = None
    url: str | None = None
    posted_at: datetime | None = None
    metadata: dict = field(default_factory=dict)


class SocialDepthScraper(BaseScraper):
    """Wrapper that dispatches to per-platform fetchers.

    Call .fetch_all(keyword) to fan-out across every configured platform.
    Individual .fetch_<platform>() methods can also be called directly.
    """

    platform = "social_depth"
    rate_limit = 1.0  # per-request throttle, jittered by BaseScraper

    def __init__(self, proxy_url: str | None = None):
        super().__init__(proxy_url)
        self.tikhub_key = os.environ.get("TIKHUB_API_KEY", "").strip() or None
        self.youtube_key = os.environ.get("YOUTUBE_API_KEY", "").strip() or None

    # ------------------------------------------------------------------
    # Douyin (via TikHub)
    # ------------------------------------------------------------------
    async def fetch_douyin(
        self, keyword: str, limit: int = 20
    ) -> list[SocialContent]:
        """TikHub Douyin general search endpoint."""
        if not self.tikhub_key:
            logger.debug("[douyin] No TIKHUB_API_KEY — skipping.")
            return []

        client = await self.get_client()
        url = "https://api.tikhub.io/api/v1/douyin/web/fetch_general_search"
        try:
            await self.throttle()
            resp = await client.get(
                url,
                params={"keyword": keyword, "offset": 0, "count": limit},
                headers={"Authorization": f"Bearer {self.tikhub_key}"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            results = self._parse_douyin_response(data, keyword)
            logger.info(f"[douyin] '{keyword}' → {len(results)} items")
            return results
        except Exception as e:
            logger.warning(f"[douyin] fetch failed for '{keyword}': {e}")
            return []

    def _parse_douyin_response(
        self, data: dict, keyword: str
    ) -> list[SocialContent]:
        """Parse TikHub Douyin search response.

        TikHub response shape varies across tiers; tolerate both
        `data.data[]` and `items[]` layouts.
        """
        results: list[SocialContent] = []
        items = (
            (data.get("data") or {}).get("data")
            or data.get("items")
            or []
        )
        for item in items:
            try:
                aweme = item.get("aweme_info") or item
                if not aweme:
                    continue
                statistics = aweme.get("statistics") or {}
                text_extra = aweme.get("text_extra") or []
                hashtags = [
                    t.get("hashtag_name", "")
                    for t in text_extra
                    if t.get("type") == 1 and t.get("hashtag_name")
                ]
                author = aweme.get("author") or {}
                create_time = aweme.get("create_time")
                posted_at = (
                    datetime.fromtimestamp(create_time)
                    if create_time
                    else None
                )
                results.append(
                    SocialContent(
                        platform="douyin",
                        content_type="video",
                        external_id=str(aweme.get("aweme_id", "")),
                        title=(aweme.get("desc") or "").strip()[:500],
                        author_name=author.get("nickname"),
                        hashtags=hashtags[:10],
                        view_count=int(statistics.get("play_count", 0) or 0),
                        like_count=int(statistics.get("digg_count", 0) or 0),
                        comment_count=int(
                            statistics.get("comment_count", 0) or 0
                        ),
                        url=aweme.get("share_url"),
                        posted_at=posted_at,
                        metadata={"keyword": keyword, "source": "tikhub"},
                    )
                )
            except Exception as e:
                logger.debug(f"[douyin] skipping malformed item: {e}")
        return results

    # ------------------------------------------------------------------
    # TikTok (via TikHub)
    # ------------------------------------------------------------------
    async def fetch_tiktok(
        self, keyword: str, limit: int = 20
    ) -> list[SocialContent]:
        """TikHub TikTok general search endpoint."""
        if not self.tikhub_key:
            logger.debug("[tiktok] No TIKHUB_API_KEY — skipping.")
            return []

        client = await self.get_client()
        url = "https://api.tikhub.io/api/v1/tiktok/web/fetch_general_search_v2"
        try:
            await self.throttle()
            resp = await client.get(
                url,
                params={"keyword": keyword, "offset": 0, "count": limit},
                headers={"Authorization": f"Bearer {self.tikhub_key}"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            results = self._parse_tiktok_response(data, keyword)
            logger.info(f"[tiktok] '{keyword}' → {len(results)} items")
            return results
        except Exception as e:
            logger.warning(f"[tiktok] fetch failed for '{keyword}': {e}")
            return []

    def _parse_tiktok_response(
        self, data: dict, keyword: str
    ) -> list[SocialContent]:
        """Parse TikHub TikTok search response.

        TikTok items usually live at data.data[].item, with statistics
        at aweme.statistics — same layout as Douyin but camelCase fields.
        """
        results: list[SocialContent] = []
        items = (
            (data.get("data") or {}).get("data")
            or data.get("items")
            or []
        )
        for item in items:
            try:
                aweme = item.get("item") or item.get("aweme_info") or item
                if not aweme:
                    continue
                statistics = (
                    aweme.get("stats")
                    or aweme.get("statistics")
                    or {}
                )
                author = aweme.get("author") or {}
                text_extra = aweme.get("textExtra") or aweme.get("text_extra") or []
                hashtags = [
                    t.get("hashtagName") or t.get("hashtag_name") or ""
                    for t in text_extra
                    if (t.get("hashtagName") or t.get("hashtag_name"))
                ]
                create_time = aweme.get("createTime") or aweme.get("create_time")
                posted_at = (
                    datetime.fromtimestamp(create_time)
                    if create_time
                    else None
                )
                external_id = str(aweme.get("id") or aweme.get("aweme_id") or "")
                author_name = (
                    author.get("uniqueId")
                    or author.get("unique_id")
                    or author.get("nickname")
                )
                results.append(
                    SocialContent(
                        platform="tiktok",
                        content_type="video",
                        external_id=external_id,
                        title=(aweme.get("desc") or "").strip()[:500],
                        author_name=author_name,
                        hashtags=hashtags[:10],
                        view_count=int(
                            statistics.get("playCount", 0)
                            or statistics.get("play_count", 0)
                            or 0
                        ),
                        like_count=int(
                            statistics.get("diggCount", 0)
                            or statistics.get("digg_count", 0)
                            or 0
                        ),
                        comment_count=int(
                            statistics.get("commentCount", 0)
                            or statistics.get("comment_count", 0)
                            or 0
                        ),
                        url=(
                            f"https://www.tiktok.com/@{author_name}/video/{external_id}"
                            if author_name and external_id
                            else None
                        ),
                        posted_at=posted_at,
                        metadata={"keyword": keyword, "source": "tikhub"},
                    )
                )
            except Exception as e:
                logger.debug(f"[tiktok] skipping malformed item: {e}")
        return results

    # ------------------------------------------------------------------
    # YouTube Data API v3
    # ------------------------------------------------------------------
    async def fetch_youtube(
        self, keyword: str, limit: int = 20
    ) -> list[SocialContent]:
        """YouTube Data API v3 — search.list + videos.list (for stats)."""
        if not self.youtube_key:
            logger.debug("[youtube] No YOUTUBE_API_KEY — skipping.")
            return []

        client = await self.get_client()
        try:
            await self.throttle()
            search_resp = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "part": "snippet",
                    "q": keyword,
                    "type": "video",
                    "maxResults": min(limit, 50),
                    "order": "viewCount",
                    "key": self.youtube_key,
                },
                timeout=15,
            )
            search_resp.raise_for_status()
            search_data = search_resp.json()

            video_ids = [
                item["id"]["videoId"]
                for item in search_data.get("items", [])
                if item.get("id", {}).get("videoId")
            ]
            if not video_ids:
                logger.info(f"[youtube] '{keyword}' → 0 items")
                return []

            # Second call for statistics (search doesn't return counts)
            await self.throttle()
            stats_resp = await client.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "part": "statistics,snippet",
                    "id": ",".join(video_ids),
                    "key": self.youtube_key,
                },
                timeout=15,
            )
            stats_resp.raise_for_status()
            stats_data = stats_resp.json()

            results: list[SocialContent] = []
            for item in stats_data.get("items", []):
                try:
                    snippet = item.get("snippet") or {}
                    stats = item.get("statistics") or {}
                    tags = snippet.get("tags") or []
                    published = snippet.get("publishedAt")
                    posted_at = (
                        datetime.fromisoformat(published.replace("Z", "+00:00"))
                        if published
                        else None
                    )
                    results.append(
                        SocialContent(
                            platform="youtube",
                            content_type="video",
                            external_id=item["id"],
                            title=(snippet.get("title") or "")[:500],
                            author_name=snippet.get("channelTitle"),
                            hashtags=[str(t) for t in tags[:10]],
                            view_count=int(stats.get("viewCount", 0) or 0),
                            like_count=(
                                int(stats["likeCount"])
                                if stats.get("likeCount")
                                else None
                            ),
                            comment_count=(
                                int(stats["commentCount"])
                                if stats.get("commentCount")
                                else None
                            ),
                            url=f"https://www.youtube.com/watch?v={item['id']}",
                            posted_at=posted_at,
                            metadata={
                                "keyword": keyword,
                                "description": (
                                    snippet.get("description") or ""
                                )[:500],
                            },
                        )
                    )
                except Exception as e:
                    logger.debug(f"[youtube] skipping malformed item: {e}")

            logger.info(f"[youtube] '{keyword}' → {len(results)} items")
            return results
        except Exception as e:
            logger.warning(f"[youtube] fetch failed for '{keyword}': {e}")
            return []

    # ------------------------------------------------------------------
    # Bilibili (public API, no auth)
    # ------------------------------------------------------------------
    async def fetch_bilibili(
        self, keyword: str, limit: int = 20
    ) -> list[SocialContent]:
        """Bilibili public search API. Referer header is required."""
        client = await self.get_client()
        try:
            await self.throttle()
            resp = await client.get(
                "https://api.bilibili.com/x/web-interface/search/type",
                params={
                    "search_type": "video",
                    "keyword": keyword,
                    "page": 1,
                    "pagesize": limit,
                    "order": "click",
                },
                headers={
                    "Referer": "https://search.bilibili.com/",
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            results: list[SocialContent] = []
            items = ((data.get("data") or {}).get("result") or [])[:limit]
            for item in items:
                try:
                    # Strip Bilibili's <em class="keyword">…</em> highlights.
                    raw_title = html.unescape(item.get("title") or "")
                    title = (
                        raw_title.replace('<em class="keyword">', "")
                        .replace("</em>", "")
                        .strip()
                    )
                    bvid = item.get("bvid") or str(item.get("id") or "")
                    tag_str = item.get("tag") or ""
                    hashtags = [
                        t.strip()
                        for t in tag_str.split(",")
                        if t.strip()
                    ][:10]
                    pubdate = item.get("pubdate")
                    posted_at = (
                        datetime.fromtimestamp(pubdate) if pubdate else None
                    )
                    results.append(
                        SocialContent(
                            platform="bilibili",
                            content_type="video",
                            external_id=bvid,
                            title=title[:500],
                            author_name=item.get("author"),
                            hashtags=hashtags,
                            view_count=int(item.get("play", 0) or 0),
                            like_count=int(item.get("like", 0) or 0),
                            comment_count=int(item.get("review", 0) or 0),
                            url=f"https://www.bilibili.com/video/{bvid}" if bvid else None,
                            posted_at=posted_at,
                            metadata={
                                "keyword": keyword,
                                "description": (
                                    item.get("description") or ""
                                )[:500],
                            },
                        )
                    )
                except Exception as e:
                    logger.debug(f"[bilibili] skipping malformed item: {e}")

            logger.info(f"[bilibili] '{keyword}' → {len(results)} items")
            return results
        except Exception as e:
            logger.warning(f"[bilibili] fetch failed for '{keyword}': {e}")
            return []

    # ------------------------------------------------------------------
    # Fan-out across all platforms
    # ------------------------------------------------------------------
    async def fetch_all(
        self,
        keyword: str,
        limit_per_platform: int = 20,
        *,
        name_zh: str | None = None,
        name_en: str | None = None,
    ) -> list[SocialContent]:
        """Fan-out fetch across all configured platforms.

        Douyin and Bilibili search in Chinese (prefer name_zh, fall back to
        name_en). YouTube and TikTok search in English (prefer name_en,
        fall back to name_zh). If only `keyword` is supplied we use it
        everywhere — older callers keep working.

        One platform failing does not block the others.
        """
        # Resolve per-platform search terms.
        zh_keyword = (name_zh or name_en or keyword or "").strip()
        en_keyword = (name_en or name_zh or keyword or "").strip()

        tasks = []
        if zh_keyword:
            tasks.append(self.fetch_douyin(zh_keyword, limit_per_platform))
            tasks.append(self.fetch_bilibili(zh_keyword, limit_per_platform))
        if en_keyword:
            tasks.append(self.fetch_tiktok(en_keyword, limit_per_platform))
            tasks.append(self.fetch_youtube(en_keyword, limit_per_platform))

        all_results: list[SocialContent] = []
        for coro in asyncio.as_completed(tasks):
            try:
                all_results.extend(await coro)
            except Exception as e:
                logger.warning(f"[social_depth] one platform failed: {e}")
        logger.info(
            f"[social_depth] zh='{zh_keyword}' en='{en_keyword}' → {len(all_results)} items"
        )
        return all_results

    # ------------------------------------------------------------------
    # BaseScraper interface — not used for this scraper
    # ------------------------------------------------------------------
    async def scrape_rankings(
        self, chart_type: str, region: str = "CN"
    ) -> list[RankingEntry]:
        return []

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        return None


__all__ = ["SocialContent", "SocialDepthScraper"]
