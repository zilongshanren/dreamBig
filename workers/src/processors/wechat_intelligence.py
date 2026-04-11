"""WeChat mini-game IAA intelligence processor.

Daily briefing generator — pulls six orthogonal signals out of the
ranking_snapshots / potential_scores / social_signals / reviews tables,
formats them as a single LLM prompt, and asks Opus to produce a
structured think-tank briefing (WechatIntelligenceReport) that gets
persisted into `generated_reports` keyed by the snapshot date.

The SQL side is the real value — LLM just synthesizes the
pre-extracted cross-chart signals into decision-grade prose. If the
LLM fails, the raw structured signals are still the input to the next
day's run and nothing breaks.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from src.llm import PoeClient, get_model_for_task
from src.llm.prompts.wechat_intelligence import (
    WECHAT_INTEL_PROMPT,
    WechatIntelligenceReport,
    build_wechat_intel_messages,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = WECHAT_INTEL_PROMPT.version  # "v1"
MIN_CONFIDENCE_TO_STORE = 0.25


# ============================================================
# Structured signal extraction (SQL side)
# ============================================================


def _get_global_stats(conn: psycopg.Connection, today: date) -> dict[str, int]:
    """Global counters — total games, chart rows, high-potential count, etc."""
    total_games = conn.execute(
        """
        SELECT COUNT(DISTINCT g.id)
        FROM games g
        JOIN platform_listings pl ON pl.game_id = g.id
        WHERE pl.platform = 'wechat_mini'
        """
    ).fetchone()[0]

    total_chart_rows = conn.execute(
        """
        SELECT COUNT(*)
        FROM ranking_snapshots rs
        JOIN platform_listings pl ON rs.platform_listing_id = pl.id
        WHERE pl.platform = 'wechat_mini' AND rs.snapshot_date = %s
        """,
        (today,),
    ).fetchone()[0]

    high_potential_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM potential_scores ps
        JOIN games g ON ps.game_id = g.id
        JOIN platform_listings pl ON pl.game_id = g.id
        WHERE pl.platform = 'wechat_mini'
          AND ps.scored_at = %s
          AND ps.overall_score >= 60
        """,
        (today,),
    ).fetchone()[0]

    games_with_score = conn.execute(
        """
        SELECT COUNT(DISTINCT g.id)
        FROM potential_scores ps
        JOIN games g ON ps.game_id = g.id
        JOIN platform_listings pl ON pl.game_id = g.id
        WHERE pl.platform = 'wechat_mini' AND ps.scored_at = %s
        """,
        (today,),
    ).fetchone()[0]

    games_with_reviews = conn.execute(
        """
        SELECT COUNT(DISTINCT pl.game_id)
        FROM reviews r
        JOIN platform_listings pl ON r.platform_listing_id = pl.id
        WHERE pl.platform = 'wechat_mini'
        """
    ).fetchone()[0]

    return {
        "total_games": int(total_games or 0),
        "total_chart_rows": int(total_chart_rows or 0),
        "high_potential_count": int(high_potential_count or 0),
        "games_with_score": int(games_with_score or 0),
        "games_with_reviews": int(games_with_reviews or 0),
    }


def _get_cross_chart_signals(
    conn: psycopg.Connection, today: date, limit: int = 20
) -> list[dict[str, Any]]:
    """Games appearing on the most chart_types today (top-100 cutoff per chart)."""
    rows = conn.execute(
        """
        SELECT g.id,
               COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
               g.developer,
               g.genre,
               COUNT(DISTINCT rs.chart_type) AS chart_count,
               ARRAY_AGG(DISTINCT rs.chart_type ORDER BY rs.chart_type) AS charts,
               MIN(rs.rank_position) AS best_rank
        FROM ranking_snapshots rs
        JOIN platform_listings pl ON rs.platform_listing_id = pl.id
        JOIN games g ON pl.game_id = g.id
        WHERE pl.platform = 'wechat_mini'
          AND rs.snapshot_date = %s
          AND rs.rank_position <= 100
        GROUP BY g.id, g.name_zh, g.name_en, g.developer, g.genre
        HAVING COUNT(DISTINCT rs.chart_type) >= 2
        ORDER BY COUNT(DISTINCT rs.chart_type) DESC, MIN(rs.rank_position) ASC
        LIMIT %s
        """,
        (today, limit),
    ).fetchall()
    return [
        {
            "game_id": int(r[0]),
            "name": r[1],
            "developer": r[2],
            "genre": r[3],
            "chart_count": int(r[4]),
            "charts": list(r[5] or []),
            "best_rank": int(r[6]),
        }
        for r in rows
    ]


def _get_momentum(
    conn: psycopg.Connection, today: date, window_days: int = 7, limit: int = 15
) -> list[dict[str, Any]]:
    """Biggest 7-day rank climbers (per chart_type). Returns one row per
    (game, chart) even if the same game appears on multiple charts."""
    cutoff = today - timedelta(days=window_days)
    rows = conn.execute(
        """
        WITH today_rank AS (
            SELECT rs.platform_listing_id, rs.chart_type,
                   rs.rank_position AS rank_today
            FROM ranking_snapshots rs
            JOIN platform_listings pl ON rs.platform_listing_id = pl.id
            WHERE pl.platform = 'wechat_mini' AND rs.snapshot_date = %s
        ),
        past_rank AS (
            SELECT rs.platform_listing_id, rs.chart_type,
                   MIN(rs.rank_position) AS rank_then
            FROM ranking_snapshots rs
            JOIN platform_listings pl ON rs.platform_listing_id = pl.id
            WHERE pl.platform = 'wechat_mini'
              AND rs.snapshot_date >= %s AND rs.snapshot_date < %s
            GROUP BY rs.platform_listing_id, rs.chart_type
        )
        SELECT g.id,
               COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
               g.developer,
               g.genre,
               t.chart_type,
               t.rank_today,
               p.rank_then,
               (p.rank_then - t.rank_today) AS rank_jump
        FROM today_rank t
        JOIN past_rank p
          ON p.platform_listing_id = t.platform_listing_id
         AND p.chart_type = t.chart_type
        JOIN platform_listings pl ON t.platform_listing_id = pl.id
        JOIN games g ON pl.game_id = g.id
        WHERE (p.rank_then - t.rank_today) >= 10
        ORDER BY (p.rank_then - t.rank_today) DESC
        LIMIT %s
        """,
        (today, cutoff, today, limit),
    ).fetchall()
    return [
        {
            "game_id": int(r[0]),
            "name": r[1],
            "developer": r[2],
            "genre": r[3],
            "chart_type": r[4],
            "rank_today": int(r[5]),
            "rank_then": int(r[6]),
            "rank_jump": int(r[7]),
        }
        for r in rows
    ]


def _get_developer_concentration(
    conn: psycopg.Connection, today: date, limit: int = 10
) -> list[dict[str, Any]]:
    """Developers with the most presence in today's top-50 across all charts."""
    rows = conn.execute(
        """
        SELECT g.developer,
               COUNT(DISTINCT g.id) AS distinct_games,
               COUNT(DISTINCT rs.chart_type) AS charts_present,
               COUNT(*) FILTER (WHERE rs.rank_position <= 10) AS top10_slots,
               COUNT(*) AS total_top50_slots
        FROM games g
        JOIN platform_listings pl ON g.id = pl.game_id
        JOIN ranking_snapshots rs ON rs.platform_listing_id = pl.id
        WHERE pl.platform = 'wechat_mini'
          AND rs.snapshot_date = %s
          AND rs.rank_position <= 50
          AND g.developer IS NOT NULL
          AND LENGTH(g.developer) > 0
        GROUP BY g.developer
        ORDER BY COUNT(DISTINCT g.id) DESC,
                 COUNT(*) FILTER (WHERE rs.rank_position <= 10) DESC
        LIMIT %s
        """,
        (today, limit),
    ).fetchall()
    return [
        {
            "developer": r[0],
            "distinct_games": int(r[1]),
            "charts_present": int(r[2]),
            "top10_slots": int(r[3]),
            "total_top50_slots": int(r[4]),
        }
        for r in rows
    ]


def _get_genre_distribution(
    conn: psycopg.Connection, today: date
) -> list[dict[str, Any]]:
    """Genre breakdown of games in today's top-50 across all charts."""
    rows = conn.execute(
        """
        SELECT g.genre,
               COUNT(DISTINCT g.id) AS games_in_top50,
               COUNT(*) AS total_top50_slots,
               (ARRAY_AGG(DISTINCT COALESCE(g.name_zh, g.name_en)
                          ORDER BY COALESCE(g.name_zh, g.name_en)))[1:5] AS example_games
        FROM games g
        JOIN platform_listings pl ON g.id = pl.game_id
        JOIN ranking_snapshots rs ON rs.platform_listing_id = pl.id
        WHERE pl.platform = 'wechat_mini'
          AND rs.snapshot_date = %s
          AND rs.rank_position <= 50
          AND g.genre IS NOT NULL
        GROUP BY g.genre
        ORDER BY COUNT(DISTINCT g.id) DESC
        """,
        (today,),
    ).fetchall()
    return [
        {
            "genre": r[0],
            "games_in_top50": int(r[1]),
            "total_top50_slots": int(r[2]),
            "example_games": list(r[3] or [])[:5],
        }
        for r in rows
    ]


def _get_social_resonance(
    conn: psycopg.Connection, today: date, limit: int = 10
) -> list[dict[str, Any]]:
    """Games in today's top-50 with the highest 7-day Bilibili view totals."""
    cutoff = today - timedelta(days=7)
    rows = conn.execute(
        """
        SELECT g.id,
               COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
               g.developer,
               g.genre,
               MIN(rs.rank_position) AS best_rank,
               COALESCE(SUM(ss.view_count), 0)::BIGINT AS total_views_7d,
               COALESCE(SUM(ss.video_count), 0) AS total_videos
        FROM games g
        JOIN platform_listings pl ON g.id = pl.game_id
        JOIN ranking_snapshots rs ON rs.platform_listing_id = pl.id
        LEFT JOIN social_signals ss ON ss.game_id = g.id
            AND ss.signal_date >= %s
        WHERE pl.platform = 'wechat_mini'
          AND rs.snapshot_date = %s
          AND rs.rank_position <= 50
        GROUP BY g.id, g.name_zh, g.name_en, g.developer, g.genre
        HAVING COALESCE(SUM(ss.view_count), 0) > 0
        ORDER BY total_views_7d DESC
        LIMIT %s
        """,
        (cutoff, today, limit),
    ).fetchall()
    return [
        {
            "game_id": int(r[0]),
            "name": r[1],
            "developer": r[2],
            "genre": r[3],
            "best_rank": int(r[4]),
            "total_views_7d": int(r[5]),
            "total_videos": int(r[6]),
        }
        for r in rows
    ]


def _get_iaa_top(
    conn: psycopg.Connection, today: date, limit: int = 10
) -> list[dict[str, Any]]:
    """Top WeChat games by today's composite potential_score + IAA grade."""
    rows = conn.execute(
        """
        SELECT g.id,
               COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
               g.developer,
               g.genre,
               g.iaa_grade,
               g.iaa_suitability,
               ps.overall_score
        FROM potential_scores ps
        JOIN games g ON ps.game_id = g.id
        JOIN platform_listings pl ON pl.game_id = g.id
        WHERE pl.platform = 'wechat_mini'
          AND ps.scored_at = %s
        ORDER BY ps.overall_score DESC
        LIMIT %s
        """,
        (today, limit),
    ).fetchall()
    return [
        {
            "game_id": int(r[0]),
            "name": r[1],
            "developer": r[2],
            "genre": r[3],
            "iaa_grade": r[4],
            "iaa_suitability": int(r[5] or 0),
            "overall_score": int(r[6] or 0),
        }
        for r in rows
    ]


# ============================================================
# Block formatters (SQL rows → prompt-friendly text)
# ============================================================


def _fmt_cross_chart(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "（今日无跨榜数据）"
    lines = []
    for r in rows:
        lines.append(
            f"- game:{r['game_id']} 《{r['name']}》 "
            f"developer={r['developer'] or '-'} "
            f"genre={r['genre'] or '-'} "
            f"charts={r['chart_count']} {r['charts']} "
            f"best_rank={r['best_rank']}"
        )
    return "\n".join(lines)


def _fmt_momentum(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "（今日无 7 天动量数据 — 可能是历史未积累）"
    lines = []
    for r in rows:
        lines.append(
            f"- game:{r['game_id']} 《{r['name']}》 "
            f"chart:{r['chart_type']} "
            f"#{r['rank_then']} → #{r['rank_today']} (+{r['rank_jump']}) "
            f"developer={r['developer'] or '-'} genre={r['genre'] or '-'}"
        )
    return "\n".join(lines)


def _fmt_developer(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "（无开发商数据）"
    lines = []
    for r in rows:
        lines.append(
            f"- {r['developer']}: "
            f"{r['distinct_games']} 款游戏, "
            f"{r['total_top50_slots']} 个 top50 席位, "
            f"{r['top10_slots']} 个 top10 席位, "
            f"覆盖 {r['charts_present']} 个榜单"
        )
    return "\n".join(lines)


def _fmt_genre(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "（无 genre 数据 — 可能是 translate / scoring 未跑）"
    total = sum(r["games_in_top50"] for r in rows)
    lines = [f"（top50 里共 {total} 款有 genre 标签的游戏）"]
    for r in rows:
        pct = (r["games_in_top50"] / total * 100) if total else 0
        examples = "、".join(r["example_games"][:3])
        lines.append(
            f"- {r['genre']}: {r['games_in_top50']} 款 ({pct:.1f}%) "
            f"例:{examples}"
        )
    return "\n".join(lines)


def _fmt_resonance(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "（今日无社媒-榜单共振数据 — 可能是 social_signals 未跑）"
    lines = []
    for r in rows:
        lines.append(
            f"- game:{r['game_id']} 《{r['name']}》 "
            f"best_rank=#{r['best_rank']} "
            f"7d_views={r['total_views_7d']:,} videos={r['total_videos']} "
            f"genre={r['genre'] or '-'}"
        )
    return "\n".join(lines)


def _fmt_iaa_top(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "（今日无 IAA / scoring 数据 — 可能是 scoring 未跑）"
    lines = []
    for r in rows:
        lines.append(
            f"- game:{r['game_id']} 《{r['name']}》 "
            f"overall_score={r['overall_score']} "
            f"iaa_grade={r['iaa_grade'] or '-'} "
            f"iaa_suitability={r['iaa_suitability']} "
            f"genre={r['genre'] or '-'} "
            f"developer={r['developer'] or '-'}"
        )
    return "\n".join(lines)


# ============================================================
# Generator
# ============================================================


class WechatIntelligenceGenerator:
    """Produces and persists one daily WeChat IAA think-tank report."""

    def __init__(self, db_url: str, client: PoeClient | None = None):
        self.db_url = db_url
        self.client = client or PoeClient()

    def _gather(
        self, conn: psycopg.Connection, today: date
    ) -> dict[str, Any]:
        stats = _get_global_stats(conn, today)
        return {
            **stats,
            "snapshot_date": today.isoformat(),
            "cross_chart": _get_cross_chart_signals(conn, today),
            "momentum": _get_momentum(conn, today),
            "developer": _get_developer_concentration(conn, today),
            "genre": _get_genre_distribution(conn, today),
            "resonance": _get_social_resonance(conn, today),
            "iaa_top": _get_iaa_top(conn, today),
        }

    async def generate(
        self, target_date: date | None = None
    ) -> dict | None:
        today = target_date or date.today()
        scope = today.isoformat()

        with psycopg.connect(self.db_url) as conn:
            # Idempotent: overwrite today's report if re-run intentionally.
            data = self._gather(conn, today)

            if (
                data["total_chart_rows"] == 0
                and data["total_games"] == 0
            ):
                logger.warning(
                    f"[wechat_intel] no wechat data for {scope}, skipping"
                )
                return None

            messages = build_wechat_intel_messages(
                snapshot_date=scope,
                total_games=data["total_games"],
                total_chart_rows=data["total_chart_rows"],
                high_potential_count=data["high_potential_count"],
                games_with_score=data["games_with_score"],
                games_with_reviews=data["games_with_reviews"],
                cross_chart_block=_fmt_cross_chart(data["cross_chart"]),
                momentum_block=_fmt_momentum(data["momentum"]),
                developer_block=_fmt_developer(data["developer"]),
                genre_block=_fmt_genre(data["genre"]),
                resonance_block=_fmt_resonance(data["resonance"]),
                iaa_top_block=_fmt_iaa_top(data["iaa_top"]),
            )

            model = get_model_for_task("wechat_intelligence")
            try:
                report = await self.client.chat_json(
                    messages=messages,
                    model=model,
                    schema=WechatIntelligenceReport,
                )
            except Exception as exc:
                logger.error(
                    f"[wechat_intel] LLM call failed for {scope}: {exc}"
                )
                return None

            if report.overall_confidence < MIN_CONFIDENCE_TO_STORE:
                logger.info(
                    f"[wechat_intel] confidence {report.overall_confidence:.2f} "
                    f"< {MIN_CONFIDENCE_TO_STORE} — persisting anyway at low confidence "
                    f"(dashboard should render a warning badge)"
                )

            tokens_used: int | None = None
            cost_usd: float | None = None
            model_usage = self.client.cost_tracker.by_model.get(model)
            if model_usage is not None:
                tokens_used = (
                    model_usage.input_tokens + model_usage.output_tokens
                )
                cost_usd = round(float(model_usage.cost_usd), 4)

            payload_json = report.model_dump_json()
            evidence_count = (
                len(report.top_signal_games)
                + len(report.market_opportunities)
                + len(report.red_flags)
                + sum(len(p.inspirations) for p in report.project_recommendations)
            )

            conn.execute(
                """
                INSERT INTO generated_reports
                    (report_type, scope, title, summary, payload,
                     evidence_count, model_used, tokens_used, cost_usd,
                     generated_at)
                VALUES ('wechat_intelligence', %s, %s, %s, %s::jsonb,
                        %s, %s, %s, %s, NOW())
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
                    scope,
                    report.headline,
                    report.market_snapshot,
                    payload_json,
                    evidence_count,
                    model,
                    tokens_used,
                    cost_usd,
                ),
            )
            conn.commit()

        logger.info(
            f"[wechat_intel] {scope} persisted: "
            f"pulse={report.market_pulse} "
            f"confidence={report.overall_confidence:.2f} "
            f"signal_games={len(report.top_signal_games)} "
            f"opportunities={len(report.market_opportunities)} "
            f"red_flags={len(report.red_flags)} "
            f"recs={len(report.project_recommendations)}"
        )
        return {
            "date": scope,
            "headline": report.headline,
            "pulse": report.market_pulse,
            "confidence": report.overall_confidence,
            "recommendations": len(report.project_recommendations),
        }


def run_wechat_intelligence(
    db_url: str, target_date: date | None = None
) -> dict | None:
    """Sync entry point for workers / schedulers."""

    async def _run() -> dict | None:
        gen = WechatIntelligenceGenerator(db_url)
        try:
            return await gen.generate(target_date=target_date)
        finally:
            try:
                await gen.client.close()
            except Exception:
                pass

    return asyncio.run(_run())


__all__ = [
    "WechatIntelligenceGenerator",
    "run_wechat_intelligence",
    "PROMPT_VERSION",
]
