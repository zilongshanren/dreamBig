"""Public-web gameplay sources: one Bing search → 4 channels via site: filters.

Design notes:
- We do NOT maintain per-site scrapers anymore. Local probing showed
  Gamelook (www.gamelook.com.cn) connection-refused from some networks,
  youxiputao.com has no public search endpoint, and bilibili's article
  search is a JS SPA requiring Playwright or WBI signing. All three
  problems vanish when we go through Bing's ``site:`` operator — Bing
  reaches them for us and returns a uniform HTML structure.
- The Bing module issues multiple queries per game (one general, four
  site-scoped) and returns a deduped list of ``WebSource`` objects.
- ``page_fetcher`` takes those URLs and pulls the main readable body
  using a bs4-based readability-lite extractor (og:description +
  longest content block).
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WebSource:
    """One scraped public-web source (search result or fetched page body)."""

    url: str
    source_site: str  # bing_general / bing_bilibili / bing_gamelook / bing_youxiputao / bing_zhihu
    title: str
    snippet: str
    content_text: str = ""
    query: str = ""
    http_status: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


from src.scrapers.gameplay_web.bing import search_bing_for_game
from src.scrapers.gameplay_web.page_fetcher import fetch_page_content

__all__ = [
    "WebSource",
    "search_bing_for_game",
    "fetch_page_content",
]
