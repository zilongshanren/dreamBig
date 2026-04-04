"""Google Play Store scraper using direct HTTP requests."""

from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)

# Google Play category mappings for games
CHART_URLS = {
    "top_free": "https://play.google.com/store/apps/collection/cluster?clp=0g4jCiEKG3RvcHNlbGxpbmdfZnJlZV9HQU1FX0FDVElPThAHGAM%3D:S:ANO1ljIkVqQ&gsr=CibSDiMKIQobdG9wc2VsbGluZ19mcmVlX0dBTUVfQUNUSU9OEAcYAw%3D%3D:S:ANO1ljJhBsI",
    "top_grossing": "https://play.google.com/store/apps/collection/cluster?clp=0g4lCiMKHXRvcHNlbGxpbmdfZ3Jvc3NpbmdfR0FNRV9BTEwQBxgD:S:ANO1ljJMx1c&gsr=CijSDiUKIwoddG9wc2VsbGluZ19ncm9zc2luZ19HQU1FX0FMTBAHGwM%3D:S:ANO1ljK52Hg",
}

# Simpler approach: use the top charts page
TOP_CHARTS_URL = "https://play.google.com/store/apps/top/category/GAME"

# Category IDs for games
GAME_CATEGORIES = {
    "GAME": "All Games",
    "GAME_ACTION": "Action",
    "GAME_ADVENTURE": "Adventure",
    "GAME_ARCADE": "Arcade",
    "GAME_BOARD": "Board",
    "GAME_CARD": "Card",
    "GAME_CASINO": "Casino",
    "GAME_CASUAL": "Casual",
    "GAME_EDUCATIONAL": "Educational",
    "GAME_MUSIC": "Music",
    "GAME_PUZZLE": "Puzzle",
    "GAME_RACING": "Racing",
    "GAME_ROLE_PLAYING": "Role Playing",
    "GAME_SIMULATION": "Simulation",
    "GAME_SPORTS": "Sports",
    "GAME_STRATEGY": "Strategy",
    "GAME_TRIVIA": "Trivia",
    "GAME_WORD": "Word",
}

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


class GooglePlayScraper(BaseScraper):
    platform = "google_play"
    rate_limit = 2.0

    async def scrape_rankings(
        self, chart_type: str = "top_free", region: str = "US"
    ) -> list[RankingEntry]:
        """Scrape Google Play game rankings.

        Uses the top charts page and parses the HTML response.
        Falls back to the collections API if the page structure changes.
        """
        client = await self.get_client()
        gl = REGION_MAP.get(region, region.lower())
        hl = "zh-CN" if gl == "cn" else "en"

        # Build the URL for top charts
        category = "GAME"
        url = f"https://play.google.com/store/apps/top/category/{category}"
        params = {"gl": gl, "hl": hl}

        resp = await client.get(url, params=params)
        resp.raise_for_status()

        return self._parse_rankings_page(resp.text, chart_type, region)

    def _parse_rankings_page(
        self, html: str, chart_type: str, region: str
    ) -> list[RankingEntry]:
        """Parse the Google Play top charts HTML page."""
        entries: list[RankingEntry] = []
        soup = BeautifulSoup(html, "lxml")

        # Google Play renders app cards in various container formats
        # Try to find app links with package IDs
        app_links = soup.select('a[href*="/store/apps/details?id="]')

        seen_ids: set[str] = set()
        rank = 0

        for link in app_links:
            href = link.get("href", "")
            match = re.search(r"id=([a-zA-Z0-9_.]+)", href)
            if not match:
                continue

            package_id = match.group(1)
            if package_id in seen_ids:
                continue
            seen_ids.add(package_id)
            rank += 1

            # Try to extract the app name from nearby elements
            name = ""
            name_el = link.select_one("span, div")
            if name_el:
                name = name_el.get_text(strip=True)
            if not name:
                name = link.get_text(strip=True)
            if not name:
                name = package_id

            # Try to get developer name
            developer = None
            parent = link.find_parent()
            if parent:
                dev_links = parent.select('a[href*="/store/apps/developer"]')
                if dev_links:
                    developer = dev_links[0].get_text(strip=True)

            # Try to get icon
            icon_url = None
            img = link.select_one("img")
            if img:
                icon_url = img.get("src") or img.get("data-src")

            entries.append(
                RankingEntry(
                    platform_id=package_id,
                    name=name,
                    rank_position=rank,
                    chart_type=chart_type,
                    region=region,
                    developer=developer,
                    icon_url=icon_url,
                    url=f"https://play.google.com/store/apps/details?id={package_id}",
                )
            )

            if rank >= 200:
                break

        return entries

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Scrape detailed information about a specific game."""
        client = await self.get_client()
        url = f"https://play.google.com/store/apps/details?id={platform_id}&hl=en&gl=us"

        resp = await client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        return self._parse_detail_page(resp.text, platform_id)

    def _parse_detail_page(self, html: str, platform_id: str) -> GameDetails:
        """Parse a Google Play game detail page."""
        soup = BeautifulSoup(html, "lxml")

        # Extract title
        title_el = soup.select_one('h1[itemprop="name"], h1')
        name = title_el.get_text(strip=True) if title_el else platform_id

        # Extract developer
        developer = None
        dev_link = soup.select_one('a[href*="/store/apps/developer"], a[href*="/store/apps/dev"]')
        if dev_link:
            developer = dev_link.get_text(strip=True)

        # Extract rating
        rating = None
        rating_el = soup.select_one('div[itemprop="starRating"], div[aria-label*="rating"]')
        if rating_el:
            text = rating_el.get("aria-label", "") or rating_el.get_text()
            match = re.search(r"([\d.]+)", text)
            if match:
                rating = float(match.group(1))

        # Extract genre from breadcrumb or metadata
        genre = None
        genre_el = soup.select_one('a[itemprop="genre"]')
        if genre_el:
            genre = genre_el.get_text(strip=True)

        # Extract description
        description = None
        desc_el = soup.select_one('div[data-g-id="description"], meta[name="description"]')
        if desc_el:
            if desc_el.name == "meta":
                description = desc_el.get("content", "")
            else:
                description = desc_el.get_text(strip=True)[:500]

        # Extract download count
        download_est = None
        for el in soup.select('div'):
            text = el.get_text(strip=True)
            match = re.match(r"^([\d,]+)\+?\s*downloads?$", text, re.IGNORECASE)
            if match:
                download_est = int(match.group(1).replace(",", ""))
                break

        return GameDetails(
            platform_id=platform_id,
            name=name,
            developer=developer,
            description=description,
            genre=genre,
            rating=rating,
            download_est=download_est,
            url=f"https://play.google.com/store/apps/details?id={platform_id}",
        )
