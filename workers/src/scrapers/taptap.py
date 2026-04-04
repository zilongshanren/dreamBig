"""TapTap scraper using their web API endpoints."""

from __future__ import annotations

import logging
import re

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)

# TapTap web API endpoints
TAPTAP_TOP_URL = "https://www.taptap.cn/webapiv2/app-top/v2/hits"
TAPTAP_DETAIL_URL = "https://www.taptap.cn/webapiv2/app/v4/detail"

# Chart types available on TapTap
CHART_TYPE_MAP = {
    "hot": "hot",       # 热门榜
    "new": "new",       # 新品榜
    "reserve": "reserve",  # 预约榜
    "sell": "sell",     # 热卖榜
}

# X-UA header is mandatory for TapTap API
TAPTAP_HEADERS = {
    "X-UA": "V=1&PN=WebApp&LANG=zh_CN&VN_CODE=102&LOC=CN&PLT=PC&DS=Android&UID=0&DT=PC&OS=Windows&OSV=10",
}


class TapTapScraper(BaseScraper):
    platform = "taptap"
    rate_limit = 3.0

    async def scrape_rankings(
        self, chart_type: str = "hot", region: str = "CN"
    ) -> list[RankingEntry]:
        """Scrape TapTap rankings.

        TapTap has an internal API that returns JSON data.
        We use the ranking list endpoint with pagination.
        """
        client = await self.get_client()
        entries: list[RankingEntry] = []
        tt_type = CHART_TYPE_MAP.get(chart_type, "hot")

        # TapTap paginates with 'from' parameter, max 15 items per page
        for page_from in range(0, 150, 15):
            await self.throttle()

            params = {
                "type_name": tt_type,
                "from": page_from,
                "limit": 15,
            }
            # Genre-specific rankings require platform param
            if tt_type in ("new", "action", "strategy", "idle", "casual", "roguelike"):
                params["platform"] = "android"

            try:
                resp = await client.get(
                    TAPTAP_TOP_URL, params=params, headers=TAPTAP_HEADERS
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning(f"[taptap] Page {page_from} failed: {e}")
                break

            items = data.get("data", {}).get("list", [])
            if not items:
                break

            for item in items:
                app_info = item.get("app", {})
                if not app_info:
                    continue

                app_id = str(app_info.get("id", ""))
                name = app_info.get("title", "")
                developer_info = app_info.get("developers", [{}])
                developer = developer_info[0].get("name") if developer_info else None

                # Rating on TapTap
                stat = app_info.get("stat", {})
                rating = stat.get("rating", {}).get("score")
                rating_count = stat.get("rating", {}).get("count")
                hits = stat.get("hits")

                # Icon
                icon = app_info.get("icon", {})
                icon_url = icon.get("original_url") or icon.get("url")

                # Genre tags
                tags = app_info.get("tags", [])
                genre = tags[0].get("value") if tags else None

                rank_position = page_from + len(entries) - (page_from // 20 * 0) + 1
                # Actually let's track the position from the rank field if available
                rank_val = item.get("rank", len(entries) + 1)

                entries.append(
                    RankingEntry(
                        platform_id=app_id,
                        name=name,
                        rank_position=rank_val,
                        chart_type=chart_type,
                        region="CN",
                        rating=rating,
                        rating_count=rating_count,
                        download_est=hits,
                        developer=developer,
                        genre=genre,
                        icon_url=icon_url,
                        url=f"https://www.taptap.cn/app/{app_id}",
                        metadata={
                            "tags": [t.get("value") for t in tags],
                            "follow_count": stat.get("fans_count"),
                        },
                    )
                )

        return entries

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Scrape detailed game info from TapTap."""
        client = await self.get_client()
        params = {
            "id": platform_id,
        }

        try:
            resp = await client.get(
                TAPTAP_DETAIL_URL, params=params, headers=TAPTAP_HEADERS
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"[taptap] Detail failed for {platform_id}: {e}")
            return None

        app = data.get("data", {})
        if not app:
            return None

        # Parse developer
        developers = app.get("developers", [])
        developer = developers[0].get("name") if developers else None

        # Parse tags/genres
        tags = app.get("tags", [])
        tag_names = [t.get("value", "") for t in tags]
        genre = tag_names[0] if tag_names else None

        # Parse rating
        stat = app.get("stat", {})
        rating_info = stat.get("rating", {})
        rating = rating_info.get("score")
        rating_count = rating_info.get("count")

        # Icon
        icon = app.get("icon", {})
        icon_url = icon.get("original_url") or icon.get("url")

        # Screenshots
        screenshots = []
        for ss in app.get("screenshots", [])[:5]:
            ss_url = ss.get("original_url") or ss.get("url")
            if ss_url:
                screenshots.append(ss_url)

        return GameDetails(
            platform_id=str(platform_id),
            name=app.get("title", ""),
            developer=developer,
            description=app.get("description", {}).get("text", "")[:500],
            genre=genre,
            sub_genres=tag_names[1:4],
            rating=rating,
            rating_count=rating_count,
            icon_url=icon_url,
            screenshots=screenshots,
            url=f"https://www.taptap.cn/app/{platform_id}",
            metadata={
                "tags": tag_names,
                "follow_count": stat.get("fans_count"),
                "download_count": stat.get("hits"),
            },
        )
