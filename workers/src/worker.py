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
    """Collect Bilibili aggregate signals for high-potential games.

    Uses the Playwright-driven BilibiliReviewScraper (same browser session
    as the review scraper) because the old plain-httpx Bilibili search was
    getting 412 Precondition Failed on ~90% of Chinese game names from
    the HK datacenter IP. The Playwright path sails through since the
    real browser signs WBI internally.

    Games selected: top-60 by yesterday's potential score + every
    watchlisted game + every wechat_mini listing. The whole loop runs in
    one event loop so Playwright's browser stays alive across games.
    """
    logger.info("Starting social signal collection")
    from src.scrapers.reviews.bilibili import BilibiliReviewScraper

    async def _run() -> None:
        scraper = BilibiliReviewScraper()
        ok = await scraper._ensure_playwright()
        if not ok:
            logger.warning(
                "Social signals: Playwright init failed, aborting"
            )
            return
        try:
            with psycopg.connect(DB_URL) as conn:
                games = conn.execute(
                    """
                    SELECT g.id, COALESCE(g.name_zh, g.name_en) AS name
                    FROM games g
                    LEFT JOIN potential_scores ps ON ps.game_id = g.id
                      AND ps.scored_at = CURRENT_DATE - INTERVAL '1 day'
                    WHERE (ps.overall_score >= 50
                           OR EXISTS (
                                SELECT 1 FROM game_tags gt
                                WHERE gt.game_id = g.id AND gt.tag = 'watchlist'
                           )
                           OR EXISTS (
                                SELECT 1 FROM platform_listings pl
                                WHERE pl.game_id = g.id AND pl.platform = 'wechat_mini'
                           ))
                    ORDER BY ps.overall_score DESC NULLS LAST
                    LIMIT 60
                    """
                ).fetchall()

                total = 0
                for game_id, game_name in games:
                    if not game_name:
                        continue
                    try:
                        sig = await scraper.get_game_signal(game_name)
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
                            (
                                game_id,
                                "bilibili",
                                sig["video_count"],
                                sig["view_count"],
                                sig["like_count"],
                                0,
                            ),
                        )
                        conn.commit()
                        total += 1
                        if sig["video_count"] > 0:
                            logger.info(
                                f"[social_signals] '{game_name}': "
                                f"{sig['video_count']} videos, "
                                f"{sig['view_count']} views"
                            )
                    except Exception as e:
                        logger.warning(
                            f"Social signal failed for '{game_name}': {e}"
                        )
                logger.info(
                    f"Social signal collection complete: {total} games processed"
                )
        finally:
            try:
                await scraper.close()
            except Exception:
                pass

    asyncio.run(_run())


def run_ad_intel():
    """Collect ad intelligence for high-potential games.

    Same top-50 LIMIT as run_social_signals — bounds AppGrowing API costs
    (Facebook Ad Library is free but still rate-limited). Rest of the
    catalog is skipped intentionally.
    """
    logger.info("Starting ad intel collection")
    from src.scrapers.ad_intel import AdIntelScraper

    async def _run() -> None:
        import json as _json
        scraper = AdIntelScraper()
        try:
            with psycopg.connect(DB_URL) as conn:
                games = conn.execute(
                    """
                    SELECT g.id, COALESCE(g.name_en, g.name_zh) AS name
                    FROM games g
                    LEFT JOIN potential_scores ps ON ps.game_id = g.id
                      AND ps.scored_at = CURRENT_DATE - INTERVAL '1 day'
                    WHERE (ps.overall_score >= 50
                           OR EXISTS (
                                SELECT 1 FROM game_tags gt
                                WHERE gt.game_id = g.id AND gt.tag = 'watchlist'
                           ))
                    ORDER BY ps.overall_score DESC NULLS LAST
                    LIMIT 50
                    """
                ).fetchall()

                for game_id, game_name in games:
                    if not game_name:
                        continue
                    try:
                        signals = await scraper.collect_signals(game_name)
                        for sig in signals:
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
        finally:
            try:
                await scraper.close()
            except Exception:
                pass

    asyncio.run(_run())
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


def run_scrape_reviews(
    platform: str,
    platform_listing_id: int,
    platform_id: str,
    region: str = "US",
    lang: str = "en",
    limit: int = 200,
) -> int:
    """Scrape reviews for a single platform listing and insert into reviews table.

    Returns number of reviews inserted (pre-dedup).
    """
    from src.scrapers.reviews import (
        AppStoreReviewScraper,
        GooglePlayReviewScraper,
        SteamReviewScraper,
        TapTapReviewScraper,
    )

    REVIEW_SCRAPER_MAP = {
        "google_play": GooglePlayReviewScraper,
        "steam": SteamReviewScraper,
        "app_store": AppStoreReviewScraper,
        "taptap": TapTapReviewScraper,
    }

    scraper_cls = REVIEW_SCRAPER_MAP.get(platform)
    if not scraper_cls:
        logger.warning(f"No review scraper for platform '{platform}'")
        return 0

    logger.info(f"Scraping reviews: {platform}/{platform_id} listing={platform_listing_id}")
    scraper = scraper_cls()
    try:
        reviews = asyncio.run(
            scraper.scrape_reviews_safe(
                platform_id, region=region, lang=lang, limit=limit
            )
        )
    finally:
        try:
            asyncio.run(scraper.close())
        except RuntimeError:
            pass

    inserted = 0
    try:
        with psycopg.connect(DB_URL) as conn:
            for r in reviews:
                conn.execute(
                    """
                    INSERT INTO reviews
                        (platform_listing_id, external_id, rating, content,
                         author_name, helpful_count, language, posted_at, scraped_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (platform_listing_id, external_id) DO NOTHING
                    """,
                    (
                        platform_listing_id,
                        r.external_id,
                        r.rating,
                        (r.content or "")[:4000],
                        r.author_name,
                        r.helpful_count,
                        r.language,
                        r.posted_at,
                    ),
                )
                inserted += 1
            conn.commit()
            _record_job(conn, platform, "scrape_reviews", "success", inserted)
    except Exception as e:
        logger.error(f"Review insert failed for {platform}/{platform_id}: {e}")
        try:
            with psycopg.connect(DB_URL) as conn:
                _record_job(conn, platform, "scrape_reviews", "failed", inserted, str(e))
        except Exception:
            pass
        raise

    logger.info(f"Scraped {inserted} reviews for {platform}/{platform_id}")
    return inserted


def run_scrape_all_reviews(limit_per_game: int = 100) -> int:
    """Scrape reviews for all platform_listings of games above threshold score.

    Review sources:
      - google_play / app_store / steam / taptap: direct review endpoints
      - wechat_mini: WeChat has no public review API, so we use BilibiliReviewScraper
        to pull top comments on game-related Bilibili videos as a proxy signal.
    """
    import json as _json

    from src.scrapers.reviews import (
        AppStoreReviewScraper,
        BilibiliReviewScraper,
        GooglePlayReviewScraper,
        SteamReviewScraper,
        TapTapReviewScraper,
    )

    REVIEW_SCRAPER_MAP = {
        "google_play": GooglePlayReviewScraper,
        "steam": SteamReviewScraper,
        "app_store": AppStoreReviewScraper,
        "taptap": TapTapReviewScraper,
        "wechat_mini": BilibiliReviewScraper,
    }

    logger.info("Starting bulk review scrape for high-potential games")
    with psycopg.connect(DB_URL) as conn:
        # Only scrape reviews for games with potential score >= 50 OR watchlisted.
        # For wechat_mini we also need g.name_zh so the Bilibili scraper can
        # search by Chinese name rather than Tencent app_id.
        listings = conn.execute(
            """
            SELECT pl.id, pl.platform, pl.platform_id, pl.metadata,
                   g.name_zh, g.name_en
            FROM platform_listings pl
            JOIN games g ON pl.game_id = g.id
            LEFT JOIN potential_scores ps ON ps.game_id = g.id AND ps.scored_at = CURRENT_DATE
            WHERE pl.platform = ANY(%s)
              AND (
                  ps.overall_score >= 50
                  OR EXISTS (
                      SELECT 1 FROM game_tags gt
                      WHERE gt.game_id = g.id AND gt.tag = 'watchlist'
                  )
                  OR pl.platform = 'wechat_mini'
              )
            ORDER BY ps.overall_score DESC NULLS LAST
            LIMIT 100
            """,
            (list(REVIEW_SCRAPER_MAP.keys()),),
        ).fetchall()

        total = 0
        for listing_id, platform, platform_id_value, metadata, name_zh, name_en in listings:
            scraper_cls = REVIEW_SCRAPER_MAP.get(platform)
            if not scraper_cls:
                continue
            try:
                scraper = scraper_cls()
                # Determine region/lang from listing metadata or defaults
                region = "US"
                lang = "en"
                if metadata:
                    meta = metadata if isinstance(metadata, dict) else _json.loads(metadata)
                    region = meta.get("region", region)
                    lang = meta.get("lang", lang)

                # For wechat_mini: Bilibili scraper takes the Chinese game
                # name, not the Tencent app_id, because Bilibili has no
                # concept of game IDs — it searches by keyword.
                search_key = platform_id_value
                if platform == "wechat_mini":
                    search_key = (name_zh or name_en or "").strip()
                    region = "CN"
                    lang = "zh"
                    if not search_key:
                        logger.debug(
                            f"Skipping wechat_mini listing {listing_id}: no name"
                        )
                        continue

                reviews = asyncio.run(
                    scraper.scrape_reviews_safe(
                        search_key, region=region, lang=lang, limit=limit_per_game
                    )
                )
                try:
                    asyncio.run(scraper.close())
                except RuntimeError:
                    pass

                for r in reviews:
                    conn.execute(
                        """
                        INSERT INTO reviews
                            (platform_listing_id, external_id, rating, content,
                             author_name, helpful_count, language, posted_at, scraped_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (platform_listing_id, external_id) DO NOTHING
                        """,
                        (
                            listing_id,
                            r.external_id,
                            r.rating,
                            (r.content or "")[:4000],
                            r.author_name,
                            r.helpful_count,
                            r.language,
                            r.posted_at,
                        ),
                    )
                conn.commit()
                total += len(reviews)
                logger.info(
                    f"Scraped {len(reviews)} reviews for {platform}/{platform_id_value}"
                )
            except Exception as e:
                logger.warning(
                    f"Review scrape failed for {platform}/{platform_id_value}: {e}"
                )
                conn.rollback()

        _record_job(conn, "reviews", "scrape_reviews", "success", total)
        logger.info(f"Bulk review scrape complete: {total} reviews total")
        return total


def run_sentiment_classification() -> int:
    """Classify pending reviews. Uses Poe Haiku."""
    logger.info("Starting sentiment classification")
    from src.processors.review_analysis import run_sentiment_classification as _run
    count = _run(DB_URL)
    logger.info(f"Sentiment classification complete: {count} reviews")
    return count


def run_topic_extraction() -> int:
    """Extract topic tags for sentiment-labeled reviews. Uses Poe Haiku."""
    logger.info("Starting topic extraction")
    from src.processors.review_analysis import run_topic_extraction as _run
    count = _run(DB_URL)
    logger.info(f"Topic extraction complete: {count} reviews")
    return count


def run_topic_clustering() -> int:
    """Cluster per-game topics into summaries. Uses Poe Sonnet."""
    logger.info("Starting topic clustering")
    from src.processors.review_analysis import run_topic_clustering as _run
    count = _run(DB_URL)
    logger.info(f"Topic clustering complete: {count} summaries")
    return count


def run_report_generation(limit: int = 20) -> int:
    """Generate game reports for top-N potential games. Uses Poe Opus."""
    logger.info(f"Starting game report generation (limit={limit})")
    from src.processors.report_generator import run_report_generation as _run
    count = _run(DB_URL, limit=limit)
    logger.info(f"Report generation complete: {count} reports")
    return count


def run_embedding_refresh(limit: int = 200):
    """Regenerate embeddings for new or stale games (> 7 days old)."""
    from src.processors.embedding import run_embedding_refresh as _run
    return _run(DB_URL, limit=limit)


def run_genre_aggregation():
    """Refresh Genre rollup table."""
    from src.processors.genre_aggregation import run_genre_aggregation as _run
    return _run(DB_URL)


def run_hook_extraction(limit: int = 200):
    """Extract hook phrases for SocialContentSample rows with null hook_phrase."""
    from src.processors.hook_extraction import run_hook_extraction as _run
    return _run(DB_URL, limit=limit)


def run_daily_digest():
    """Dispatch daily digests to active subscribers."""
    from src.processors.daily_digest import run_daily_digest as _run
    return _run(DB_URL)


def run_genre_weekly_report():
    """Generate this week's genre trend report."""
    from src.processors.genre_weekly_report import run_genre_weekly_report as _run
    return _run(DB_URL)


def run_project_advice_generation(limit: int = 20):
    """Generate pursue/monitor/pass advice for top-N games with GameReports."""
    from src.processors.project_advice_generator import run_project_advice_generation as _run
    return _run(DB_URL, limit=limit)


def run_asset_analysis(limit: int = 10, screenshots_per_game: int = 2):
    """Run GPT-4o-mini vision analysis on game screenshots."""
    from src.processors.asset_analysis import run_asset_analysis as _run
    return _run(DB_URL, limit=limit, screenshots_per_game=screenshots_per_game)


def run_trailer_analysis(limit: int = 10):
    """Run trailer hook analysis for top-N high-potential games."""
    from src.processors.trailer_analysis import run_trailer_analysis as _run
    return _run(DB_URL, limit=limit)


def run_feishu_command_processor():
    """Process pending Feishu bot commands."""
    from src.processors.feishu_command_worker import run_feishu_command_processor as _run
    return _run(DB_URL)


def run_game_name_translate(limit: int = 300):
    """Translate pending game names (name_en → name_zh via Haiku)."""
    from src.processors.game_name_translate import run_game_name_translate as _run
    return _run(DB_URL, limit=limit)


def run_scrape_social_depth(limit_per_game: int = 20):
    """Scrape deep social content (video titles + hashtags + metrics) for high-potential games.

    Game count is bounded by SOCIAL_DEPTH_GAME_LIMIT env var (default 8).
    Each game costs 2 TikHub calls (Douyin + TikTok) plus free Bilibili/YouTube
    quota, so with the default 8 games × 30 days = 480 TikHub calls/month —
    fits inside a 600/month TikHub plan with 20% headroom.

    Uses name_zh for Chinese platforms (Douyin / Bilibili) and name_en for
    English platforms (YouTube / TikTok) to get high-quality search hits.
    """
    from src.scrapers.social_depth import SocialDepthScraper
    import json as _json

    game_limit = int(os.environ.get("SOCIAL_DEPTH_GAME_LIMIT", "8"))

    scraper = SocialDepthScraper()

    with psycopg.connect(DB_URL) as conn:
        # Only for high-potential games + watchlisted
        games = conn.execute(
            """
            SELECT g.id, g.name_zh, g.name_en
            FROM games g
            LEFT JOIN potential_scores ps ON ps.game_id = g.id AND ps.scored_at = CURRENT_DATE
            WHERE ps.overall_score >= 60
               OR EXISTS (SELECT 1 FROM game_tags gt WHERE gt.game_id = g.id AND gt.tag = 'watchlist')
            ORDER BY ps.overall_score DESC NULLS LAST
            LIMIT %s
            """,
            (game_limit,),
        ).fetchall()

        total = 0
        for game_id, name_zh, name_en in games:
            primary_name = name_zh or name_en
            if not primary_name:
                continue
            try:
                contents = asyncio.run(
                    scraper.fetch_all(
                        primary_name,
                        limit_per_platform=limit_per_game,
                        name_zh=name_zh,
                        name_en=name_en,
                    )
                )
                for c in contents:
                    conn.execute(
                        """
                        INSERT INTO social_content_samples
                        (game_id, platform, content_type, external_id, title, author_name, hashtags, view_count, like_count, comment_count, url, posted_at, metadata, scraped_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                        ON CONFLICT (platform, external_id) DO UPDATE SET
                            view_count = EXCLUDED.view_count,
                            like_count = EXCLUDED.like_count,
                            comment_count = EXCLUDED.comment_count,
                            scraped_at = NOW()
                        """,
                        (
                            game_id, c.platform, c.content_type, c.external_id,
                            c.title, c.author_name, c.hashtags,
                            c.view_count, c.like_count, c.comment_count,
                            c.url, c.posted_at, _json.dumps(c.metadata),
                        ),
                    )
                conn.commit()
                total += len(contents)
                logger.info(f"Social depth for '{primary_name}': {len(contents)} items")
            except Exception as e:
                logger.warning(f"Social depth failed for '{primary_name}': {e}")

    try:
        asyncio.run(scraper.close())
    except RuntimeError:
        pass
    logger.info(f"Social depth scrape complete: {total} items")
    return total


def poll_internal_jobs() -> None:
    """Poll scrape_jobs for web-triggered jobs (report_generation, etc.)."""
    import json as _json

    with psycopg.connect(DB_URL) as conn:
        rows = conn.execute(
            """
            SELECT id, job_type, error_message
            FROM scrape_jobs
            WHERE platform = 'internal' AND status = 'pending'
            ORDER BY id
            LIMIT 10
            """
        ).fetchall()

        if not rows:
            return

        logger.info(f"Polling internal jobs: found {len(rows)} pending")

        for job_id, job_type, payload_str in rows:
            try:
                payload = _json.loads(payload_str or "{}")
                conn.execute(
                    "UPDATE scrape_jobs SET status='running', started_at=NOW() WHERE id=%s",
                    (job_id,),
                )
                conn.commit()

                if job_type == "report_generation":
                    from src.processors.report_generator import ReportGenerator

                    game_id = int(payload.get("gameId"))
                    gen = ReportGenerator(DB_URL)
                    asyncio.run(gen.generate_for_game(game_id))
                    asyncio.run(gen.client.close())
                    conn.execute(
                        "UPDATE scrape_jobs SET status='success', items_scraped=1, finished_at=NOW() WHERE id=%s",
                        (job_id,),
                    )
                    logger.info(f"Internal job {job_id}: report_generation for game {game_id} done")
                elif job_type == "experiment_suggest":
                    game_id = int(payload.get("gameId"))
                    from src.processors.experiment_advisor import run_experiment_suggest
                    suggestions = run_experiment_suggest(DB_URL, game_id)
                    if suggestions is not None:
                        # Store suggestions back into scrape_jobs.error_message as JSON
                        conn.execute(
                            "UPDATE scrape_jobs SET status='success', items_scraped=%s, error_message=%s, finished_at=NOW() WHERE id=%s",
                            (len(suggestions), _json.dumps({"suggestions": suggestions}), job_id),
                        )
                    else:
                        conn.execute(
                            "UPDATE scrape_jobs SET status='failed', error_message='suggest_failed', finished_at=NOW() WHERE id=%s",
                            (job_id,),
                        )
                elif job_type == "feishu_command":
                    # Feishu commands are processed in batch, not per-job. Just run the processor.
                    from src.processors.feishu_command_worker import run_feishu_command_processor
                    count = run_feishu_command_processor(DB_URL)
                    conn.execute(
                        "UPDATE scrape_jobs SET status='success', items_scraped=%s, finished_at=NOW() WHERE id=%s",
                        (count, job_id),
                    )
                else:
                    conn.execute(
                        "UPDATE scrape_jobs SET status='failed', error_message='unknown job_type', finished_at=NOW() WHERE id=%s",
                        (job_id,),
                    )
                    logger.warning(f"Internal job {job_id}: unknown job_type '{job_type}'")

                conn.commit()
            except Exception as e:
                conn.execute(
                    "UPDATE scrape_jobs SET status='failed', error_message=%s, finished_at=NOW() WHERE id=%s",
                    (str(e), job_id),
                )
                conn.commit()
                logger.error(f"Internal job {job_id} failed: {e}")


if __name__ == "__main__":
    # For local testing: run a single scrape
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "fetch_details":
        run_fetch_details()
    elif len(sys.argv) >= 2 and sys.argv[1] == "scrape_reviews":
        run_scrape_all_reviews()
    elif len(sys.argv) >= 2 and sys.argv[1] == "classify_sentiment":
        print(run_sentiment_classification())
    elif len(sys.argv) >= 2 and sys.argv[1] == "extract_topics":
        print(run_topic_extraction())
    elif len(sys.argv) >= 2 and sys.argv[1] == "cluster_topics":
        print(run_topic_clustering())
    elif len(sys.argv) >= 2 and sys.argv[1] == "generate_reports":
        _limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        print(run_report_generation(limit=_limit))
    elif len(sys.argv) >= 2 and sys.argv[1] == "poll_internal":
        poll_internal_jobs()
    elif len(sys.argv) >= 2 and sys.argv[1] == "refresh_embeddings":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 200
        print(run_embedding_refresh(limit=limit))
    elif len(sys.argv) >= 2 and sys.argv[1] == "aggregate_genres":
        print(run_genre_aggregation())
    elif len(sys.argv) >= 2 and sys.argv[1] == "extract_hooks":
        print(run_hook_extraction())
    elif len(sys.argv) >= 2 and sys.argv[1] == "daily_digest":
        print(run_daily_digest())
    elif len(sys.argv) >= 2 and sys.argv[1] == "scrape_social_depth":
        print(run_scrape_social_depth())
    elif len(sys.argv) >= 2 and sys.argv[1] == "genre_weekly":
        print(run_genre_weekly_report())
    elif len(sys.argv) >= 2 and sys.argv[1] == "project_advice":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        print(run_project_advice_generation(limit=limit))
    elif len(sys.argv) >= 2 and sys.argv[1] == "asset_analysis":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        print(run_asset_analysis(limit=limit))
    elif len(sys.argv) >= 2 and sys.argv[1] == "trailer_analysis":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        print(run_trailer_analysis(limit=limit))
    elif len(sys.argv) >= 2 and sys.argv[1] == "feishu_worker":
        print(run_feishu_command_processor())
    elif len(sys.argv) >= 2 and sys.argv[1] == "translate_names":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 300
        print(run_game_name_translate(limit=limit))
    elif len(sys.argv) >= 3:
        platform = sys.argv[1]
        chart_type = sys.argv[2]
        region = sys.argv[3] if len(sys.argv) > 3 else "US"
        run_scrape_job(platform, chart_type, region)
    else:
        print("Usage: python -m src.worker <platform> <chart_type> [region]")
        print("       python -m src.worker fetch_details")
        print("       python -m src.worker scrape_reviews")
        print("       python -m src.worker classify_sentiment")
        print("       python -m src.worker extract_topics")
        print("       python -m src.worker cluster_topics")
        print("       python -m src.worker generate_reports [limit]")
        print("       python -m src.worker poll_internal")
        print("       python -m src.worker refresh_embeddings [limit]")
        print("       python -m src.worker aggregate_genres")
        print("       python -m src.worker extract_hooks")
        print("       python -m src.worker daily_digest")
        print("       python -m src.worker scrape_social_depth")
        print("       python -m src.worker genre_weekly")
        print("       python -m src.worker project_advice [limit]")
        print("       python -m src.worker asset_analysis [limit]")
        print("       python -m src.worker trailer_analysis [limit]")
        print("       python -m src.worker feishu_worker")
        print("       python -m src.worker translate_names [limit]")
        print("Example: python -m src.worker app_store top_free US")
