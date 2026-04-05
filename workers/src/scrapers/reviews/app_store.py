"""Apple App Store review scraper via the iTunes Customer Reviews RSS feed.

Endpoint:
    https://itunes.apple.com/{region}/rss/customerreviews/page={N}/id={app_id}/sortby=mostrecent/json

The RSS feed is paginated (1-10) with ~50 reviews per page, giving a
practical ceiling of ~500 recent reviews per app. The first entry in
each feed is the app itself (metadata), not a review — we skip it.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .base import BaseReviewScraper, ReviewEntry

logger = logging.getLogger(__name__)

RSS_URL = (
    "https://itunes.apple.com/{region}/rss/customerreviews/"
    "page={page}/id={app_id}/sortby=mostrecent/json"
)

MAX_PAGES = 10  # iTunes RSS caps at page 10
PAGE_SIZE_HINT = 50  # approximate

REGION_MAP = {
    "CN": "cn",
    "US": "us",
    "JP": "jp",
    "KR": "kr",
    "TW": "tw",
    "GB": "gb",
    "DE": "de",
    "BR": "br",
    "IN": "in",
}


def _label(field) -> str:
    """Extract the .label value from an iTunes RSS field safely."""
    if isinstance(field, dict):
        return field.get("label", "") or ""
    if isinstance(field, str):
        return field
    return ""


def _parse_datetime(s: str) -> datetime:
    """Parse iTunes RSS ISO-8601 timestamps like 2024-03-15T07:42:11-07:00."""
    if not s:
        return datetime.now()
    try:
        # Python can parse the -07:00 offset form in 3.11+
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return datetime.now()


class AppStoreReviewScraper(BaseReviewScraper):
    platform = "app_store"
    rate_limit = 1.5

    async def scrape_reviews(
        self,
        platform_id: str,
        region: str = "US",
        limit: int = 200,
        lang: str = "en",
    ) -> list[ReviewEntry]:
        """Scrape most-recent App Store reviews via the iTunes RSS feed.

        `lang` is accepted for API consistency but ignored — iTunes
        RSS returns reviews for whichever language a user wrote in
        the given region's store. `region` maps to storefronts
        (us, cn, jp, ...).
        """
        client = await self.get_client()
        region_code = REGION_MAP.get(region.upper(), region.lower()) or "us"

        entries: list[ReviewEntry] = []
        max_pages = min(MAX_PAGES, max(1, (limit + PAGE_SIZE_HINT - 1) // PAGE_SIZE_HINT))

        for page in range(1, max_pages + 1):
            if len(entries) >= limit:
                break

            url = RSS_URL.format(region=region_code, page=page, app_id=platform_id)
            try:
                resp = await client.get(url)
                # 404 on first page = unknown app; later pages = end of feed
                if resp.status_code == 404:
                    if page == 1:
                        logger.warning(
                            f"[app_store] 404 for {platform_id} in {region_code}"
                        )
                    break
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(
                    f"[app_store] RSS page {page} failed for {platform_id}: {e}"
                )
                break

            feed = data.get("feed", {}) or {}
            raw_entries = feed.get("entry", []) or []
            if not raw_entries:
                break

            # Page 1's first entry is the app metadata, not a review
            start_idx = 1 if page == 1 and len(raw_entries) > 0 else 0
            page_reviews = raw_entries[start_idx:]

            if not page_reviews:
                break

            added_this_page = 0
            for e in page_reviews:
                if len(entries) >= limit:
                    break
                try:
                    # review id
                    id_field = e.get("id", {})
                    review_id = _label(id_field)
                    if not review_id:
                        continue

                    # rating
                    rating_raw = _label(e.get("im:rating"))
                    try:
                        rating = int(rating_raw) if rating_raw else None
                    except ValueError:
                        rating = None

                    # content text
                    content = _label(e.get("content")).strip()
                    if not content:
                        continue

                    # author name
                    author_name = None
                    author_field = e.get("author", {})
                    if isinstance(author_field, dict):
                        author_name = _label(author_field.get("name")) or None

                    # helpful / vote count
                    helpful_raw = _label(e.get("im:voteCount"))
                    try:
                        helpful_count = int(helpful_raw) if helpful_raw else 0
                    except ValueError:
                        helpful_count = 0

                    # timestamp
                    posted_at = _parse_datetime(_label(e.get("updated")))

                    # additional metadata
                    title = _label(e.get("title"))
                    version = _label(e.get("im:version"))

                    entries.append(
                        ReviewEntry(
                            external_id=review_id,
                            rating=rating,
                            content=content,
                            author_name=author_name,
                            helpful_count=helpful_count,
                            language=lang,
                            posted_at=posted_at,
                            metadata={
                                "title": title,
                                "app_version": version,
                                "vote_sum": _label(e.get("im:voteSum")) or None,
                                "region": region_code,
                            },
                        )
                    )
                    added_this_page += 1
                except Exception as err:
                    logger.warning(f"[app_store] skipped malformed review: {err}")
                    continue

            # If page returned nothing usable, stop paginating
            if added_this_page == 0:
                break

        return entries[:limit]
