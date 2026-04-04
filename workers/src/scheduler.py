"""Job scheduler for periodic scraping tasks.

Uses APScheduler to trigger scrape jobs on a cron schedule.
Jobs are enqueued to Redis via rq for worker processing.
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv
from redis import Redis
from rq import Queue

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

redis_conn = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
queue = Queue(connection=redis_conn)


def enqueue_scrape(platform: str, chart_type: str, region: str = "CN"):
    """Enqueue a scrape job to the Redis queue."""
    queue.enqueue(
        "src.worker.run_scrape_job",
        platform=platform,
        chart_type=chart_type,
        region=region,
        job_timeout="10m",
    )
    logger.info(f"Enqueued scrape: {platform}/{chart_type}/{region}")


def enqueue_scoring():
    """Enqueue scoring recalculation."""
    queue.enqueue("src.worker.run_scoring", job_timeout="30m")
    logger.info("Enqueued scoring job")


def enqueue_alerts():
    """Enqueue alert evaluation."""
    queue.enqueue("src.worker.run_alerts", job_timeout="5m")
    logger.info("Enqueued alerts job")


def enqueue_social_signals():
    """Enqueue social media signal collection."""
    queue.enqueue("src.worker.run_social_signals", job_timeout="30m")
    logger.info("Enqueued social signals job")


def enqueue_ad_intel():
    """Enqueue ad intelligence collection."""
    queue.enqueue("src.worker.run_ad_intel", job_timeout="30m")
    logger.info("Enqueued ad intel job")


def main():
    scheduler = BlockingScheduler()

    # === Morning run: 06:00 HKT ===
    # Google Play rankings (multiple regions)
    for region in ["CN", "US", "JP"]:
        scheduler.add_job(
            enqueue_scrape,
            "cron",
            hour=6, minute=0,
            args=["google_play", "top_free", region],
            id=f"gp_top_free_{region}",
        )

    # App Store rankings
    for region in ["CN", "US", "JP"]:
        scheduler.add_job(
            enqueue_scrape,
            "cron",
            hour=6, minute=5,
            args=["app_store", "top_free", region],
            id=f"as_top_free_{region}",
        )

    # TapTap
    scheduler.add_job(
        enqueue_scrape, "cron", hour=6, minute=10,
        args=["taptap", "hot", "CN"], id="taptap_hot",
    )
    scheduler.add_job(
        enqueue_scrape, "cron", hour=6, minute=15,
        args=["taptap", "new", "CN"], id="taptap_new",
    )

    # Steam
    scheduler.add_job(
        enqueue_scrape, "cron", hour=6, minute=20,
        args=["steam", "top_sellers", "GLOBAL"], id="steam_sellers",
    )
    scheduler.add_job(
        enqueue_scrape, "cron", hour=6, minute=25,
        args=["steam", "trending", "GLOBAL"], id="steam_trending",
    )

    # HTML5 portals
    scheduler.add_job(
        enqueue_scrape, "cron", hour=6, minute=30,
        args=["poki", "popular", "GLOBAL"], id="poki_popular",
    )
    scheduler.add_job(
        enqueue_scrape, "cron", hour=6, minute=35,
        args=["crazygames", "trending", "GLOBAL"], id="cg_trending",
    )

    # === Afternoon run: 14:00 HKT (2nd daily for major platforms) ===
    for region in ["CN", "US"]:
        scheduler.add_job(
            enqueue_scrape, "cron", hour=14, minute=0,
            args=["google_play", "top_free", region],
            id=f"gp_top_free_{region}_pm",
        )
        scheduler.add_job(
            enqueue_scrape, "cron", hour=14, minute=5,
            args=["app_store", "top_free", region],
            id=f"as_top_free_{region}_pm",
        )

    # === 07:00: Post-scrape processing ===
    scheduler.add_job(
        enqueue_social_signals, "cron", hour=7, minute=0,
        id="social_signals",
    )
    scheduler.add_job(
        enqueue_ad_intel, "cron", hour=7, minute=30,
        id="ad_intel",
    )

    # === 08:00: Scoring + Alerts ===
    scheduler.add_job(
        enqueue_scoring, "cron", hour=8, minute=0, id="scoring",
    )
    scheduler.add_job(
        enqueue_alerts, "cron", hour=8, minute=30, id="alerts",
    )

    logger.info("Scheduler started. Jobs registered:")
    for job in scheduler.get_jobs():
        logger.info(f"  {job.id}: {job.trigger}")

    scheduler.start()


if __name__ == "__main__":
    main()
