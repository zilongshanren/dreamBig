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
        try:
            asyncio.run(scraper.close())
        except RuntimeError:
            pass


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


def run_fetch_details():
    """Fetch game details (screenshots, descriptions) for games missing them."""
    logger.info("Starting game details fetch")
    import json

    with psycopg.connect(DB_URL) as conn:
        # Find games without screenshots in metadata
        games = conn.execute(
            """
            SELECT g.id, pl.platform, pl.platform_id
            FROM games g
            JOIN platform_listings pl ON g.id = pl.game_id
            WHERE g.metadata::text = '{}'
               OR g.metadata->>'screenshots' IS NULL
            ORDER BY g.id
            """
        ).fetchall()

        if not games:
            logger.info("All games already have details")
            return

        logger.info(f"Fetching details for {len(games)} game-platform pairs")

        # Group by platform to reuse scraper instances
        by_platform: dict[str, list[tuple[int, str]]] = {}
        for game_id, platform, platform_id in games:
            by_platform.setdefault(platform, []).append((game_id, platform_id))

        total = 0
        for platform, items in by_platform.items():
            if platform not in SCRAPER_MAP:
                continue

            scraper = _get_scraper(platform)
            for game_id, platform_id in items:
                try:
                    details = asyncio.run(scraper.scrape_game_details(platform_id))
                    if not details:
                        continue

                    # Build metadata update
                    meta = {}
                    if details.screenshots:
                        meta["screenshots"] = details.screenshots[:5]
                    if details.description:
                        meta["description"] = details.description
                    if details.icon_url:
                        meta["icon_url"] = details.icon_url

                    if meta:
                        conn.execute(
                            """
                            UPDATE games SET
                                metadata = metadata || %s::jsonb,
                                thumbnail_url = COALESCE(thumbnail_url, %s)
                            WHERE id = %s
                            """,
                            (json.dumps(meta), details.icon_url, game_id),
                        )
                        conn.commit()
                        total += 1
                        logger.info(
                            f"Updated details for game #{game_id} "
                            f"({len(details.screenshots)} screenshots)"
                        )

                except Exception as e:
                    logger.warning(
                        f"Detail fetch failed for {platform}/{platform_id}: {e}"
                    )

            try:
                asyncio.run(scraper.close())
            except RuntimeError:
                pass

        logger.info(f"Details fetch complete: {total} games updated")


if __name__ == "__main__":
    # For local testing: run a single scrape
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "fetch_details":
        run_fetch_details()
    elif len(sys.argv) >= 3:
        platform = sys.argv[1]
        chart_type = sys.argv[2]
        region = sys.argv[3] if len(sys.argv) > 3 else "US"
        run_scrape_job(platform, chart_type, region)
    else:
        print("Usage: python -m src.worker <platform> <chart_type> [region]")
        print("       python -m src.worker fetch_details")
        print("Example: python -m src.worker app_store top_free US")
