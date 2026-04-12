"""Bing 中文 search adapter — single entry, two quoted queries per game.

Strategy determined by live probing against real game names:

- Unquoted Chinese game names get tokenized by Bing into individual
  characters, returning irrelevant biology/celebrity hits. Quoting
  the name with ``"…"`` forces exact-phrase match.
- The keyword ``微信`` derails the query (Bing reweights heavily toward
  WeChat help content). Use ``玩法`` and ``游戏攻略`` instead.
- Bing's ``site:gamelook.com.cn`` / ``site:youxiputao.com`` filters
  return zero hits — those sites aren't well-indexed in the Chinese
  Bing corpus. Drop the site filters entirely and rely on organic
  ranking to surface media articles.
- zhihu.com, bilibili.com/read (article SPA) and douyin.com all block
  simple httpx fetches (403 or JS-gated). Drop them before returning
  results so the page_fetcher isn't burning requests on dead ends.

Two queries per game, deduped by URL, unreachable domains filtered:
  1. ``"<name>" 玩法``
  2. ``"<name>" 游戏攻略``
"""

from __future__ import annotations

import html as htmllib
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_BING_URL = "https://cn.bing.com/search"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Two queries per game, both quoted for exact-match. The source_label
# stays "bing_<intent>" so we can tell them apart in game_web_sources.
_QUERY_PLAN: list[tuple[str, str]] = [
    ("bing_gameplay", '"{name}" 玩法'),
    ("bing_walkthrough", '"{name}" 游戏攻略'),
]

# Max results to keep per query. Bing returns ~10.
_PER_QUERY_CAP = 10

# Domains we know we cannot fetch (JS SPA / 403 bot protection). Dropping
# them at search time saves us burning page_fetcher requests. The user
# can still see the search-result title + snippet, but we won't try to
# pull the full page body from these hosts.
_UNREACHABLE_DOMAIN_PREFIXES = (
    "www.zhihu.com/question",       # 403
    "www.zhihu.com/answer",         # 403
    "www.douyin.com",               # SPA
    "www.bilibili.com/read/",       # SPA
    "search.bilibili.com",          # SPA
)


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    text = htmllib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_bing_html(html: str) -> list[dict[str, str]]:
    """Return list of {title, url, snippet} from a Bing 中文 result page."""
    # Each result is wrapped in <li class="b_algo" ...>...</li>.
    blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.DOTALL)
    out: list[dict[str, str]] = []
    for block in blocks:
        # Title + URL live in h2 > a[href]
        h2m = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.DOTALL)
        if not h2m:
            continue
        h2_inner = h2m.group(1)
        am = re.search(
            r'href\s*=\s*"([^"]+)"[^>]*>(.*?)</a>',
            h2_inner,
            re.DOTALL,
        )
        if not am:
            continue
        url = htmllib.unescape(am.group(1))
        title = _strip_tags(am.group(2))
        if not url.startswith("http") or not title:
            continue
        # Snippet: first <p> in the block
        snippet = ""
        pm = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)
        if pm:
            snippet = _strip_tags(pm.group(1))[:400]
        out.append({"title": title, "url": url, "snippet": snippet})
    return out


def _is_valid_url(url: str) -> bool:
    """Reject obvious junk / ad / redirect URLs and unreachable hosts."""
    if not url:
        return False
    bad_hosts = (
        "bing.com/aclick",  # Bing ads redirect
        "bing.com/ck/a",
        "microsofttranslator.com",
    )
    if any(b in url for b in bad_hosts):
        return False
    # Strip protocol for prefix matching against the unreachable list.
    stripped = url.split("://", 1)[-1]
    return not any(
        stripped.startswith(p) for p in _UNREACHABLE_DOMAIN_PREFIXES
    )


def search_bing_for_game(
    game_name: str,
    *,
    client: httpx.Client | None = None,
    per_query_cap: int = _PER_QUERY_CAP,
    request_timeout: float = 15.0,
) -> list[dict[str, Any]]:
    """Run all five queries for one game, return a deduped list of hits.

    Each hit is a dict with keys:
        source_site, url, title, snippet, query, http_status

    Uses a single persistent client if one is passed in (so the caller
    can share a connection pool). Otherwise creates a throwaway one.
    """
    owned = client is None
    c = client or httpx.Client(
        trust_env=False,
        timeout=request_timeout,
        follow_redirects=True,
        headers={
            "User-Agent": _UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    results_by_url: dict[str, dict[str, Any]] = {}
    try:
        for label, template in _QUERY_PLAN:
            query = template.format(name=game_name)
            url = f"{_BING_URL}?q={quote(query)}&ensearch=0&mkt=zh-CN"
            try:
                resp = c.get(url)
            except Exception as exc:
                logger.warning(
                    f"[bing] {label!r} query {query!r} failed: {exc}"
                )
                continue
            if resp.status_code != 200:
                logger.warning(
                    f"[bing] {label!r} HTTP {resp.status_code} for {query!r}"
                )
                continue
            parsed = _parse_bing_html(resp.text)[:per_query_cap]
            for h in parsed:
                u = h["url"]
                if not _is_valid_url(u):
                    continue
                if u in results_by_url:
                    # Earlier query wins; don't clobber source label.
                    continue
                results_by_url[u] = {
                    "source_site": label,
                    "url": u,
                    "title": h["title"],
                    "snippet": h["snippet"],
                    "query": query,
                    "http_status": resp.status_code,
                }
            logger.debug(
                f"[bing] {label}: {len(parsed)} hits "
                f"(dedup total: {len(results_by_url)})"
            )
    finally:
        if owned:
            c.close()

    return list(results_by_url.values())


__all__ = ["search_bing_for_game"]
