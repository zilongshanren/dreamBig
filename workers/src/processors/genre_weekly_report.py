"""Weekly genre trend report generator.

Runs once a week, aggregates genre momentum + top games from the `genres`
rollup table, produces a narrative Chinese report via Sonnet, and stores
the structured payload in `generated_reports` keyed by ISO week code.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from decimal import Decimal

import psycopg

from src.llm import PoeClient, get_model_for_task
from src.llm.prompts.genre_report import (
    GENRE_REPORT_PROMPT,
    GenreWeeklyReport,
    build_genre_report_messages,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = GENRE_REPORT_PROMPT.version  # "v1"

MIN_CONFIDENCE_TO_STORE = 0.3
TOP_GAME_NAMES_PER_GENRE = 3


class GenreWeeklyReportGenerator:
    """Produces and persists one weekly genre trend report per ISO week."""

    def __init__(self, db_url: str, client: PoeClient | None = None):
        self.db_url = db_url
        self.client = client or PoeClient()

    # --------------------------------------------------------------
    # Week helpers
    # --------------------------------------------------------------
    @staticmethod
    def iso_week_code(d: date) -> str:
        """Return ISO week code like '2026-W15' for the given date."""
        year, week, _ = d.isocalendar()
        return f"{year}-W{week:02d}"

    # --------------------------------------------------------------
    # Data gathering
    # --------------------------------------------------------------
    def _gather_data(self, conn: psycopg.Connection) -> list[dict]:
        """Fetch genre rollup stats + top-3 game names per genre.

        Reads the `genres` table (refreshed daily by GenreAggregator),
        then looks up top_game_ids to resolve game names. Only genres
        with any activity (hot games or non-zero momentum) are kept.
        """
        genre_rows = conn.execute(
            """
            SELECT key, label_zh, label_en, hot_games_count,
                   momentum, top_game_ids, iaa_baseline
            FROM genres
            WHERE hot_games_count > 0 OR momentum != 0
            ORDER BY momentum DESC
            """
        ).fetchall()

        if not genre_rows:
            return []

        # Collect all referenced game IDs in one pass for a single lookup query
        all_ids: set[int] = set()
        for _, _, _, _, _, top_ids, _ in genre_rows:
            for gid in (top_ids or [])[:TOP_GAME_NAMES_PER_GENRE]:
                all_ids.add(int(gid))

        name_by_id: dict[int, str] = {}
        if all_ids:
            name_rows = conn.execute(
                """
                SELECT id, COALESCE(name_zh, name_en, 'Unknown')
                FROM games
                WHERE id = ANY(%s)
                """,
                (list(all_ids),),
            ).fetchall()
            name_by_id = {gid: name for gid, name in name_rows}

        out: list[dict] = []
        for key, label_zh, label_en, hot_count, momentum, top_ids, iaa_baseline in genre_rows:
            top_ids_capped = list(top_ids or [])[:TOP_GAME_NAMES_PER_GENRE]
            top_names = [
                name_by_id[int(gid)]
                for gid in top_ids_capped
                if int(gid) in name_by_id
            ]
            momentum_f = (
                float(momentum)
                if isinstance(momentum, Decimal)
                else float(momentum or 0)
            )
            out.append(
                {
                    "key": key,
                    "label_zh": label_zh,
                    "label_en": label_en,
                    "hot_games_count": int(hot_count or 0),
                    "momentum": round(momentum_f, 3),
                    "iaa_baseline": int(iaa_baseline or 0),
                    "top_game_names": top_names,
                }
            )

        return out

    # --------------------------------------------------------------
    # Generate + persist
    # --------------------------------------------------------------
    async def generate(self, week: str | None = None) -> dict | None:
        """Generate and persist one weekly report for the given ISO week.

        Defaults to the current ISO week. Returns a small summary dict on
        success, or None if skipped (already exists, no data, LLM failure,
        or confidence below threshold).
        """
        week_code = week or self.iso_week_code(date.today())

        with psycopg.connect(self.db_url) as conn:
            # Skip if we've already generated this week's report.
            existing = conn.execute(
                """
                SELECT id FROM generated_reports
                WHERE report_type = 'weekly_genre' AND scope = %s
                """,
                (week_code,),
            ).fetchone()
            if existing:
                logger.info(
                    f"Weekly genre report for {week_code} already exists, skipping"
                )
                return None

            genres_data = self._gather_data(conn)
            if not genres_data:
                logger.warning(
                    f"No active genre data for {week_code}, skipping report"
                )
                return None

            model = get_model_for_task("genre_trend_summary")
            messages = build_genre_report_messages(week_code, genres_data)

            try:
                report = await self.client.chat_json(
                    messages=messages,
                    model=model,
                    schema=GenreWeeklyReport,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"LLM call failed for genre weekly report {week_code}: {exc}"
                )
                return None

            if report.overall_confidence < MIN_CONFIDENCE_TO_STORE:
                logger.info(
                    f"Genre report {week_code} confidence {report.overall_confidence:.2f} "
                    f"below {MIN_CONFIDENCE_TO_STORE}, discarding"
                )
                return None

            # Tokens / cost attribution from the cost tracker.
            model_usage = self.client.cost_tracker.by_model.get(model)
            tokens_used: int | None = None
            cost_usd: float | None = None
            if model_usage is not None:
                tokens_used = (
                    model_usage.input_tokens + model_usage.output_tokens
                )
                cost_usd = round(float(model_usage.cost_usd), 4)

            payload_json = report.model_dump_json()
            evidence_count = len(report.top_rising) + len(report.top_declining)

            conn.execute(
                """
                INSERT INTO generated_reports
                    (report_type, scope, title, summary, payload,
                     evidence_count, model_used, tokens_used, cost_usd,
                     generated_at)
                VALUES ('weekly_genre', %s, %s, %s, %s::jsonb, %s, %s, %s, %s, NOW())
                ON CONFLICT (report_type, scope) DO UPDATE SET
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    payload = EXCLUDED.payload,
                    evidence_count = EXCLUDED.evidence_count,
                    model_used = EXCLUDED.model_used,
                    tokens_used = EXCLUDED.tokens_used,
                    cost_usd = EXCLUDED.cost_usd,
                    generated_at = NOW()
                """,
                (
                    week_code,
                    report.headline,
                    report.summary,
                    payload_json,
                    evidence_count,
                    model,
                    tokens_used,
                    cost_usd,
                ),
            )
            conn.commit()

        logger.info(
            f"Weekly genre report {week_code} written "
            f"(confidence={report.overall_confidence:.2f}, "
            f"rising={len(report.top_rising)}, "
            f"declining={len(report.top_declining)})"
        )
        return {
            "week": week_code,
            "headline": report.headline,
            "confidence": report.overall_confidence,
        }


def run_genre_weekly_report(db_url: str, week: str | None = None) -> dict | None:
    """Sync entry point for workers / schedulers."""

    async def _run() -> dict | None:
        generator = GenreWeeklyReportGenerator(db_url)
        try:
            return await generator.generate(week=week)
        finally:
            await generator.client.close()

    return asyncio.run(_run())


__all__ = [
    "GenreWeeklyReportGenerator",
    "run_genre_weekly_report",
    "PROMPT_VERSION",
    "MIN_CONFIDENCE_TO_STORE",
]
