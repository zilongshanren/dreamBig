"""Bilibili video comment scraper — review proxy for WeChat mini-games.

Uses Playwright (headless Chromium) for the whole scrape flow:

  1. Open https://search.bilibili.com/video?keyword=XXX in a real browser.
  2. Intercept the browser's own /x/web-interface/wbi/search/type XHR response
     (the browser signs WBI internally — we just grab the JSON).
  3. For each matching video, call the public reply API via ``page.request``
     so the call inherits the page's cookies (buvid3 etc.), bypassing the
     rate-limit triggers that hit plain httpx from the same IP.

This replaces the pure-httpx + manual WBI approach because Bilibili's
anti-bot on /search/type from a datacenter HK IP is aggressive enough
that unsigned requests get 412 even after cookie bootstrap. A real
browser session sails through.

Pipeline:
  1. Lazy Playwright init (single browser + context per scraper instance).
  2. Search → capture API response from the page's XHR.
  3. For each of the top-N videos, fetch comments via page.request.
  4. Emit ReviewEntry rows. external_id is bvid:reply_id for stable dedup.
  5. close() cleans up the browser.
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import datetime
from typing import Any

from .base import BaseReviewScraper, ReviewEntry

logger = logging.getLogger(__name__)


DEFAULT_VIDEOS_PER_GAME = 5
DEFAULT_COMMENTS_PER_VIDEO = 40
MIN_COMMENT_LEN = 3  # keep "真好玩" etc.


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# Bilibili BV↔AV conversion — the new-format BV IDs introduced in 2020.
# AID is encoded inside the BVID via a fixed permutation + xor mask.
# Algorithm reference: https://www.zhihu.com/question/381784377/answer/1099438784
#
# We use this instead of the /x/web-interface/view endpoint because /view is
# aggressively rate-limited from datacenter IPs (returns code != 0 for 42/43
# BVIDs in practice), while local conversion is zero-cost and always correct.
_BV_TABLE = "fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF"
_BV_TABLE_MAP = {c: i for i, c in enumerate(_BV_TABLE)}
_BV_POS = (11, 10, 3, 8, 4, 6)
_BV_XOR = 177_451_812
_BV_ADD = 8_728_348_608


def _bvid_to_aid_local(bvid: str) -> int | None:
    """Local BV→AV converter for classic 12-char BVIDs ("BV1xxxxxxxxxx").

    Returns None for malformed inputs; never hits the network.
    """
    if not bvid or not bvid.startswith("BV") or len(bvid) != 12:
        return None
    try:
        r = 0
        for i in range(6):
            ch = bvid[_BV_POS[i]]
            r += _BV_TABLE_MAP[ch] * (58 ** i)
        return (r - _BV_ADD) ^ _BV_XOR
    except KeyError:
        return None
    except Exception:
        return None


class BilibiliReviewScraper(BaseReviewScraper):
    """Scrapes Bilibili comments via headless Chromium + page-scoped requests."""

    platform = "bilibili_review"
    rate_limit = 1.0  # we're paced by Playwright page loads anyway

    def __init__(self, proxy_url: str | None = None) -> None:
        super().__init__(proxy_url)
        self._pw: Any = None
        self._browser: Any = None
        self._ctx: Any = None

    # ------------------------------------------------------------------
    # Playwright lifecycle
    # ------------------------------------------------------------------
    async def _ensure_playwright(self) -> bool:
        if self._ctx is not None:
            return True
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError:
            logger.warning(
                "[bilibili_review] playwright not installed — scraper disabled. "
                "Rebuild the workers image to install chromium."
            )
            return False

        try:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                ],
            )
            self._ctx = await self._browser.new_context(
                user_agent=_USER_AGENT,
                locale="zh-CN",
                viewport={"width": 1280, "height": 800},
                extra_http_headers={
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
            # Prime cookies with a homepage visit so buvid3 etc. land in the jar
            try:
                page = await self._ctx.new_page()
                await page.goto(
                    "https://www.bilibili.com/",
                    wait_until="domcontentloaded",
                    timeout=20_000,
                )
                try:
                    await page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:
                    pass
                await page.close()
                logger.info("[bilibili_review] playwright session ready")
                return True
            except Exception as e:
                logger.warning(f"[bilibili_review] homepage bootstrap failed: {e}")
                return True  # continue anyway, some calls may still work
        except Exception as e:
            logger.error(f"[bilibili_review] playwright launch failed: {e}")
            await self._teardown_playwright()
            return False

    async def _teardown_playwright(self) -> None:
        for attr in ("_ctx", "_browser"):
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            try:
                await obj.close()
            except Exception:
                pass
            setattr(self, attr, None)
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

    async def close(self) -> None:
        await self._teardown_playwright()
        try:
            await super().close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def scrape_reviews(
        self,
        platform_id: str,
        region: str = "CN",
        limit: int = DEFAULT_COMMENTS_PER_VIDEO * DEFAULT_VIDEOS_PER_GAME,
        lang: str = "zh",
    ) -> list[ReviewEntry]:
        game_name = platform_id.strip()
        if not game_name:
            return []

        if not await self._ensure_playwright():
            return []

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
                    f"[bilibili_review] comment fetch failed for {bvid}: {e}"
                )
            if len(all_reviews) >= limit:
                break

        logger.info(
            f"[bilibili_review] '{game_name}': {len(all_reviews)} comments "
            f"across {len(videos)} videos"
        )
        return all_reviews[:limit]

    # ------------------------------------------------------------------
    # Search: hybrid — listen for any "search" XHR while the page loads,
    # then also scrape the DOM. Whichever returns more data wins.
    # ------------------------------------------------------------------
    async def _search_videos(
        self, keyword: str, limit: int
    ) -> list[dict[str, Any]]:
        assert self._ctx is not None
        page = await self._ctx.new_page()
        captured: list[dict[str, Any]] = []

        # Collect every JSON response from *any* Bilibili search endpoint that
        # fires during the page load. Bilibili currently uses
        # /x/web-interface/wbi/search/all/v2 for the main search page XHR,
        # but the exact path has changed twice in 2 years. Match broadly.
        async def _capture(resp: Any) -> None:
            try:
                url = resp.url or ""
                if "/search/" not in url:
                    return
                if resp.request.method != "GET":
                    return
                ctype = (resp.headers.get("content-type") or "").lower()
                if "json" not in ctype:
                    return
                data = await resp.json()
            except Exception:
                return
            if not isinstance(data, dict) or data.get("code") != 0:
                return
            # /search/type returns data.result (list).
            # /search/all/v2 returns data.result (list of sections with a
            #   section.result_type=="video" containing data).
            payload = (data.get("data") or {})
            result = payload.get("result")
            if isinstance(result, list) and result:
                if isinstance(result[0], dict) and "result_type" in result[0]:
                    # all/v2 shape — pick the video section
                    for section in result:
                        if (
                            section.get("result_type") == "video"
                            and isinstance(section.get("data"), list)
                        ):
                            captured.extend(section["data"])
                            return
                else:
                    # type shape — list of videos directly
                    captured.extend(result)

        page.on("response", _capture)

        try:
            search_url = (
                "https://search.bilibili.com/video?keyword="
                + urllib.parse.quote(f"{keyword} 小游戏")
                + "&order=click&duration=0&tids=0"
            )

            try:
                await page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
            except Exception as e:
                logger.warning(
                    f"[bilibili_review] search page goto failed for '{keyword}': {e}"
                )

            # Wait for video cards to actually appear in the DOM. Bilibili
            # sometimes takes 5-15s to populate the grid after domcontentloaded.
            try:
                await page.wait_for_selector(
                    'a[href*="/video/BV"]',
                    timeout=20_000,
                )
            except Exception:
                pass  # keep going; we'll try scraping whatever rendered

            # Scroll a bit to trigger lazy-loaded cards
            try:
                await page.evaluate(
                    "() => window.scrollBy(0, window.innerHeight * 2)"
                )
                await page.wait_for_timeout(1_500)
            except Exception:
                pass

            # Try DOM scrape to supplement any captured XHR
            dom_videos = await self._search_videos_dom_extract(page)

            # Remove the response listener so subsequent pages don't stack up
            try:
                page.remove_listener("response", _capture)
            except Exception:
                pass

            if captured:
                import re
                for v in captured:
                    if isinstance(v.get("title"), str):
                        v["title"] = re.sub(r"<[^>]+>", "", v["title"])
                logger.info(
                    f"[bilibili_review] search '{keyword}': XHR captured "
                    f"{len(captured)} videos ({len(dom_videos)} also in DOM)"
                )
                return captured[:limit]

            if dom_videos:
                logger.info(
                    f"[bilibili_review] search '{keyword}': DOM scraped "
                    f"{len(dom_videos)} BVIDs, resolving aids..."
                )
                resolved = await self._resolve_bvids(dom_videos, limit)
                logger.info(
                    f"[bilibili_review] search '{keyword}': resolved "
                    f"{len(resolved)} videos via DOM fallback"
                )
                return resolved

            logger.warning(
                f"[bilibili_review] search '{keyword}': 0 videos from XHR and DOM"
            )
            return []
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _search_videos_dom_extract(self, page: Any) -> list[dict[str, Any]]:
        """Pull every BVID + title from the rendered search DOM."""
        try:
            videos = await page.evaluate(
                r"""() => {
                    const out = [];
                    const seen = new Set();

                    // Primary: any anchor linking to /video/BVxxx
                    const anchors = document.querySelectorAll('a[href*="/video/BV"]');
                    for (const a of anchors) {
                        const m = a.href.match(/\/video\/(BV[0-9A-Za-z]+)/);
                        if (!m) continue;
                        const bvid = m[1];
                        if (seen.has(bvid)) continue;
                        seen.add(bvid);

                        // Pull title from the anchor's title attr, aria-label,
                        // or nearest meaningful ancestor text.
                        let title = (a.title || a.getAttribute('aria-label') || '').trim();
                        if (!title) {
                            let el = a;
                            for (let i = 0; i < 6 && el; i++) {
                                const txt = (el.innerText || el.textContent || '').trim();
                                if (txt.length > 5) {
                                    title = txt.split('\n')[0];
                                    break;
                                }
                                el = el.parentElement;
                            }
                        }
                        out.push({ bvid, title: (title || '').slice(0, 200) });
                        if (out.length >= 60) break;
                    }
                    return out;
                }"""
            )
            return videos or []
        except Exception as e:
            logger.debug(f"[bilibili_review] DOM extract eval failed: {e}")
            return []

    async def _resolve_bvids(
        self, dom_videos: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        """Resolve each BVID to numeric aid using local conversion.

        We previously called /x/web-interface/view per BVID but Bilibili
        rate-limits that endpoint heavily from datacenter IPs — in our
        last run 42 out of 43 calls failed. The BV format encodes the AID
        directly, so we compute it locally for free.
        """
        resolved: list[dict[str, Any]] = []
        for v in dom_videos:
            aid = _bvid_to_aid_local(v["bvid"])
            if not aid or aid <= 0:
                continue
            resolved.append(
                {
                    "aid": aid,
                    "bvid": v["bvid"],
                    "title": v.get("title", ""),
                    "play": 0,
                }
            )
            if len(resolved) >= limit:
                break
        return resolved

    async def _bvid_to_aid(self, bvid: str) -> int | None:
        """Look up numeric aid for a bvid via Bilibili's view endpoint."""
        assert self._ctx is not None
        try:
            resp = await self._ctx.request.get(
                "https://api.bilibili.com/x/web-interface/view",
                params={"bvid": bvid},
                headers={
                    "Accept": "application/json",
                    "Referer": "https://www.bilibili.com/",
                    "User-Agent": _USER_AGENT,
                },
                timeout=15_000,
            )
            data = await resp.json()
            if data.get("code") != 0:
                return None
            return int((data.get("data") or {}).get("aid") or 0) or None
        except Exception as e:
            logger.debug(f"[bilibili_review] view lookup failed for {bvid}: {e}")
            return None

    # ------------------------------------------------------------------
    # Comment fetch via page-scoped requests (inherits cookies).
    # ------------------------------------------------------------------
    async def _fetch_video_comments(
        self,
        aid: int,
        bvid: str,
        video_play: int,
        video_title: str,
        limit: int,
    ) -> list[ReviewEntry]:
        assert self._ctx is not None
        replies_raw: list[dict[str, Any]] = []

        headers = {
            "Accept": "application/json",
            "Referer": f"https://www.bilibili.com/video/{bvid}",
            "Origin": "https://www.bilibili.com",
            "User-Agent": _USER_AGENT,
        }

        # Attempt 1: legacy /x/v2/reply sorted by likes
        try:
            resp = await self._ctx.request.get(
                "https://api.bilibili.com/x/v2/reply",
                params={
                    "type": 1,
                    "oid": aid,
                    "pn": 1,
                    "ps": min(limit, 49),
                    "sort": 2,
                },
                headers=headers,
                timeout=15_000,
            )
            data = await resp.json()
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

        # Attempt 2: newer /x/v2/reply/main
        if not replies_raw:
            try:
                resp = await self._ctx.request.get(
                    "https://api.bilibili.com/x/v2/reply/main",
                    params={
                        "type": 1,
                        "oid": aid,
                        "mode": 3,
                        "next": 0,
                        "ps": min(limit, 20),
                    },
                    headers=headers,
                    timeout=15_000,
                )
                data = await resp.json()
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
