"""Poki HTML5 game portal scraper."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)

POKI_BASE_URL = "https://poki.com"


class PokiScraper(BaseScraper):
    platform = "poki"
    rate_limit = 2.0

    async def scrape_rankings(
        self, chart_type: str = "popular", region: str = "GLOBAL"
    ) -> list[RankingEntry]:
        """Scrape Poki's popular/trending games pages."""
        client = await self.get_client()

        # Poki has categorized pages we can scrape
        page_map = {
            "popular": f"{POKI_BASE_URL}/en/best-games",
            "new": f"{POKI_BASE_URL}/en/new-games",
            "trending": f"{POKI_BASE_URL}/en/trending",
        }
        url = page_map.get(chart_type, page_map["popular"])

        resp = await client.get(url)
        resp.raise_for_status()

        return self._parse_game_list(resp.text, chart_type)

    def _parse_game_list(self, html: str, chart_type: str) -> list[RankingEntry]:
        """Parse Poki's game listing page."""
        soup = BeautifulSoup(html, "lxml")
        entries: list[RankingEntry] = []

        # Poki renders game cards as anchor tags with game slugs
        game_links = soup.select('a[href*="/en/g/"]')
        seen_slugs: set[str] = set()

        for rank_idx, link in enumerate(game_links):
            href = link.get("href", "")
            match = re.search(r"/en/g/(.+?)(?:\?|$)", href)
            if not match:
                continue

            slug = match.group(1).strip("/")
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            name = link.get("title", "") or link.get_text(strip=True) or slug.replace("-", " ").title()

            icon_url = None
            img = link.select_one("img")
            if img:
                icon_url = img.get("src") or img.get("data-src")

            entries.append(
                RankingEntry(
                    platform_id=slug,
                    name=name,
                    rank_position=len(entries) + 1,
                    chart_type=chart_type,
                    region="GLOBAL",
                    icon_url=icon_url,
                    url=f"{POKI_BASE_URL}/en/g/{slug}",
                )
            )

            if len(entries) >= 100:
                break

        return entries

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Scrape detailed info about a Poki game."""
        client = await self.get_client()
        url = f"{POKI_BASE_URL}/en/g/{platform_id}"

        resp = await client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        title_el = soup.select_one("h1")
        name = title_el.get_text(strip=True) if title_el else platform_id.replace("-", " ").title()

        # Try to find developer info
        developer = None
        dev_el = soup.select_one('a[href*="/en/publisher/"]')
        if dev_el:
            developer = dev_el.get_text(strip=True)

        # Description
        description = None
        desc_el = soup.select_one('meta[name="description"]')
        if desc_el:
            description = desc_el.get("content", "")[:500]

        return GameDetails(
            platform_id=platform_id,
            name=name,
            developer=developer,
            description=description,
            url=url,
        )
