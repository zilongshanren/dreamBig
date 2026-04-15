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


def enqueue_fetch_details():
    """Enqueue game detail/screenshot fetching."""
    queue.enqueue("src.worker.run_fetch_details", job_timeout="60m")
    logger.info("Enqueued game details fetch job")


def enqueue_social_signals():
    """Enqueue social media signal collection."""
    queue.enqueue("src.worker.run_social_signals", job_timeout="30m")
    logger.info("Enqueued social signals job")


def enqueue_ad_intel():
    """Enqueue ad intelligence collection."""
    queue.enqueue("src.worker.run_ad_intel", job_timeout="30m")
    logger.info("Enqueued ad intel job")


def enqueue_scrape_reviews():
    """Enqueue bulk review scraping for high-potential games."""
    queue.enqueue("src.worker.run_scrape_all_reviews", job_timeout="60m")
    logger.info("Enqueued review scraping")


def enqueue_sentiment_classification():
    """Enqueue sentiment classification over pending reviews."""
    queue.enqueue("src.worker.run_sentiment_classification", job_timeout="30m")
    logger.info("Enqueued sentiment classification")


def enqueue_topic_extraction():
    """Enqueue topic extraction over sentiment-labeled reviews."""
    queue.enqueue("src.worker.run_topic_extraction", job_timeout="30m")
    logger.info("Enqueued topic extraction")


def enqueue_topic_clustering():
    """Enqueue per-game topic clustering + summarization."""
    queue.enqueue("src.worker.run_topic_clustering", job_timeout="30m")
    logger.info("Enqueued topic clustering")


def enqueue_report_generation():
    """Enqueue game report generation (expensive, LLM Opus)."""
    queue.enqueue("src.worker.run_report_generation", job_timeout="60m")
    logger.info("Enqueued game report generation")


def enqueue_internal_jobs():
    """Enqueue polling for web-triggered internal jobs."""
    queue.enqueue("src.worker.poll_internal_jobs", job_timeout="30m")


def enqueue_embedding_refresh():
    queue.enqueue("src.worker.run_embedding_refresh", job_timeout="60m")
    logger.info("Enqueued embedding refresh")


def enqueue_genre_aggregation():
    queue.enqueue("src.worker.run_genre_aggregation", job_timeout="10m")
    logger.info("Enqueued genre aggregation")


def enqueue_hook_extraction():
    queue.enqueue("src.worker.run_hook_extraction", job_timeout="30m")
    logger.info("Enqueued hook extraction")


def enqueue_daily_digest():
    queue.enqueue("src.worker.run_daily_digest", job_timeout="20m")
    logger.info("Enqueued daily digest")


def enqueue_scrape_social_depth():
    queue.enqueue("src.worker.run_scrape_social_depth", job_timeout="60m")
    logger.info("Enqueued social depth scrape")


def enqueue_genre_weekly_report():
    queue.enqueue("src.worker.run_genre_weekly_report", job_timeout="20m")
    logger.info("Enqueued genre weekly report")


def enqueue_project_advice():
    queue.enqueue("src.worker.run_project_advice_generation", job_timeout="60m")
    logger.info("Enqueued project advice generation")


def enqueue_asset_analysis():
    queue.enqueue("src.worker.run_asset_analysis", job_timeout="30m")
    logger.info("Enqueued asset analysis")


def enqueue_trailer_analysis():
    # Limit=5 is intentionally conservative for small VPSes (~2 core / 40G disk).
    # Each game downloads the first 60s of a trailer (~20MB), extracts 6 frames,
    # sends to GPT-4o-mini vision, and cleans up — peak disk per game is < 50MB.
    # 5 games sequentially runs in ~5-10 min total, well within resource budget.
    # Raise this (or run the worker CLI manually with a larger limit) when you
    # have spare budget; lower it if you're tight on tokens.
    queue.enqueue("src.worker.run_trailer_analysis", 5, job_timeout="60m")
    logger.info("Enqueued trailer analysis (limit=5)")


def enqueue_feishu_worker():
    queue.enqueue("src.worker.run_feishu_command_processor", job_timeout="5m")
    logger.info("Enqueued feishu command processor")


def enqueue_game_name_translate():
    queue.enqueue("src.worker.run_game_name_translate", job_timeout="30m")
    logger.info("Enqueued game name translation")


def enqueue_wechat_intelligence():
    queue.enqueue("src.worker.run_wechat_intelligence", job_timeout="20m")
    logger.info("Enqueued wechat intelligence briefing")


def enqueue_gameplay_intel():
    queue.enqueue("src.worker.run_gameplay_intel", 50, job_timeout="30m")
    logger.info("Enqueued gameplay intel batch (limit=50)")


def main():
    scheduler = BlockingScheduler()

    # === Morning run: 06:00 HKT ===
    # Google Play rankings (multiple regions, CN excluded - no Google Play in China)
    for region in ["US", "JP", "KR"]:
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

    # WeChat Mini Games — 10 charts from sj.qq.com (腾讯应用宝)
    # Ranking charts (06:40 - 06:46)
    _wechat_mini_charts = [
        (40, "hot", "wechat_mini_hot"),                 # 热门榜
        (41, "top_grossing", "wechat_mini_grossing"),   # 畅销榜
        (42, "new", "wechat_mini_new"),                 # 新游榜
        (43, "featured", "wechat_mini_featured"),       # 小游戏精选榜
        # Category listings (06:44 - 06:49)
        (44, "tag_puzzle", "wechat_mini_tag_puzzle"),        # 休闲益智
        (45, "tag_rpg", "wechat_mini_tag_rpg"),              # 角色扮演
        (46, "tag_board", "wechat_mini_tag_board"),          # 棋牌
        (47, "tag_strategy", "wechat_mini_tag_strategy"),    # 策略
        (48, "tag_adventure", "wechat_mini_tag_adventure"),  # 动作冒险
        (49, "tag_singleplayer", "wechat_mini_tag_single"),  # 单机
    ]
    for _minute, _chart, _id in _wechat_mini_charts:
        scheduler.add_job(
            enqueue_scrape, "cron", hour=6, minute=_minute,
            args=["wechat_mini", _chart, "CN"], id=_id,
        )

    # === Afternoon run: 14:00 HKT (2nd daily for major platforms) ===
    for region in ["US", "JP"]:
        scheduler.add_job(
            enqueue_scrape, "cron", hour=14, minute=0,
            args=["google_play", "top_free", region],
            id=f"gp_top_free_{region}_pm",
        )
    for region in ["CN", "US"]:
        scheduler.add_job(
            enqueue_scrape, "cron", hour=14, minute=5,
            args=["app_store", "top_free", region],
            id=f"as_top_free_{region}_pm",
        )

    # === 06:45: Fetch game details/screenshots for new games ===
    scheduler.add_job(
        enqueue_fetch_details, "cron", hour=6, minute=45,
        id="fetch_details",
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

    # === 09:00: Review scraping (after scoring so we know which games to focus on) ===
    scheduler.add_job(
        enqueue_scrape_reviews, "cron", hour=9, minute=0, id="scrape_reviews",
    )

    # === 10:00: Review NLP pipeline (sentiment -> topics -> clustering) ===
    scheduler.add_job(
        enqueue_sentiment_classification, "cron", hour=10, minute=0,
        id="sentiment_classification",
    )
    scheduler.add_job(
        enqueue_topic_extraction, "cron", hour=10, minute=30,
        id="topic_extraction",
    )
    scheduler.add_job(
        enqueue_topic_clustering, "cron", hour=11, minute=0,
        id="topic_clustering",
    )

    # === 11:30: Generate game reports (LLM Opus, expensive, limit=20) ===
    scheduler.add_job(
        enqueue_report_generation, "cron", hour=11, minute=30,
        id="report_generation",
    )

    # === Every 5 minutes: poll for web-triggered manual report generation ===
    scheduler.add_job(
        enqueue_internal_jobs, "interval", minutes=5, id="internal_jobs",
    )

    # === 08:45: Genre aggregation (after scoring) ===
    scheduler.add_job(enqueue_genre_aggregation, "cron", hour=8, minute=45, id="genre_aggregation")

    # === 09:15: Social depth scraping (after review scraping at 09:00) ===
    scheduler.add_job(enqueue_scrape_social_depth, "cron", hour=9, minute=15, id="social_depth")

    # === 11:15: Hook phrase extraction (Haiku, after social depth) ===
    scheduler.add_job(enqueue_hook_extraction, "cron", hour=11, minute=15, id="hook_extraction")

    # === 12:00: Daily digest (after all pipelines done) ===
    scheduler.add_job(enqueue_daily_digest, "cron", hour=12, minute=0, id="daily_digest")

    # === Weekly: embedding refresh (Sunday 3 AM, low-traffic window) ===
    scheduler.add_job(enqueue_embedding_refresh, "cron", day_of_week="sun", hour=3, minute=0, id="embedding_refresh")

    # === Monday 09:00: Genre weekly report (uses last week's data) ===
    scheduler.add_job(enqueue_genre_weekly_report, "cron", day_of_week="mon", hour=9, minute=0, id="genre_weekly_report")

    # === 12:30: Project advice generation (after daily digest) ===
    scheduler.add_job(enqueue_project_advice, "cron", hour=12, minute=30, id="project_advice")

    # === Every 5 days at 13:00 HKT: WeChat IAA intelligence briefing.
    #     Runs on day 1,6,11,16,21,26 (≈6 runs/month, ~80% token cut vs daily).
    #     Fires after scoring + project_advice so cross-correlation signals
    #     are fresh. 5-day cadence aligns with the 7-day rank_momentum /
    #     market_history windows the prompt already uses. ===
    scheduler.add_job(
        enqueue_wechat_intelligence, "cron",
        day="1,6,11,16,21,26", hour=13, minute=0,
        id="wechat_intelligence",
    )

    # === 08:15 HKT: Gameplay intel fact sheets (Sonnet, per-game, 50 games).
    #                Runs after fetch_details (06:45) so editor_intro /
    #                screenshots are fresh, and before alerts (08:30). ===
    scheduler.add_job(
        enqueue_gameplay_intel, "cron", hour=8, minute=15,
        id="gameplay_intel",
    )

    # === Tuesday 02:00: Asset analysis (weekly, low-traffic window, cheap but slow) ===
    scheduler.add_job(enqueue_asset_analysis, "cron", day_of_week="tue", hour=2, minute=0, id="asset_analysis")

    # === Wednesday 03:00: Trailer hook analysis (weekly, after asset_analysis, bandwidth-heavy) ===
    scheduler.add_job(enqueue_trailer_analysis, "cron", day_of_week="wed", hour=3, minute=0, id="trailer_analysis")

    # === Every 1 minute: Feishu command processor (bot needs quick response) ===
    scheduler.add_job(enqueue_feishu_worker, "interval", minutes=1, id="feishu_worker")

    # === 07:15 HKT: Game name EN → ZH translation (after overnight scrapes,
    #                before scoring so dashboard shows Chinese names) ===
    scheduler.add_job(
        enqueue_game_name_translate, "cron", hour=7, minute=15, id="game_name_translate",
    )

    logger.info("Scheduler started. Jobs registered:")
    for job in scheduler.get_jobs():
        logger.info(f"  {job.id}: {job.trigger}")

    scheduler.start()


if __name__ == "__main__":
    main()
