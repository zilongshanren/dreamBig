"""Game report generation: context assembly → Poe Opus → structured write-back.

For each high-potential game, produces a single structured report that
downstream UI (game detail page, IAA advisor page) consumes. All outputs
are evidence-backed — low-confidence reports are discarded.
"""

from __future__ import annotations

import asyncio
import hashlib
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
from src.llm.prompts.project_advice import (
    PROJECT_ADVICE_PROMPT,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = GAME_REPORT_PROMPT.version  # "v1"

# Thresholds
MIN_REVIEWS_REQUIRED = 10
MIN_CONFIDENCE_TO_STORE = 0.4  # discard anything below
MIN_POTENTIAL_SCORE = 60       # only report games with real potential

# Limits on prompt content to keep token usage bounded
MAX_TOPICS_PER_SENTIMENT = 8
MAX_REVIEW_TOPICS_FETCHED = 30
SOCIAL_WINDOW_DAYS = 7

# Inline project_advice merge — populated alongside the report in a single
# Sonnet call. Cuts roughly half of the per-game LLM cost vs the legacy
# two-call pipeline. Disable via env if the merged prompt regresses.
SIMILAR_GAMES_LIMIT = 5
PROJECT_ADVICE_PROMPT_VERSION = PROJECT_ADVICE_PROMPT.version


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

        # 1.5. Content-hash short-circuit: skip if inputs are identical to
        # the previously persisted report. Caches across runs.
        new_hash = _context_hash(context)
        try:
            with psycopg.connect(self.db_url) as conn:
                if _existing_report_hash(conn, game_id) == new_hash:
                    logger.info(
                        f"Report inputs unchanged for game {game_id} "
                        f"({context['game_name']}) — hash {new_hash}, skipping LLM"
                    )
                    return None
        except psycopg.Error:
            # Hash check is best-effort; never block generation on it.
            pass

        # 2. Build messages — merged prompt asks for game_report + project_advice
        # in a single Sonnet round-trip when we have both potential_score and
        # similar_games (we always do for eligible games).
        messages = build_game_report_messages(
            game_name=context["game_name"],
            genre=context["genre"],
            platform_summary=context["platform_summary"],
            review_topics=context["review_topics"],
            social_hot_words=context["social_hot_words"],
            similar_games=context.get("similar_games"),
            potential_score=context.get("potential_score"),
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
                    content_hash=new_hash,
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
    """Fetch everything needed to render the merged prompt.

    Returns None if the game has insufficient data (no listings, no review
    topics, etc.) — we'd rather skip than ask the LLM to hallucinate.

    Also fetches the inputs needed for the inline project_advice merge:
    the game's latest potential score and a small list of similar games.
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

    # 5. Project-advice inputs: latest potential score + similar games.
    # Pulled here so the merged LLM call can produce both deliverables at once.
    score_row = conn.execute(
        """
        SELECT overall_score
        FROM potential_scores
        WHERE game_id = %s
        ORDER BY scored_at DESC
        LIMIT 1
        """,
        (game_id,),
    ).fetchone()
    potential_score = int(score_row[0]) if score_row else 0

    similar_games = _fetch_similar_games(conn, game_id)

    return {
        "game_name": game_name,
        "genre": genre,
        "developer": developer or "",
        "iaa_suitability": iaa_suitability,
        "platform_summary": platform_summary,
        "review_topics": review_topics_str,
        "social_hot_words": social_hot_words,
        "potential_score": potential_score,
        "similar_games": similar_games,
    }


def _fetch_similar_games(
    conn: psycopg.Connection, game_id: int
) -> list[dict]:
    """Fetch up to N similar games for the inline project_advice merge.

    Mirrors the logic in project_advice_generator._fetch_similar_games but
    swallows all errors — the report should still generate without similar
    games (the prompt then produces null project_advice).
    """
    try:
        has_embedding = conn.execute(
            "SELECT 1 FROM game_embeddings WHERE game_id = %s",
            (game_id,),
        ).fetchone()
    except psycopg.Error:
        has_embedding = None

    if has_embedding is not None:
        try:
            rows = conn.execute(
                """
                SELECT g.id,
                       COALESCE(g.name_en, g.name_zh, 'Unknown') AS name,
                       g.iaa_grade,
                       COALESCE(ps.overall_score, 0) AS score
                FROM game_embeddings target
                JOIN game_embeddings other ON other.game_id != target.game_id
                JOIN games g ON g.id = other.game_id
                LEFT JOIN potential_scores ps
                       ON ps.game_id = g.id AND ps.scored_at = CURRENT_DATE
                WHERE target.game_id = %s
                  AND EXISTS (
                      SELECT 1 FROM game_reports WHERE game_id = g.id
                  )
                ORDER BY target.embedding <=> other.embedding
                LIMIT %s
                """,
                (game_id, SIMILAR_GAMES_LIMIT),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "name": r[1],
                    "iaa_grade": r[2],
                    "overall_score": int(r[3]) if r[3] is not None else 0,
                }
                for r in rows
            ]
        except psycopg.Error:
            pass

    try:
        rows = conn.execute(
            """
            SELECT g.id,
                   COALESCE(g.name_en, g.name_zh, 'Unknown') AS name,
                   g.iaa_grade,
                   COALESCE(ps.overall_score, 0) AS score
            FROM games g
            LEFT JOIN potential_scores ps
                   ON ps.game_id = g.id AND ps.scored_at = CURRENT_DATE
            WHERE g.id != %s
              AND g.genre = (SELECT genre FROM games WHERE id = %s)
            ORDER BY ps.overall_score DESC NULLS LAST
            LIMIT %s
            """,
            (game_id, game_id, SIMILAR_GAMES_LIMIT),
        ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "iaa_grade": r[2],
                "overall_score": int(r[3]) if r[3] is not None else 0,
            }
            for r in rows
        ]
    except psycopg.Error:
        return []


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
# Content-hash helpers
# ============================================================
def _context_hash(context: dict) -> str:
    """Stable hash of inputs that drive the LLM output. Re-runs that produce
    the same hash get short-circuited."""
    digest = hashlib.sha256()
    digest.update(PROMPT_VERSION.encode("utf-8"))
    digest.update(b"|")
    parts = [
        context.get("genre") or "",
        context.get("platform_summary") or "",
        context.get("review_topics") or "",
        context.get("social_hot_words") or "",
    ]
    digest.update("\n".join(parts).encode("utf-8"))
    return digest.hexdigest()[:16]


def _existing_report_hash(
    conn: psycopg.Connection, game_id: int
) -> str | None:
    row = conn.execute(
        """
        SELECT payload->>'content_hash'
        FROM game_reports
        WHERE game_id = %s
        """,
        (game_id,),
    ).fetchone()
    return row[0] if row and row[0] else None


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
    content_hash: str,
) -> None:
    """Write GameReport row + update Game structured fields.

    Writes are transactional within the caller's connection — caller must
    commit. evidence_count is computed from evidence_refs across the two
    loop fields. The content_hash is stored INSIDE the JSONB payload so
    the next run can detect "nothing changed" without a schema migration.
    """
    evidence_count = (
        len(payload.core_loop.evidence_refs)
        + len(payload.meta_loop.evidence_refs)
    )
    payload_dict = payload.model_dump()
    payload_dict["content_hash"] = content_hash

    # If the merged prompt produced inline project_advice, stamp it with the
    # legacy prompt_version + content_hash so the standalone advice processor
    # treats it as already-done and skips it.
    inline_advice = payload_dict.get("project_advice")
    if isinstance(inline_advice, dict):
        inline_advice["prompt_version"] = PROJECT_ADVICE_PROMPT_VERSION
        inline_advice["content_hash"] = content_hash
        inline_advice["model_used"] = model_used
        payload_dict["project_advice"] = inline_advice

    payload_json = json.dumps(payload_dict, ensure_ascii=False, default=str)
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
