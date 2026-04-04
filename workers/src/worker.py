"""Worker entry point for processing scrape jobs from the Redis queue.

Each job function is called by rq workers when a job is dequeued.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

import psycopg
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

DB_URL = os.environ.get("DATABASE_URL", "postgresql://dreambig:dreambig@db:5432/dreambig")

# Scraper registry
SCRAPER_MAP = {
    "google_play": "src.scrapers.google_play.GooglePlayScraper",
    "app_store": "src.scrapers.app_store.AppStoreScraper",
    "taptap": "src.scrapers.taptap.TapTapScraper",
    "steam": "src.scrapers.steam.SteamScraper",
    "poki": "src.scrapers.poki.PokiScraper",
    "crazygames": "src.scrapers.crazygames.CrazyGamesScraper",
    "wechat_mini": "src.scrapers.wechat_mini.WeChatMiniScraper",
}


def _get_scraper(platform: str):
    """Dynamically load a scraper class by platform name."""
    module_path, class_name = SCRAPER_MAP[platform].rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls()


def _record_job(conn, platform: str, job_type: str, status: str, items: int = 0, error: str | None = None):
    """Record a scrape job in the tracking table."""
    conn.execute(
        """
        INSERT INTO scrape_jobs (platform, job_type, status, items_scraped, error_message, started_at, finished_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (platform, job_type, status, items, error, datetime.now(),
         datetime.now() if status in ("success", "failed") else None),
    )
    conn.commit()


def run_scrape_job(platform: str, chart_type: str, region: str = "CN"):
    """Run a ranking scrape job for a given platform."""
    logger.info(f"Starting scrape: {platform}/{chart_type}/{region}")

    scraper = _get_scraper(platform)

    try:
        entries = asyncio.run(scraper.scrape_rankings_safe(chart_type, region))

        if not entries:
            logger.warning(f"No entries scraped for {platform}/{chart_type}/{region}")
            with psycopg.connect(DB_URL) as conn:
                _record_job(conn, platform, "rankings", "success", 0)
            return

        # Process entries through dedup engine
        from src.processors.dedup import DeduplicationEngine
        dedup = DeduplicationEngine(DB_URL)

        with psycopg.connect(DB_URL) as conn:
            count = dedup.process_ranking_entries(conn, entries, platform)
            _record_job(conn, platform, "rankings", "success", count)

        logger.info(f"Completed: {platform}/{chart_type}/{region} - {count} entries")

    except Exception as e:
        logger.error(f"Scrape failed: {platform}/{chart_type}/{region} - {e}")
        try:
            with psycopg.connect(DB_URL) as conn:
                _record_job(conn, platform, "rankings", "failed", 0, str(e))
        except Exception:
            pass
        raise
    finally:
        asyncio.run(scraper.close())


def run_scoring():
    """Run the scoring engine for all games."""
    logger.info("Starting scoring run")
    from src.processors.scoring import ScoringEngine
    engine = ScoringEngine(DB_URL)
    scores = engine.score_all_games()
    logger.info(f"Scoring complete: {len(scores)} games scored")


def run_alerts():
    """Evaluate alert rules."""
    logger.info("Starting alert evaluation")
    from src.processors.alerting import AlertEngine
    engine = AlertEngine(DB_URL)
    triggered = engine.evaluate_alerts()
    logger.info(f"Alerts complete: {triggered} triggered")


def run_social_signals():
    """Collect social media signals for all tracked games."""
    logger.info("Starting social signal collection")
    from src.scrapers.social_media import SocialMediaScraper

    scraper = SocialMediaScraper()

    with psycopg.connect(DB_URL) as conn:
        games = conn.execute(
            "SELECT id, COALESCE(name_zh, name_en) FROM games ORDER BY id"
        ).fetchall()

        for game_id, game_name in games:
            if not game_name:
                continue

            try:
                signals = asyncio.run(scraper.collect_signals(game_name))

                for sig in signals:
                    conn.execute(
                        """
                        INSERT INTO social_signals
                        (game_id, platform, video_count, view_count, like_count,
                         hashtag_volume, signal_date)
                        VALUES (%s, %s, %s, %s, %s, %s, CURRENT_DATE)
                        ON CONFLICT (game_id, platform, signal_date) DO UPDATE SET
                            video_count = EXCLUDED.video_count,
                            view_count = EXCLUDED.view_count,
                            like_count = EXCLUDED.like_count
                        """,
                        (game_id, sig.platform, sig.video_count,
                         sig.view_count, sig.like_count, sig.hashtag_volume),
                    )

                conn.commit()
            except Exception as e:
                logger.warning(f"Social signal failed for '{game_name}': {e}")

    asyncio.run(scraper.close())
    logger.info("Social signal collection complete")


def run_ad_intel():
    """Collect ad intelligence for all tracked games."""
    logger.info("Starting ad intel collection")
    from src.scrapers.ad_intel import AdIntelScraper

    scraper = AdIntelScraper()

    with psycopg.connect(DB_URL) as conn:
        games = conn.execute(
            "SELECT id, COALESCE(name_en, name_zh) FROM games ORDER BY id"
        ).fetchall()

        for game_id, game_name in games:
            if not game_name:
                continue

            try:
                signals = asyncio.run(scraper.collect_signals(game_name))

                for sig in signals:
                    import json
                    conn.execute(
                        """
                        INSERT INTO ad_intelligence
                        (game_id, source, active_creatives, markets,
                         creative_types, estimated_spend, signal_date)
                        VALUES (%s, %s, %s, %s, %s, %s, CURRENT_DATE)
                        ON CONFLICT (game_id, source, signal_date) DO UPDATE SET
                            active_creatives = EXCLUDED.active_creatives,
                            markets = EXCLUDED.markets,
                            creative_types = EXCLUDED.creative_types,
                            estimated_spend = EXCLUDED.estimated_spend
                        """,
                        (game_id, sig.source, sig.active_creatives,
                         sig.markets, sig.creative_types, sig.estimated_spend),
                    )

                conn.commit()
            except Exception as e:
                logger.warning(f"Ad intel failed for '{game_name}': {e}")

    asyncio.run(scraper.close())
    logger.info("Ad intel collection complete")


if __name__ == "__main__":
    # For local testing: run a single scrape
    import sys

    if len(sys.argv) >= 3:
        platform = sys.argv[1]
        chart_type = sys.argv[2]
        region = sys.argv[3] if len(sys.argv) > 3 else "US"
        run_scrape_job(platform, chart_type, region)
    else:
        print("Usage: python -m src.worker <platform> <chart_type> [region]")
        print("Example: python -m src.worker app_store top_free US")
