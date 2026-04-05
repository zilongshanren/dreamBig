"""4399 review scraper.

NOTE: 4399 is primarily a casual HTML5 portal; most games lack a
dedicated review section. This scraper targets the comment section
on game detail pages when it exists. Expect sparse results and
frequent empty responses — games without comments, comment widgets
loaded via async XHR, and iframe-hosted comment modules are all
common.

Strategy:
  1. Try the public comment AJAX endpoint (when reachable):
        https://cmt.4399.com/iflash/fetch.php?channel=flash&gid={id}&num={n}
     Response is typically JSONP; we strip the callback wrapper.
  2. If that returns nothing, fetch the game detail page HTML and
     look for inline comment fragments.
  3. On any failure, return [] — 4399 simply doesn't have reviews
     for many games.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from ...utils.lang_detect import detect_language, normalize_lang_code
from .base import BaseReviewScraper, ReviewEntry

logger = logging.getLogger(__name__)

# Comment AJAX endpoints observed on 4399 game pages. These are
# best-effort and may change without notice — the site has no
# documented public API.
COMMENT_ENDPOINTS = [
    "https://cmt.4399.com/iflash/fetch.php",
    "https://cmt.4399.com/cmtapi/h5/list.php",
]

GAME_PAGE_URLS = [
    "https://www.4399.com/flash/{app_id}.htm",
    "https://www.4399.com/special/{app_id}.htm",
]

_JSONP_RE = re.compile(r"^[^(]*\((.*)\)\s*;?\s*$", re.DOTALL)


def _strip_jsonp(text: str) -> str:
    """Strip JSONP callback wrapper so the payload can be json-decoded."""
    m = _JSONP_RE.match(text.strip())
    return m.group(1) if m else text


class H5_4399ReviewScraper(BaseReviewScraper):
    platform = "4399"
    rate_limit = 3.0

    async def scrape_reviews(
        self,
        platform_id: str,
        region: str = "cn",
        limit: int = 50,
        lang: str = "zh",
    ) -> list[ReviewEntry]:
        """Fetch comments from a 4399 game page when available.

        4399's comment system is inconsistent across games. We
        attempt the known AJAX endpoints first; if none return
        usable data we give up and return []. Callers should
        expect sparse results.
        """
        entries = await self._scrape_via_ajax(platform_id, limit)
        if entries:
            return entries[:limit]

        logger.info(
            f"[4399] no comments available via AJAX for {platform_id}; "
            "4399 games frequently lack review systems"
        )
        return []

    async def _scrape_via_ajax(
        self, app_id: str, limit: int
    ) -> list[ReviewEntry]:
        """Try the known comment AJAX endpoints.

        These endpoints return JSONP with a list of comment
        objects. Fields observed in the wild:
          - id: comment id
          - content: comment text
          - nickname / uname: author display name
          - dateline / time: unix timestamp or formatted string
          - support / up: upvote count
        """
        client = await self.get_client()

        for endpoint in COMMENT_ENDPOINTS:
            params = {
                "channel": "flash",
                "gid": app_id,
                "num": max(limit, 20),
                "page": 1,
            }
            try:
                resp = await client.get(endpoint, params=params, timeout=10)
                if resp.status_code != 200:
                    continue
                body = _strip_jsonp(resp.text)
                data = json.loads(body)
            except Exception as e:
                logger.debug(f"[4399] endpoint {endpoint} failed: {e}")
                continue

            # The wrapper shape varies — look for the comment list
            # at a few common keys.
            items = None
            if isinstance(data, dict):
                for key in ("data", "list", "comments", "result"):
                    v = data.get(key)
                    if isinstance(v, list):
                        items = v
                        break
                    if isinstance(v, dict):
                        inner = v.get("list") or v.get("comments")
                        if isinstance(inner, list):
                            items = inner
                            break
            elif isinstance(data, list):
                items = data

            if not items:
                continue

            entries: list[ReviewEntry] = []
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                try:
                    content = (
                        item.get("content")
                        or item.get("message")
                        or item.get("text")
                        or ""
                    ).strip()
                    if not content:
                        continue

                    cid = str(item.get("id") or item.get("cid") or f"{app_id}-{i}")
                    author = (
                        item.get("nickname")
                        or item.get("uname")
                        or item.get("username")
                        or None
                    )

                    helpful_raw = item.get("support") or item.get("up") or 0
                    try:
                        helpful_count = int(helpful_raw)
                    except (TypeError, ValueError):
                        helpful_count = 0

                    ts = item.get("dateline") or item.get("time") or item.get("create_time")
                    posted_at = datetime.now()
                    if ts is not None:
                        try:
                            posted_at = datetime.fromtimestamp(int(ts))
                        except (TypeError, ValueError, OSError):
                            # Try string formats
                            if isinstance(ts, str):
                                try:
                                    posted_at = datetime.fromisoformat(ts)
                                except ValueError:
                                    posted_at = datetime.now()

                    detected = detect_language(content) or normalize_lang_code("zh")

                    entries.append(
                        ReviewEntry(
                            external_id=cid,
                            rating=None,  # 4399 comments aren't star-rated
                            content=content,
                            author_name=author,
                            helpful_count=helpful_count,
                            language=detected,
                            posted_at=posted_at,
                            metadata={
                                "source": endpoint,
                                "game_id": app_id,
                            },
                        )
                    )
                except Exception as e:
                    logger.warning(f"[4399] skipped malformed comment: {e}")
                    continue

            if entries:
                return entries

        return []
