"""TapTap review scraper.

TapTap exposes a webapiv2 review endpoint that returns JSON without
needing a full browser. The JSON path is far more reliable than DOM
scraping, so we try that first. If the JSON API is blocked or returns
nothing usable, we fall back to Playwright DOM scraping.

The Playwright fallback selectors below are best-effort — TapTap's
frontend uses CSS Modules with hashed class names, so the selectors
may drift. Inspect and adjust if reviews stop returning.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from .base import BaseReviewScraper, ReviewEntry

logger = logging.getLogger(__name__)

# Known TapTap webapiv2 review endpoint. This is the same base
# used by the rankings scraper and has worked historically for
# public review pages.
TAPTAP_REVIEW_URL = "https://www.taptap.cn/webapiv2/review/v1/by-app"

# Mandatory X-UA header for any webapiv2 call
TAPTAP_HEADERS = {
    "X-UA": (
        "V=1&PN=WebApp&LANG=zh_CN&VN_CODE=102&LOC=CN&PLT=PC"
        "&DS=Android&UID=0&DT=PC&OS=Windows&OSV=10"
    ),
}

REVIEWS_PER_PAGE = 10  # TapTap returns ~10 reviews per page

# Playwright fallback: review page URL pattern
TAPTAP_REVIEW_PAGE = "https://www.taptap.cn/app/{app_id}/review"


class TapTapReviewScraper(BaseReviewScraper):
    platform = "taptap"
    rate_limit = 2.0

    async def scrape_reviews(
        self,
        platform_id: str,
        region: str = "CN",
        limit: int = 200,
        lang: str = "zh",
    ) -> list[ReviewEntry]:
        """Scrape TapTap reviews.

        Primary path: hit the webapiv2/review/v1/by-app JSON endpoint
        with cursor pagination.
        Fallback path: render the review page with Playwright and
        parse DOM nodes.
        """
        # Try the JSON API first — fast, stable, no browser required
        entries = await self._scrape_via_json_api(platform_id, limit)
        if entries:
            return entries

        logger.warning(
            f"[taptap] JSON API returned 0 reviews for {platform_id}, "
            "falling back to Playwright DOM scraping"
        )
        return await self._scrape_via_playwright(platform_id, limit)

    async def _scrape_via_json_api(
        self, app_id: str, limit: int
    ) -> list[ReviewEntry]:
        """Primary: call webapiv2 review endpoint with pagination."""
        client = await self.get_client()
        entries: list[ReviewEntry] = []

        page_from = 0
        max_pages = max(1, (limit + REVIEWS_PER_PAGE - 1) // REVIEWS_PER_PAGE)
        pages_done = 0

        while len(entries) < limit and pages_done < max_pages:
            params = {
                "app_id": app_id,
                "from": page_from,
                "limit": REVIEWS_PER_PAGE,
                "sort": "new",  # newest first
            }

            try:
                resp = await client.get(
                    TAPTAP_REVIEW_URL, params=params, headers=TAPTAP_HEADERS
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning(
                    f"[taptap] JSON review page from={page_from} failed: {e}"
                )
                break

            items = data.get("data", {}).get("list", []) or []
            if not items:
                break

            added_this_page = 0
            for item in items:
                if len(entries) >= limit:
                    break

                # `moment` often wraps the actual review content object
                moment = item.get("moment") or item
                review = (
                    moment.get("review")
                    or moment.get("topic")
                    or item.get("review")
                    or item
                )

                try:
                    review_id = str(
                        review.get("id") or moment.get("id") or item.get("id") or ""
                    )
                    if not review_id:
                        continue

                    # Content may be in review.contents.text or similar
                    content = ""
                    contents = review.get("contents") or {}
                    if isinstance(contents, dict):
                        content = (contents.get("text") or contents.get("raw") or "").strip()
                    if not content:
                        content = (
                            review.get("content")
                            or review.get("text")
                            or moment.get("text")
                            or ""
                        ).strip()
                    if not content:
                        continue

                    # Star rating out of 5
                    rating_raw = (
                        review.get("score")
                        or review.get("rating")
                        or review.get("star")
                    )
                    try:
                        rating = int(rating_raw) if rating_raw is not None else None
                    except (TypeError, ValueError):
                        rating = None

                    # Author
                    author = (
                        review.get("author")
                        or moment.get("author")
                        or item.get("author")
                        or {}
                    )
                    author_name = None
                    if isinstance(author, dict):
                        author_name = (
                            author.get("name")
                            or author.get("nickname")
                            or author.get("username")
                        )

                    # Helpful / upvotes
                    helpful_count = 0
                    stat = review.get("stat") or moment.get("stat") or {}
                    if isinstance(stat, dict):
                        helpful_count = int(
                            stat.get("ups")
                            or stat.get("up_count")
                            or stat.get("like_count")
                            or 0
                        )
                    if not helpful_count:
                        helpful_count = int(
                            review.get("ups")
                            or review.get("up_count")
                            or review.get("like_count")
                            or 0
                        )

                    # Posted timestamp
                    ts = (
                        review.get("published_time")
                        or review.get("created_time")
                        or moment.get("created_time")
                        or item.get("created_time")
                    )
                    posted_at = (
                        datetime.fromtimestamp(int(ts)) if ts else datetime.now()
                    )

                    device = review.get("device") or moment.get("device")

                    entries.append(
                        ReviewEntry(
                            external_id=review_id,
                            rating=rating,
                            content=content,
                            author_name=author_name,
                            helpful_count=helpful_count,
                            language="zh",
                            posted_at=posted_at,
                            metadata={
                                "device": device,
                                "spent": review.get("spent"),
                                "played_spent": review.get("played_spent"),
                                "updated_time": review.get("updated_time"),
                            },
                        )
                    )
                    added_this_page += 1
                except Exception as e:
                    logger.warning(f"[taptap] skipped malformed review: {e}")
                    continue

            if added_this_page == 0:
                break

            page_from += REVIEWS_PER_PAGE
            pages_done += 1

            # throttle between pages to stay polite
            if len(entries) < limit and pages_done < max_pages:
                await asyncio.sleep(self.rate_limit)

        return entries[:limit]

    async def _scrape_via_playwright(
        self, app_id: str, limit: int
    ) -> list[ReviewEntry]:
        """Fallback: render the review page and scrape the DOM.

        This is a best-effort implementation. TapTap's frontend uses
        CSS Modules, so class names are hashed and change between
        builds. The selectors below may need updating — check them
        against the live page if this scraper starts returning 0
        reviews.

        Approach:
          1. Open /app/{app_id}/review
          2. Wait for review list container to render
          3. Scroll to bottom repeatedly to trigger lazy-loading
          4. Read each review card's sub-elements

        If Playwright is unavailable or the page structure has
        changed beyond repair, raise NotImplementedError so the
        circuit breaker records the failure.
        """
        try:
            from ...utils.browser import get_page
        except ImportError as e:
            logger.error(f"[taptap] browser utility unavailable: {e}")
            raise NotImplementedError(
                "TapTap review scraping requires the Playwright browser "
                "utility at workers/src/utils/browser.py"
            ) from e

        url = TAPTAP_REVIEW_PAGE.format(app_id=app_id)
        entries: list[ReviewEntry] = []

        try:
            async with get_page(headless=True, locale="zh-CN") as page:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                # Wait for any review card to render. Selector is
                # best-effort: TapTap uses data-* attributes on
                # review items in some builds, class names in others.
                # Adjust these if scraping stops working:
                review_card_selectors = [
                    '[data-e2e="review-item"]',
                    '[class*="review-item"]',
                    '[class*="ReviewItem"]',
                    'article[class*="review"]',
                ]

                card_selector = None
                for sel in review_card_selectors:
                    try:
                        await page.wait_for_selector(sel, timeout=5_000)
                        card_selector = sel
                        break
                    except Exception:
                        continue

                if not card_selector:
                    logger.error(
                        f"[taptap] could not locate review cards on {url}; "
                        "all candidate selectors failed"
                    )
                    raise NotImplementedError(
                        "TapTap review DOM selectors are out of date; "
                        "update _scrape_via_playwright()"
                    )

                # Scroll to load more reviews until we have enough
                prev_count = 0
                stall_iterations = 0
                max_scrolls = max(10, (limit // 10) * 2)
                for _ in range(max_scrolls):
                    await page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                    await asyncio.sleep(1.5)
                    cards = await page.query_selector_all(card_selector)
                    if len(cards) >= limit:
                        break
                    if len(cards) == prev_count:
                        stall_iterations += 1
                        if stall_iterations >= 3:
                            break  # no more content loading
                    else:
                        stall_iterations = 0
                    prev_count = len(cards)

                cards = await page.query_selector_all(card_selector)

                # The following sub-selectors are all best-effort —
                # inspect page.content() in a browser and update if
                # needed. Each uses multiple fallbacks.
                for i, card in enumerate(cards[:limit]):
                    try:
                        # review id: try data-id attribute then href fragment
                        rid = await card.get_attribute("data-review-id")
                        if not rid:
                            rid = await card.get_attribute("data-id")
                        if not rid:
                            # fall back to positional id
                            rid = f"{app_id}-dom-{i}"

                        # content text
                        content_node = await card.query_selector(
                            '[class*="review-content"], [class*="ReviewContent"], '
                            '[class*="review-item-text"]'
                        )
                        content = (
                            (await content_node.inner_text()).strip()
                            if content_node
                            else ""
                        )
                        if not content:
                            continue

                        # star rating: count filled stars or read aria-label
                        rating = None
                        rating_node = await card.query_selector(
                            '[class*="star"], [class*="Star"]'
                        )
                        if rating_node:
                            aria = await rating_node.get_attribute("aria-label")
                            if aria:
                                # aria-label like "5 分" or "5 stars"
                                digits = "".join(c for c in aria if c.isdigit())
                                if digits:
                                    try:
                                        rating = int(digits[0])
                                    except ValueError:
                                        rating = None

                        # author name
                        author_node = await card.query_selector(
                            '[class*="author-name"], [class*="user-name"], '
                            '[class*="UserName"]'
                        )
                        author_name = (
                            (await author_node.inner_text()).strip()
                            if author_node
                            else None
                        )

                        # helpful count
                        helpful_count = 0
                        like_node = await card.query_selector(
                            '[class*="like-count"], [class*="up-count"], '
                            '[class*="thumb"]'
                        )
                        if like_node:
                            like_text = (await like_node.inner_text()).strip()
                            digits = "".join(c for c in like_text if c.isdigit())
                            if digits:
                                try:
                                    helpful_count = int(digits)
                                except ValueError:
                                    helpful_count = 0

                        # posted time (relative like "3 天前" or absolute)
                        time_node = await card.query_selector("time, [class*='time']")
                        posted_at_attr = None
                        if time_node:
                            posted_at_attr = await time_node.get_attribute("datetime")
                        posted_at = (
                            datetime.fromisoformat(posted_at_attr.replace("Z", "+00:00"))
                            if posted_at_attr
                            else datetime.now()
                        )

                        entries.append(
                            ReviewEntry(
                                external_id=str(rid),
                                rating=rating,
                                content=content,
                                author_name=author_name,
                                helpful_count=helpful_count,
                                language="zh",
                                posted_at=posted_at,
                                metadata={"source": "playwright_dom"},
                            )
                        )
                    except Exception as e:
                        logger.warning(f"[taptap] skipped malformed DOM card: {e}")
                        continue

        except NotImplementedError:
            raise
        except Exception as e:
            logger.error(f"[taptap] Playwright scraping failed: {e}")
            return []

        return entries[:limit]
