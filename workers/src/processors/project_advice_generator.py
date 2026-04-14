"""Project advice generator — adds pursue/monitor/pass recommendation to GameReport.payload.

For each Top game with an existing GameReport, call an Opus-tier LLM to
produce a project-level decision (立项/观察/放弃). The advice is merged
INTO the existing GameReport.payload under the `project_advice` key so
no schema change is required — downstream UIs read it directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

import psycopg

from src.llm import PoeClient, get_model_for_task
from src.llm.poe_client import LLMError
from src.llm.prompts.project_advice import (
    PROJECT_ADVICE_PROMPT,
    ProjectAdvice,
    build_project_advice_messages,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = PROJECT_ADVICE_PROMPT.version  # "v1"
MIN_CONFIDENCE = 0.4
SIMILAR_GAMES_LIMIT = 5
MIN_POTENTIAL_SCORE = 60  # only spend Sonnet on high-potential games


class ProjectAdviceGenerator:
    """Generates pursue/monitor/pass recommendations for games that already have a GameReport."""

    def __init__(self, db_url: str, client: PoeClient | None = None):
        self.db_url = db_url
        self.client = client or PoeClient()
        self.model = get_model_for_task("iaa_advice")

    async def generate_for_game(self, game_id: int) -> dict | None:
        """Generate project advice for one game, merge into existing GameReport.payload.

        Returns the advice dict on success, or None when skipped
        (missing report, already fresh, low confidence, or LLM failure).
        """
        # 1. Fetch existing GameReport + game metadata
        try:
            with psycopg.connect(self.db_url) as conn:
                context = _gather_context(conn, game_id)
        except psycopg.Error as exc:
            logger.error(f"DB error gathering context for game {game_id}: {exc}")
            return None

        if context is None:
            logger.info(
                f"Game {game_id} has no existing GameReport, skipping project advice"
            )
            return None

        # 2. Skip if payload already has project_advice with matching prompt version
        existing_advice = context["existing_payload"].get("project_advice")
        if (
            isinstance(existing_advice, dict)
            and existing_advice.get("prompt_version") == PROMPT_VERSION
        ):
            logger.info(
                f"Game {game_id} ({context['game_name']}) already has "
                f"project_advice v{PROMPT_VERSION}, skipping"
            )
            return None

        # 2.5. Content-hash short-circuit: if inputs match the previously
        # persisted advice, don't burn a Sonnet call.
        new_hash = _advice_hash(context)
        if (
            isinstance(existing_advice, dict)
            and existing_advice.get("content_hash") == new_hash
        ):
            logger.info(
                f"Project advice inputs unchanged for game {game_id} "
                f"({context['game_name']}) — hash {new_hash}, skipping LLM"
            )
            return None

        # 3. Build messages
        messages = build_project_advice_messages(
            game_name=context["game_name"],
            genre=context["genre"],
            game_report_payload=context["existing_payload"],
            similar_games=context["similar_games"],
            potential_score=context["potential_score"],
            platform_summary=context["platform_summary"],
        )

        # 4. Call LLM
        try:
            advice = await self.client.chat_json(
                messages=messages,
                model=self.model,
                schema=ProjectAdvice,
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

        # 5. Validate confidence
        if advice.confidence < MIN_CONFIDENCE:
            logger.info(
                f"Low confidence ({advice.confidence:.2f}) project_advice for "
                f"game {game_id} ({context['game_name']}), discarding"
            )
            return None

        # 6. Merge advice into existing payload and persist
        advice_dict = advice.model_dump()
        advice_dict["prompt_version"] = PROMPT_VERSION
        advice_dict["content_hash"] = new_hash

        merged_payload = dict(context["existing_payload"])
        merged_payload["project_advice"] = advice_dict

        try:
            with psycopg.connect(self.db_url) as conn:
                conn.execute(
                    """
                    UPDATE game_reports
                    SET payload = %s::jsonb, generated_at = NOW()
                    WHERE game_id = %s
                    """,
                    (json.dumps(merged_payload, default=str), game_id),
                )
                conn.commit()
        except psycopg.Error as exc:
            logger.error(
                f"Failed to persist project_advice for game {game_id}: {exc}"
            )
            return None

        logger.info(
            f"Project advice written for game {game_id} ({context['game_name']}) "
            f"recommendation={advice.recommendation} "
            f"confidence={advice.confidence:.2f}"
        )
        return advice_dict

    async def generate_for_all(self, limit: int = 20) -> int:
        """Generate advice for top N games with GameReports lacking project_advice."""
        try:
            with psycopg.connect(self.db_url) as conn:
                eligible = _find_eligible_games(conn, limit=limit)
        except psycopg.Error as exc:
            logger.error(f"Eligibility query failed: {exc}")
            return 0

        if not eligible:
            logger.info("No eligible games for project advice generation")
            return 0

        logger.info(
            f"Starting project advice generation for {len(eligible)} games "
            f"(prompt_version={PROMPT_VERSION}, model={self.model})"
        )

        written = 0
        for game_id, game_name in eligible:
            try:
                advice = await self.generate_for_game(game_id)
                if advice is not None:
                    written += 1
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"Project advice generation failed for game {game_id} "
                    f"({game_name}): {exc}"
                )
                continue
            logger.info(
                f"Cumulative {self.client.cost_tracker.summary()} "
                f"(after game {game_id})"
            )

        return written


# ============================================================
# Context gathering
# ============================================================
def _gather_context(
    conn: psycopg.Connection, game_id: int
) -> dict | None:
    """Fetch everything needed to render the project advice prompt."""
    # 1. Game base info + existing GameReport payload
    row = conn.execute(
        """
        SELECT COALESCE(g.name_en, g.name_zh, 'Unknown') AS name,
               g.genre,
               gr.payload
        FROM games g
        JOIN game_reports gr ON gr.game_id = g.id
        WHERE g.id = %s
        """,
        (game_id,),
    ).fetchone()

    if row is None:
        return None

    game_name, genre, payload = row
    if isinstance(payload, dict):
        existing_payload = payload
    elif isinstance(payload, str):
        existing_payload = json.loads(payload)
    else:
        existing_payload = {}

    # 2. Latest potential score
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

    # 3. Similar games (via embedding if available, else genre fallback)
    similar_games = _fetch_similar_games(conn, game_id)

    # 4. Platform summary
    platform_summary = _build_platform_summary(conn, game_id)

    return {
        "game_name": game_name,
        "genre": genre or "unknown",
        "existing_payload": existing_payload,
        "potential_score": potential_score,
        "similar_games": similar_games,
        "platform_summary": platform_summary,
    }


def _fetch_similar_games(
    conn: psycopg.Connection, game_id: int
) -> list[dict]:
    """Fetch up to N similar games. Uses GameEmbedding cosine if available,
    otherwise falls back to same-genre top scorers.
    """
    # Check if target game has an embedding.
    has_embedding = conn.execute(
        "SELECT 1 FROM game_embeddings WHERE game_id = %s",
        (game_id,),
    ).fetchone()

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
        except psycopg.Error as exc:
            logger.warning(
                f"Embedding-based similarity query failed for game {game_id}: {exc}. "
                "Falling back to genre match."
            )

    # Genre fallback
    try:
        rows = conn.execute(
            """
            SELECT g.id,
                   COALESCE(g.name_en, g.name_zh, 'Unknown') AS name,
                   g.iaa_grade,
                   COALESCE(ps.overall_score, 0) AS score
            FROM games g
            JOIN game_reports gr ON gr.game_id = g.id
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
    except psycopg.Error as exc:
        logger.warning(
            f"Genre-based similarity fallback failed for game {game_id}: {exc}"
        )
        return []


def _build_platform_summary(
    conn: psycopg.Connection, game_id: int
) -> str:
    """One-line summary per platform listing."""
    try:
        rows = conn.execute(
            """
            SELECT platform, rating, rating_count, download_est
            FROM platform_listings
            WHERE game_id = %s
            ORDER BY COALESCE(rating_count, 0) DESC
            """,
            (game_id,),
        ).fetchall()
    except psycopg.Error:
        return "无平台数据"

    if not rows:
        return "无平台数据"

    lines: list[str] = []
    for platform, rating, rating_count, download_est in rows:
        parts: list[str] = []
        if rating is not None:
            rc = f"{int(rating_count):,}" if rating_count else "0"
            parts.append(f"评分 {float(rating):.1f} ({rc} 评论)")
        if download_est is not None:
            parts.append(f"下载量 {int(download_est):,}+")
        detail = ", ".join(parts) if parts else "无指标"
        lines.append(f"- {platform}: {detail}")
    return "\n".join(lines)


# ============================================================
# Content-hash helper
# ============================================================
def _advice_hash(context: dict) -> str:
    """Stable hash of inputs that drive the project advice LLM call."""
    digest = hashlib.sha256()
    digest.update(PROMPT_VERSION.encode("utf-8"))
    digest.update(b"|")

    payload = context.get("existing_payload") or {}
    # Hash the GameReport's own content_hash if present — that already
    # captures the upstream report inputs cheaply.
    upstream_hash = payload.get("content_hash") or ""

    # Similar games — only the IDs (order-stable)
    similar_ids = sorted(
        int(g.get("id") or 0) for g in (context.get("similar_games") or [])
    )

    parts = [
        context.get("genre") or "",
        str(context.get("potential_score") or 0),
        upstream_hash,
        json.dumps(similar_ids),
        context.get("platform_summary") or "",
    ]
    digest.update("\n".join(parts).encode("utf-8"))
    return digest.hexdigest()[:16]


# ============================================================
# Eligibility query
# ============================================================
def _find_eligible_games(
    conn: psycopg.Connection, limit: int
) -> list[tuple[int, str]]:
    """Return (game_id, name) for games with GameReports lacking project_advice.

    Ordering: by latest potential score DESC, so highest-value decisions happen first.
    Filtered to potential_score >= MIN_POTENTIAL_SCORE so we don't spend Sonnet
    cycles on games unlikely to be greenlit anyway.
    """
    rows = conn.execute(
        """
        SELECT g.id, COALESCE(g.name_en, g.name_zh, 'Unknown') AS name
        FROM games g
        JOIN game_reports gr ON gr.game_id = g.id
        LEFT JOIN LATERAL (
            SELECT overall_score
            FROM potential_scores
            WHERE game_id = g.id
            ORDER BY scored_at DESC
            LIMIT 1
        ) ps ON TRUE
        WHERE (
            gr.payload->'project_advice' IS NULL
            OR gr.payload->'project_advice'->>'prompt_version' IS DISTINCT FROM %s
        )
          AND COALESCE(ps.overall_score, 0) >= %s
        ORDER BY COALESCE(ps.overall_score, 0) DESC
        LIMIT %s
        """,
        (PROMPT_VERSION, MIN_POTENTIAL_SCORE, limit),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


# ============================================================
# Sync entry point
# ============================================================
def run_project_advice_generation(db_url: str, limit: int = 20) -> int:
    """Generate project advice for top-N eligible games. Entry for rq worker."""

    async def _run() -> int:
        gen = ProjectAdviceGenerator(db_url)
        try:
            count = await gen.generate_for_all(limit=limit)
            logger.info(
                f"Project advice generation: {count} advice entries written. "
                f"{gen.client.cost_tracker.summary()}"
            )
            return count
        finally:
            await gen.client.close()

    return asyncio.run(_run())


__all__ = [
    "ProjectAdviceGenerator",
    "run_project_advice_generation",
    "PROMPT_VERSION",
    "MIN_CONFIDENCE",
]
