"""Readability-lite: fetch a URL and extract the main readable body.

We don't have trafilatura in the venv, so we roll a small extractor
that does the 80% job:

  1. Strip layout chrome (<nav>/<header>/<footer>/<aside>/<script>/<style>).
  2. Try <meta property="og:description"> / <meta name="description">.
  3. Try <article>, <main>, .post-content, .entry-content, etc.
  4. Fallback: pick the container with the highest text-to-anchor ratio
     (real articles have prose <p>; site menus are anchor-dense <div>s).
  5. Relevance filter: game name must appear in <title> OR the first
     300 chars of the extracted body. Otherwise reject — redirect /
     landing pages that only mention the game in a single sidebar link
     will be correctly dropped.

The goal is a 300-800 character chunk of Chinese text per page, not
pixel-perfect parsing. LLM will do the final synthesis.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Preferred content containers in descending priority.
_CONTENT_SELECTORS = [
    "article",
    "main",
    ".post-content",
    ".entry-content",
    ".article-body",
    ".article-content",
    ".content",
    "#content",
    ".rich-text",
    ".post__content",
]


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # Collapse repeated punctuation artifacts.
    text = re.sub(r"(&nbsp;|\u200b)+", " ", text)
    return text


def _extract_meta_description(soup: BeautifulSoup) -> Optional[str]:
    for key in ("og:description", "twitter:description"):
        m = soup.find("meta", attrs={"property": key}) or soup.find(
            "meta", attrs={"name": key}
        )
        if m and m.get("content"):
            return _clean_text(m["content"])
    m = soup.find("meta", attrs={"name": "description"})
    if m and m.get("content"):
        return _clean_text(m["content"])
    return None


def _strip_layout_chrome(soup: BeautifulSoup) -> None:
    """Remove nav/header/footer/script/style in-place before extraction."""
    for tag_name in (
        "nav",
        "header",
        "footer",
        "aside",
        "script",
        "style",
        "form",
        "noscript",
        "iframe",
        "svg",
    ):
        for el in soup.find_all(tag_name):
            el.decompose()


def _container_prose_score(el) -> tuple[int, str]:
    """Score a container by (length of non-anchor text, returned text).

    Real articles contain <p> tags with prose. Site menus / widgets
    wrap most text inside <a>. By measuring text OUTSIDE anchors we
    get a strong signal for real content.
    """
    # Clone-safe: compute by subtracting anchor text from total.
    total_text = _clean_text(el.get_text(" ", strip=True))
    anchor_text_len = 0
    for a in el.find_all("a"):
        anchor_text_len += len(_clean_text(a.get_text(" ", strip=True)))
    prose_len = max(0, len(total_text) - anchor_text_len)
    return (prose_len, total_text)


def _extract_main_body(soup: BeautifulSoup) -> Optional[str]:
    # Try semantic containers first.
    for sel in _CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el is None:
            continue
        prose_len, total_text = _container_prose_score(el)
        # Require at least 80 chars of text *outside* anchors.
        if prose_len >= 80:
            return total_text

    # Fallback: find the container with the highest prose_len score.
    candidates: list[tuple[int, str]] = []
    for el in soup.find_all(["div", "section", "article"]):
        prose_len, total_text = _container_prose_score(el)
        if prose_len >= 80:
            candidates.append((prose_len, total_text))
    if candidates:
        candidates.sort(key=lambda t: t[0], reverse=True)
        return candidates[0][1]
    return None


def _looks_relevant(
    title: str, body: str, game_name: str
) -> bool:
    """Game name must appear in BOTH <title> AND the extracted body.

    Testing three stricter rules against real-world output:
    - title-only lets through SEO content farms (taobao landing pages
      that match in <title> but discuss a different product)
    - body-only lets through nav-dump extractions (gamersky header
      listing unrelated browser games)
    - ``both`` correctly drops both failure modes while keeping all
      the real article pages.
    """
    if not game_name:
        return False
    if game_name not in (title or ""):
        return False
    return game_name in (body or "")


def fetch_page_content(
    url: str,
    game_name: str,
    *,
    client: httpx.Client | None = None,
    max_chars: int = 800,
    timeout: float = 15.0,
) -> Optional[dict]:
    """Fetch a URL, extract title + description + body, return a dict.

    Returns None on any failure. The caller decides what to do with None
    (e.g. mark source as unreachable in game_web_sources).

    Output keys: title / description / body / final_url / http_status
    """
    owned = client is None
    c = client or httpx.Client(
        trust_env=False,
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": _UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )

    try:
        try:
            resp = c.get(url)
        except Exception as exc:
            logger.info(f"[page_fetcher] GET {url[:80]} failed: {exc}")
            return None
        if resp.status_code != 200:
            logger.info(
                f"[page_fetcher] {url[:80]} status={resp.status_code}"
            )
            return None

        # Some sites serve binary or gigantic payloads — guard.
        if len(resp.text) > 2_000_000:
            logger.info(
                f"[page_fetcher] {url[:80]} body too large, skipping"
            )
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        _strip_layout_chrome(soup)

        title_el = soup.find("title")
        title = _clean_text(title_el.get_text(" ", strip=True)) if title_el else ""

        description = _extract_meta_description(soup) or ""
        body = _extract_main_body(soup) or ""

        # Prefer body, fall back to description if body is thin.
        chosen = body if len(body) > len(description) else description
        if not chosen and description:
            chosen = description

        # Tightened relevance filter — catch redirect/landing pages.
        if not _looks_relevant(title, chosen, game_name):
            logger.debug(
                f"[page_fetcher] {url[:80]} dropped — '{game_name}' "
                f"not in title or head of body"
            )
            return None

        # Truncate to the prompt budget.
        chosen = chosen[:max_chars]

        return {
            "title": title,
            "description": description,
            "body": chosen,
            "final_url": str(resp.url),
            "http_status": resp.status_code,
        }
    finally:
        if owned:
            c.close()


__all__ = ["fetch_page_content"]
