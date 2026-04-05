"""Game report generation: context assembly → Poe Opus → structured write-back.

For each high-potential game, produces a single structured report that
downstream UI (game detail page, IAA advisor page) consumes. All outputs
are evidence-backed — low-confidence reports are discarded.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

import psycopg

from src.llm import PoeClient, get_model_for_task
from src.llm.poe_client import LLMError
from src.llm.prompts.game_report import (
    GAME_REPORT_PROMPT,
    GameReport as GameReportSchema,
    build_game_report_messages,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = GAME_REPORT_PROMPT.version  # "v1"

# Thresholds
MIN_REVIEWS_REQUIRED = 10
MIN_CONFIDENCE_TO_STORE = 0.4  # discard anything below
MIN_POTENTIAL_SCORE = 50       # only report games with real potential

# Limits on prompt content to keep token usage bounded
MAX_TOPICS_PER_SENTIMENT = 8
MAX_REVIEW_TOPICS_FETCHED = 30
SOCIAL_WINDOW_DAYS = 7


class ReportGenerator:
    """Orchestrates context assembly, LLM call, and persistence for game reports."""

    def __init__(self, db_url: str, poe_client: PoeClient | None = None):
        self.db_url = db_url
        self.client = poe_client or PoeClient()
        self.model = get_model_for_task("game_report_generation")

    async def generate_for_game(self, game_id: int) -> dict | None:
        """Generate and persist a report for one game.

        Returns the payload dict on success, or None when skipped
        (insufficient data, low confidence, or LLM failure).
        """
        # 1. Gather context (sync DB reads)
        try:
            with psycopg.connect(self.db_url) as conn:
                context = _gather_context(conn, game_id)
        except psycopg.errors.UndefinedTable as exc:
            logger.warning(
                f"review_topic_summaries table missing, skipping game {game_id}: {exc}"
            )
            return None
        except psycopg.Error as exc:
            logger.error(f"DB error gathering context for game {game_id}: {exc}")
            return None

        if context is None:
            logger.info(f"Insufficient context for game {game_id}, skipping report")
            return None

        # 2. Build messages
        messages = build_game_report_messages(
            game_name=context["game_name"],
            genre=context["genre"],
            platform_summary=context["platform_summary"],
            review_topics=context["review_topics"],
            social_hot_words=context["social_hot_words"],
        )

        # 3. Call chat_json
        try:
            report = await self.client.chat_json(
                messages=messages,
                model=self.model,
                schema=GameReportSchema,
            )
        except LLMError as exc:
            logger.error(
                f"LLM call failed for game {game_id} ({context['game_name']}): {exc}"
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.error(
                f"Unexpected error calling LLM for game {game_id} "
                f"({context['game_name']}): {exc}"
            )
            return None

        # 4. Validate confidence
        if report.overall_confidence < MIN_CONFIDENCE_TO_STORE:
            logger.info(
                f"Report confidence {report.overall_confidence:.2f} below threshold "
                f"{MIN_CONFIDENCE_TO_STORE} for game {game_id} "
                f"({context['game_name']}), discarding"
            )
            return None

        # 5. Compute tokens/cost from the most recent call
        model_usage = self.client.cost_tracker.by_model.get(self.model)
        tokens_used = 0
        cost_usd = 0.0
        if model_usage is not None:
            tokens_used = model_usage.input_tokens + model_usage.output_tokens
            cost_usd = model_usage.cost_usd

        # 6. Persist
        try:
            with psycopg.connect(self.db_url) as conn:
                _persist_report(
                    conn=conn,
                    game_id=game_id,
                    payload=report,
                    model_used=self.model,
                    tokens=tokens_used,
                    cost=cost_usd,
                )
                conn.commit()
        except psycopg.Error as exc:
            logger.error(f"Failed to persist report for game {game_id}: {exc}")
            return None

        logger.info(
            f"Report written for game {game_id} ({context['game_name']}) "
            f"confidence={report.overall_confidence:.2f} "
            f"grade={report.iaa_advice.overall_grade}"
        )
        return report.model_dump()

    async def generate_for_all_eligible(self, limit: int = 20) -> int:
        """Generate reports for top-N eligible games. Returns count written."""
        try:
            with psycopg.connect(self.db_url) as conn:
                eligible = _find_eligible_games(conn, limit=limit)
        except psycopg.errors.UndefinedTable as exc:
            logger.warning(
                f"Eligibility query failed (table missing): {exc}. "
                "Report generation cannot proceed."
            )
            return 0
        except psycopg.Error as exc:
            logger.error(f"Eligibility query failed: {exc}")
            return 0

        if not eligible:
            logger.info("No eligible games for report generation")
            return 0

        logger.info(
            f"Starting report generation for {len(eligible)} eligible games "
            f"(prompt_version={PROMPT_VERSION}, model={self.model})"
        )

        written = 0
        for game_id, game_name in eligible:
            try:
                payload = await self.generate_for_game(game_id)
                if payload is not None:
                    written += 1
            except Exception as exc:  # noqa: BLE001
                # One game failing must never stop the batch.
                logger.error(
                    f"Report generation failed for game {game_id} ({game_name}): {exc}"
                )
                continue
            # Log running cost after every call for observability
            logger.info(
                f"Cumulative {self.client.cost_tracker.summary()} "
                f"(after game {game_id})"
            )

        return written


# ============================================================
# Context gathering
# ============================================================
def _gather_context(conn: psycopg.Connection, game_id: int) -> dict | None:
    """Fetch everything needed to render the prompt.

    Returns None if the game has insufficient data (no listings, no review
    topics, etc.) — we'd rather skip than ask the LLM to hallucinate.
    """
    # 1. Game base info
    game_row = conn.execute(
        """
        SELECT COALESCE(name_en, name_zh, 'Unknown') AS name,
               genre,
               developer,
               iaa_suitability
        FROM games
        WHERE id = %s
        """,
        (game_id,),
    ).fetchone()

    if game_row is None:
        logger.warning(f"Game {game_id} not found")
        return None

    game_name, genre, developer, iaa_suitability = game_row
    genre = genre or "unknown"

    # 2. Platform listings
    listing_rows = conn.execute(
        """
        SELECT platform, name, rating, rating_count, download_est, metadata
        FROM platform_listings
        WHERE game_id = %s
        ORDER BY COALESCE(rating_count, 0) DESC
        """,
        (game_id,),
    ).fetchall()

    if not listing_rows:
        logger.info(f"Game {game_id} has no platform listings, skipping")
        return None

    platform_summary = _format_platform_summary(listing_rows)

    # 3. Top review topics
    topic_rows = conn.execute(
        """
        SELECT topic, sentiment, sample_review_ids, snippet, review_count
        FROM review_topic_summaries
        WHERE game_id = %s
        ORDER BY review_count DESC
        LIMIT %s
        """,
        (game_id, MAX_REVIEW_TOPICS_FETCHED),
    ).fetchall()

    if not topic_rows:
        logger.info(f"Game {game_id} has no review topic summaries, skipping")
        return None

    review_topics_str = _format_review_topics(topic_rows)

    # 4. Social signals (latest per platform, last N days)
    social_rows = conn.execute(
        """
        SELECT platform,
               SUM(view_count)::BIGINT AS total_views,
               SUM(video_count)::INT AS total_videos,
               MAX(signal_date) AS last_date
        FROM social_signals
        WHERE game_id = %s
          AND signal_date >= CURRENT_DATE - (%s::INT * INTERVAL '1 day')
        GROUP BY platform
        ORDER BY total_views DESC
        """,
        (game_id, SOCIAL_WINDOW_DAYS),
    ).fetchall()

    social_hot_words = _format_social_signals(social_rows)

    return {
        "game_name": game_name,
        "genre": genre,
        "developer": developer or "",
        "iaa_suitability": iaa_suitability,
        "platform_summary": platform_summary,
        "review_topics": review_topics_str,
        "social_hot_words": social_hot_words,
    }


def _format_platform_summary(rows: list[tuple]) -> str:
    """Format platform listings as human-readable multi-line string.

    Example lines:
        - app_store (US): rating 4.6 (12.3k reviews), download est 1M+
        - taptap (CN): rating 8.7 (3.2k reviews)
    """
    lines: list[str] = []
    for platform, name, rating, rating_count, download_est, metadata in rows:
        # Try to extract region from metadata (falls back to empty)
        region = ""
        if isinstance(metadata, dict):
            region = (metadata.get("region") or metadata.get("country") or "").upper()
        elif metadata:
            try:
                md = json.loads(metadata) if isinstance(metadata, str) else {}
                region = (md.get("region") or md.get("country") or "").upper()
            except (json.JSONDecodeError, TypeError):
                region = ""

        region_part = f" ({region})" if region else ""

        parts: list[str] = []
        if rating is not None:
            rating_float = float(rating)
            count_txt = _fmt_count(rating_count) if rating_count else "0"
            parts.append(f"rating {rating_float:.1f} ({count_txt} reviews)")

        if download_est is not None:
            parts.append(f"download est {_fmt_count(download_est)}+")

        detail = ", ".join(parts) if parts else "no metrics"
        lines.append(f"- {platform}{region_part}: {detail}")

    return "\n".join(lines) if lines else "No platform listings available"


def _format_review_topics(rows: list[tuple]) -> str:
    """Format review topics grouped by sentiment with evidence refs.

    Expected format:
        POSITIVE topics:
        - level_design (83 mentions, review:12,review:45): 玩家普遍赞赏...
        - progression (67 mentions, review:23,review:78): 成长节奏流畅...
        NEGATIVE topics:
        - ads_intrusive (54 mentions, review:91,review:102): 广告频繁...
    """
    positive: list[tuple] = []
    negative: list[tuple] = []

    for topic, sentiment, sample_ids, snippet, review_count in rows:
        entry = (topic, sample_ids or [], snippet or "", review_count)
        sentiment_norm = (sentiment or "").lower().strip()
        if sentiment_norm == "positive":
            positive.append(entry)
        elif sentiment_norm == "negative":
            negative.append(entry)
        # Ignore neutral / unknown sentiments

    positive = positive[:MAX_TOPICS_PER_SENTIMENT]
    negative = negative[:MAX_TOPICS_PER_SENTIMENT]

    sections: list[str] = []

    if positive:
        sections.append("POSITIVE topics:")
        for topic, sample_ids, snippet, count in positive:
            refs = _format_review_refs(sample_ids)
            sections.append(
                f"- {topic} ({count} mentions, {refs}): {snippet}"
            )

    if negative:
        sections.append("NEGATIVE topics:")
        for topic, sample_ids, snippet, count in negative:
            refs = _format_review_refs(sample_ids)
            sections.append(
                f"- {topic} ({count} mentions, {refs}): {snippet}"
            )

    if not sections:
        return "No review topics available"

    return "\n".join(sections)


def _format_review_refs(sample_ids: list) -> str:
    """Format a list of review IDs as 'review:12,review:45' (capped)."""
    if not sample_ids:
        return "no refs"
    # Cap to 4 refs per topic to keep prompt compact
    capped = list(sample_ids)[:4]
    return ",".join(f"review:{i}" for i in capped)


def _format_social_signals(rows: list[tuple]) -> str:
    """Format social signals aggregated by platform.

    Example:
        douyin: 2.3M views, 156 videos (last 7 days)
        bilibili: 450k views, 42 videos (last 7 days)
    """
    if not rows:
        return "No social data available"

    lines: list[str] = []
    for platform, total_views, total_videos, _last_date in rows:
        views = _fmt_count(total_views or 0)
        videos = total_videos or 0
        lines.append(
            f"{platform}: {views} views, {videos} videos (last {SOCIAL_WINDOW_DAYS} days)"
        )
    return "\n".join(lines)


def _fmt_count(n: int | float | None) -> str:
    """Format a count with K/M suffixes."""
    if n is None:
        return "0"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


# ============================================================
# Eligibility query
# ============================================================
def _find_eligible_games(
    conn: psycopg.Connection, limit: int
) -> list[tuple[int, str]]:
    """Return (game_id, name) for games eligible for report generation.

    Eligibility:
    - potential_scores today exists AND overall_score >= MIN_POTENTIAL_SCORE
    - Game has at least 1 review_topic_summary row
    - Game does NOT have a recent report (within 7 days) for this prompt version
    """
    rows = conn.execute(
        """
        SELECT g.id, COALESCE(g.name_en, g.name_zh, 'Unknown') AS name
        FROM games g
        JOIN potential_scores ps ON g.id = ps.game_id
        WHERE ps.scored_at = CURRENT_DATE
          AND ps.overall_score >= %s
          AND EXISTS (
              SELECT 1 FROM review_topic_summaries rts
              WHERE rts.game_id = g.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM game_reports gr
              WHERE gr.game_id = g.id
                AND gr.prompt_version = %s
                AND gr.generated_at >= NOW() - INTERVAL '7 days'
          )
        ORDER BY ps.overall_score DESC
        LIMIT %s
        """,
        (MIN_POTENTIAL_SCORE, PROMPT_VERSION, limit),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


# ============================================================
# Persistence
# ============================================================
def _persist_report(
    conn: psycopg.Connection,
    game_id: int,
    payload: GameReportSchema,
    model_used: str,
    tokens: int,
    cost: float,
) -> None:
    """Write GameReport row + update Game structured fields.

    Writes are transactional within the caller's connection — caller must
    commit. evidence_count is computed from evidence_refs across the two
    loop fields.
    """
    evidence_count = (
        len(payload.core_loop.evidence_refs)
        + len(payload.meta_loop.evidence_refs)
    )
    payload_json = payload.model_dump_json()
    confidence = float(payload.overall_confidence)

    conn.execute(
        """
        INSERT INTO game_reports (
            game_id, payload, prompt_version, model_used,
            evidence_count, confidence, tokens_used, cost_usd, generated_at
        )
        VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (game_id) DO UPDATE SET
            payload = EXCLUDED.payload,
            prompt_version = EXCLUDED.prompt_version,
            model_used = EXCLUDED.model_used,
            evidence_count = EXCLUDED.evidence_count,
            confidence = EXCLUDED.confidence,
            tokens_used = EXCLUDED.tokens_used,
            cost_usd = EXCLUDED.cost_usd,
            generated_at = EXCLUDED.generated_at
        """,
        (
            game_id,
            payload_json,
            PROMPT_VERSION,
            model_used,
            evidence_count,
            confidence,
            int(tokens),
            round(float(cost), 4),
            datetime.now(),
        ),
    )

    # Update the Game's structured fields so list/detail pages can render
    # them without re-parsing the JSON payload.
    conn.execute(
        """
        UPDATE games
        SET positioning = %s,
            core_loop = %s,
            meta_loop = %s,
            pleasure_points = %s,
            replay_drivers = %s,
            iaa_grade = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (
            payload.positioning,
            payload.core_loop.description,
            payload.meta_loop.description,
            list(payload.pleasure_points),
            list(payload.replay_drivers),
            payload.iaa_advice.overall_grade,
            game_id,
        ),
    )


# ============================================================
# Sync entry points for worker / scheduler
# ============================================================
def run_report_generation(db_url: str, limit: int = 20) -> int:
    """Generate game reports for top-N eligible games. Entry for rq worker."""
    return asyncio.run(_generate_all_async(db_url, limit))


async def _generate_all_async(db_url: str, limit: int) -> int:
    generator = ReportGenerator(db_url)
    try:
        count = await generator.generate_for_all_eligible(limit=limit)
        logger.info(
            f"Report generation: {count} reports written. "
            f"{generator.client.cost_tracker.summary()}"
        )
        return count
    finally:
        await generator.client.close()


__all__ = [
    "ReportGenerator",
    "run_report_generation",
    "PROMPT_VERSION",
    "MIN_CONFIDENCE_TO_STORE",
    "MIN_POTENTIAL_SCORE",
]
