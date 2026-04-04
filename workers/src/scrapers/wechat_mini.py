"""WeChat Mini Games scraper.

This is the hardest platform to scrape because WeChat's ecosystem is
relatively closed. This implementation provides a framework that will
need manual session token maintenance or a WeChat automation setup.
"""

from __future__ import annotations

import logging

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)


class WeChatMiniScraper(BaseScraper):
    """WeChat Mini Games scraper.

    Due to WeChat's closed ecosystem, this scraper requires either:
    1. Manual session token from WeChat DevTools
    2. Playwright-based automation with WeChat login
    3. Third-party data providers (e.g., aldwx.com, aldzs.com)

    For MVP, we recommend using third-party data aggregators
    like 阿拉丁指数 (aldzs.com) which tracks mini-program rankings.
    """

    platform = "wechat_mini"
    rate_limit = 5.0

    # 阿拉丁指数 (Aladdin Index) - tracks WeChat mini-program rankings
    ALADDIN_URL = "https://www.aldzs.com"

    async def scrape_rankings(
        self, chart_type: str = "hot", region: str = "CN"
    ) -> list[RankingEntry]:
        """Scrape WeChat Mini Games rankings.

        Primary method: Scrape 阿拉丁指数 rankings page.
        This provides mini-game rankings without needing
        WeChat session tokens.
        """
        client = await self.get_client()
        entries: list[RankingEntry] = []

        # Try aldzs.com game rankings
        try:
            resp = await client.get(
                f"{self.ALADDIN_URL}/top/game",
                headers={"Referer": self.ALADDIN_URL},
            )
            resp.raise_for_status()

            # aldzs.com may require JavaScript rendering
            # For now, log that manual integration is needed
            logger.info(
                "[wechat_mini] aldzs.com page fetched. "
                "JS rendering may be needed for full data extraction. "
                "Consider using Playwright for this scraper."
            )

            # TODO: Implement Playwright-based scraping for aldzs.com
            # or integrate their API if they provide one

        except Exception as e:
            logger.warning(f"[wechat_mini] aldzs.com scrape failed: {e}")

        return entries

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Scrape detailed info about a WeChat mini-game.

        Requires WeChat session or third-party data source.
        """
        logger.info(
            f"[wechat_mini] Detail scraping for {platform_id} "
            "requires WeChat session. Skipping."
        )
        return None
