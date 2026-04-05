"""Google Play review scraper using the google-play-scraper library.

Uses the internal `reviews()` function from the google-play-scraper
package, which wraps Play Store's review API endpoints.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ...utils.lang_detect import detect_language, normalize_lang_code
from .base import BaseReviewScraper, ReviewEntry

logger = logging.getLogger(__name__)


# Google Play country codes are lowercase 2-letter
REGION_MAP = {
    "US": "us",
    "JP": "jp",
    "KR": "kr",
    "TW": "tw",
    "GB": "gb",
    "DE": "de",
    "BR": "br",
    "IN": "in",
    "CN": "us",  # Play Store not available in CN
}


def _to_datetime(raw) -> datetime:
    """Normalize Google Play's `at` field to a datetime.

    The library returns a datetime already in most cases, but some
    versions return a timestamp int. Defensive coding here.
    """
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now()


class GooglePlayReviewScraper(BaseReviewScraper):
    platform = "google_play"
    rate_limit = 2.0

    async def scrape_reviews(
        self,
        platform_id: str,
        region: str = "US",
        limit: int = 200,
        lang: str = "en",
    ) -> list[ReviewEntry]:
        """Scrape the most recent Google Play reviews for an app.

        Uses `google_play_scraper.reviews()` with `Sort.NEWEST`. The
        underlying library is synchronous, so we offload to a thread
        to keep the event loop responsive.
        """
        # google-play-scraper is a sync lib — run in a thread
        try:
            from google_play_scraper import Sort, reviews
        except ImportError as e:
            logger.error(f"[google_play] google_play_scraper not installed: {e}")
            return []

        country = REGION_MAP.get(region, region.lower())

        def _fetch():
            # Returns (list_of_review_dicts, continuation_token)
            result, _ = reviews(
                platform_id,
                lang=lang,
                country=country,
                sort=Sort.NEWEST,
                count=limit,
            )
            return result

        try:
            raw_reviews = await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.error(
                f"[google_play] reviews() failed for {platform_id} "
                f"({country}/{lang}): {e}"
            )
            return []

        entries: list[ReviewEntry] = []
        for r in raw_reviews or []:
            try:
                content = (r.get("content") or "").strip()
                if not content:
                    continue

                review_id = str(r.get("reviewId") or "")
                if not review_id:
                    continue

                score = r.get("score")
                rating = int(score) if score is not None else None

                detected_lang = (
                    detect_language(content) or normalize_lang_code(lang)
                )

                entries.append(
                    ReviewEntry(
                        external_id=review_id,
                        rating=rating,
                        content=content,
                        author_name=r.get("userName") or None,
                        helpful_count=r.get("thumbsUpCount") or 0,
                        language=detected_lang,
                        posted_at=_to_datetime(r.get("at")),
                        metadata={
                            "app_version": r.get("reviewCreatedVersion") or r.get("appVersion"),
                            "reply_content": r.get("replyContent"),
                            "reply_at": (
                                _to_datetime(r.get("repliedAt")).isoformat()
                                if r.get("repliedAt")
                                else None
                            ),
                            "user_image": r.get("userImage"),
                        },
                    )
                )
            except Exception as e:
                logger.warning(f"[google_play] skipped malformed review: {e}")
                continue

        return entries
