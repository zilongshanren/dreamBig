"""Steam review scraper using the public Steam appreviews endpoint.

Endpoint docs (community-reversed):
    https://partner.steamgames.com/doc/store/getreviews
    GET https://store.steampowered.com/appreviews/{appid}?json=1

Pagination is cursor-based: the response contains a `cursor` string
which must be passed as the `cursor=` query param on the next call.
The first call uses `cursor=*`.

Steam reviews are binary (thumbs up/down) not star-rated, so we
normalize `voted_up=True -> 5` and `voted_up=False -> 1` for
the 0-5 ReviewEntry.rating scale.
"""

from __future__ import annotations

import asyncio
import logging
import random
import urllib.parse
from datetime import datetime

from .base import BaseReviewScraper, ReviewEntry

logger = logging.getLogger(__name__)

STEAM_REVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"

# Steam language codes differ slightly from ISO; map common ones
LANG_MAP = {
    "en": "english",
    "zh": "schinese",
    "zh-cn": "schinese",
    "zh-tw": "tchinese",
    "ja": "japanese",
    "ko": "korean",
    "de": "german",
    "fr": "french",
    "es": "spanish",
    "pt": "portuguese",
    "pt-br": "brazilian",
    "ru": "russian",
}

PAGE_SIZE = 100  # Steam max per page
PAGE_DELAY = 1.5  # seconds between pages (per task spec)


class SteamReviewScraper(BaseReviewScraper):
    platform = "steam"
    rate_limit = 1.5

    async def scrape_reviews(
        self,
        platform_id: str,
        region: str = "US",
        limit: int = 200,
        lang: str = "en",
    ) -> list[ReviewEntry]:
        """Scrape recent Steam reviews for an appid via cursor pagination.

        Steam's endpoint is global (no region param for reviews
        themselves — `region` is accepted for interface consistency
        but unused). Language filtering uses Steam's own vocabulary
        (english, schinese, etc.), mapped from ISO codes.
        """
        client = await self.get_client()
        steam_lang = LANG_MAP.get(lang.lower(), lang.lower() or "all")
        cursor = "*"
        url = STEAM_REVIEWS_URL.format(appid=platform_id)

        entries: list[ReviewEntry] = []
        seen_cursors: set[str] = set()
        pages_fetched = 0
        max_pages = max(1, (limit + PAGE_SIZE - 1) // PAGE_SIZE)

        while len(entries) < limit and pages_fetched < max_pages:
            params = {
                "json": "1",
                "filter": "recent",
                "language": steam_lang,
                "num_per_page": str(PAGE_SIZE),
                "cursor": cursor,
                "purchase_type": "all",
            }

            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(
                    f"[steam] appreviews fetch failed for {platform_id} "
                    f"(cursor={cursor}): {e}"
                )
                break

            if not data.get("success"):
                logger.warning(
                    f"[steam] appreviews returned success=0 for {platform_id}"
                )
                break

            raw_reviews = data.get("reviews", []) or []
            if not raw_reviews:
                break

            for r in raw_reviews:
                if len(entries) >= limit:
                    break
                try:
                    content = (r.get("review") or "").strip()
                    if not content:
                        continue

                    review_id = str(r.get("recommendationid") or "")
                    if not review_id:
                        continue

                    voted_up = bool(r.get("voted_up"))
                    rating = 5 if voted_up else 1

                    author = r.get("author") or {}
                    author_name = str(author.get("steamid") or "") or None

                    ts_created = r.get("timestamp_created")
                    posted_at = (
                        datetime.fromtimestamp(int(ts_created))
                        if ts_created
                        else datetime.now()
                    )

                    entries.append(
                        ReviewEntry(
                            external_id=review_id,
                            rating=rating,
                            content=content,
                            author_name=author_name,
                            helpful_count=int(r.get("votes_up") or 0),
                            language=r.get("language") or steam_lang,
                            posted_at=posted_at,
                            metadata={
                                "voted_up": voted_up,
                                "votes_funny": r.get("votes_funny"),
                                "weighted_vote_score": r.get("weighted_vote_score"),
                                "comment_count": r.get("comment_count"),
                                "steam_purchase": r.get("steam_purchase"),
                                "received_for_free": r.get("received_for_free"),
                                "written_during_early_access": r.get(
                                    "written_during_early_access"
                                ),
                                "playtime_forever": author.get("playtime_forever"),
                                "playtime_at_review": author.get("playtime_at_review"),
                                "num_games_owned": author.get("num_games_owned"),
                                "num_reviews": author.get("num_reviews"),
                            },
                        )
                    )
                except Exception as e:
                    logger.warning(f"[steam] skipped malformed review: {e}")
                    continue

            # Advance cursor; cursors must be URL-encoded when sent back
            next_cursor_raw = data.get("cursor")
            if not next_cursor_raw or next_cursor_raw in seen_cursors:
                # Steam repeats the last cursor when no more pages
                break
            seen_cursors.add(next_cursor_raw)
            cursor = urllib.parse.quote(next_cursor_raw, safe="")
            pages_fetched += 1

            # Steam asks for ~1.5s between pages; add jitter
            if len(entries) < limit and pages_fetched < max_pages:
                await asyncio.sleep(PAGE_DELAY * random.uniform(0.9, 1.2))

        return entries[:limit]
