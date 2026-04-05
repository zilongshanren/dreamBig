"""Alert evaluation and notification system.

Evaluates 5 alert detector types against fresh signals, assigns severity levels
(P1/P2/P3), de-duplicates per game, respects cooldowns, and emits rich Feishu
interactive cards with suggested actions.

Detectors:
  - RANKING_JUMP:    game moved up >= 15 positions on a chart today
  - REVIEW_BURST:    review volume today >= 3x the 7-day mean
  - SOCIAL_BURST:    social view volume today >= 3x the 7-day mean
  - STEAM_MOMENTUM:  Steam rank rising and ranking_velocity score >= 60
  - TAPTAP_HEAT:     TapTap hot chart rising or rating_count surging

Also preserves backward-compatible user-defined rule evaluation (the old path
reading from the `alerts` table with `conditions` JSON).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

import httpx
import psycopg

logger = logging.getLogger(__name__)


# ============================================================
# Enums / types
# ============================================================
class AlertType(str, Enum):
    RANKING_JUMP = "ranking_jump"
    REVIEW_BURST = "review_burst"
    SOCIAL_BURST = "social_burst"
    STEAM_MOMENTUM = "steam_momentum"
    TAPTAP_HEAT = "taptap_heat"


class Severity(str, Enum):
    P1 = "P1"  # high — multi-signal confluence, large magnitude
    P2 = "P2"  # medium — single strong signal
    P3 = "P3"  # low — mild trend


# Ordering for deduplication (higher = more important)
_SEVERITY_RANK = {Severity.P1: 3, Severity.P2: 2, Severity.P3: 1}

# Chinese label + emoji per alert type
_ALERT_TYPE_META: dict[AlertType, tuple[str, str]] = {
    AlertType.RANKING_JUMP: ("🚀", "榜单暴涨预警"),
    AlertType.REVIEW_BURST: ("💬", "评论激增预警"),
    AlertType.SOCIAL_BURST: ("🔥", "社交爆发预警"),
    AlertType.STEAM_MOMENTUM: ("🎮", "Steam 动量预警"),
    AlertType.TAPTAP_HEAT: ("📱", "TapTap 热度预警"),
}

# Feishu card header template colors per severity
_SEVERITY_TEMPLATE = {
    Severity.P1: "red",
    Severity.P2: "yellow",
    Severity.P3: "blue",
}


@dataclass
class AlertCandidate:
    """A detector result, ready for dedup / cooldown / dispatch."""

    game_id: int
    game_name: str
    alert_type: AlertType
    severity: Severity
    reason: str
    score: int | None = None
    metadata: dict = field(default_factory=dict)
    suggested_actions: list[str] = field(default_factory=list)


# ============================================================
# AlertEngine
# ============================================================
class AlertEngine:
    """Evaluates detectors + user rules and sends notifications."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.feishu_webhook = os.environ.get("FEISHU_WEBHOOK_URL")
        self.app_url = os.environ.get("NEXT_PUBLIC_APP_URL", "http://localhost:3000")

    # --------------------------------------------------------
    # Public entry point
    # --------------------------------------------------------
    def evaluate_alerts(self) -> int:
        """Run detectors + legacy rule evaluation. Returns triggered count."""
        triggered = 0

        with psycopg.connect(self.db_url) as conn:
            # Ensure one "system" Alert row exists per AlertType so alert_events
            # have a foreign key target. Uses a synthetic name as the unique key.
            system_alert_ids = self._ensure_system_alerts(conn)

            # --- Detector path (new) ---
            candidates: list[AlertCandidate] = []
            for detector in (
                self.detect_ranking_jump,
                self.detect_social_burst,
                self.detect_steam_momentum,
                self.detect_taptap_heat,
                self.detect_review_burst,
            ):
                try:
                    candidates.extend(detector(conn))
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Detector {detector.__name__} failed: {e}")
                    conn.rollback()

            # Deduplicate: keep highest severity per (game_id, alert_type) and
            # also collapse multiple alert types per game to the single most
            # severe one. Per-type keeps nuance; the global collapse prevents
            # spamming a game with three cards at once.
            deduped = self._dedupe(candidates)

            for cand in deduped:
                alert_id = system_alert_ids.get(cand.alert_type)
                if alert_id is None:
                    continue

                if self._is_in_cooldown(conn, alert_id, cand.game_id, cooldown_hours=12):
                    continue

                conn.execute(
                    """
                    INSERT INTO alert_events
                      (alert_id, game_id, score, alert_type, severity, reason)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        alert_id,
                        cand.game_id,
                        cand.score,
                        cand.alert_type.value,
                        cand.severity.value,
                        cand.reason,
                    ),
                )
                self._send_notification(self.feishu_webhook, cand)
                triggered += 1

            # --- Legacy user-rule path (backward compat) ---
            triggered += self._evaluate_user_rules(conn, system_alert_ids)

            conn.commit()

        logger.info(f"Alerting: triggered {triggered} notifications")
        return triggered

    # --------------------------------------------------------
    # Detectors
    # --------------------------------------------------------
    def detect_ranking_jump(
        self, conn: psycopg.Connection
    ) -> list[AlertCandidate]:
        """Games that jumped up >= 15 positions on any chart today."""
        rows = conn.execute(
            """
            SELECT g.id,
                   COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
                   pl.platform,
                   rs.chart_type,
                   rs.region,
                   rs.rank_position,
                   rs.previous_rank,
                   rs.rank_change
            FROM ranking_snapshots rs
            JOIN platform_listings pl ON pl.id = rs.platform_listing_id
            JOIN games g              ON g.id  = pl.game_id
            WHERE rs.snapshot_date = CURRENT_DATE
              AND rs.rank_change IS NOT NULL
              AND rs.rank_change >= 15
            ORDER BY rs.rank_change DESC
            LIMIT 200
            """
        ).fetchall()

        # Count how many platforms each game is rising on today (for P1 uplift)
        multi_platform_counts: dict[int, int] = {}
        for row in rows:
            multi_platform_counts[row[0]] = multi_platform_counts.get(row[0], 0) + 1

        out: list[AlertCandidate] = []
        for (
            game_id, name, platform, chart_type, region,
            rank_position, previous_rank, rank_change,
        ) in rows:
            severity = self._ranking_severity(
                rank_change, multi_platform_counts.get(game_id, 1)
            )
            prev = previous_rank if previous_rank is not None else "N/A"
            reason = (
                f"{platform}/{chart_type}/{region}: "
                f"#{prev} → #{rank_position} (上升 {rank_change} 名)"
            )
            out.append(
                AlertCandidate(
                    game_id=game_id,
                    game_name=name,
                    alert_type=AlertType.RANKING_JUMP,
                    severity=severity,
                    reason=reason,
                    score=self._lookup_score(conn, game_id),
                    metadata={
                        "platform": platform,
                        "chart_type": chart_type,
                        "region": region,
                        "rank_position": rank_position,
                        "previous_rank": previous_rank,
                        "rank_change": rank_change,
                        "rising_platforms": multi_platform_counts.get(game_id, 1),
                    },
                    suggested_actions=[
                        "generate_report",
                        "add_to_watchlist",
                        "view_dashboard",
                    ],
                )
            )
        return out

    def detect_social_burst(
        self, conn: psycopg.Connection
    ) -> list[AlertCandidate]:
        """Today's social views >= 3x 7-day mean AND >= 10k views."""
        rows = conn.execute(
            """
            WITH today AS (
                SELECT game_id, platform, SUM(view_count)::BIGINT AS v
                FROM social_signals
                WHERE signal_date = CURRENT_DATE
                GROUP BY game_id, platform
            ),
            baseline AS (
                SELECT game_id, platform, AVG(view_count)::BIGINT AS v
                FROM social_signals
                WHERE signal_date BETWEEN CURRENT_DATE - INTERVAL '7 days'
                                      AND CURRENT_DATE - INTERVAL '1 day'
                GROUP BY game_id, platform
            )
            SELECT t.game_id,
                   COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
                   t.platform,
                   t.v AS today_v,
                   COALESCE(b.v, 0) AS base_v
            FROM today t
            JOIN games g ON g.id = t.game_id
            LEFT JOIN baseline b USING (game_id, platform)
            WHERE t.v >= 10000
              AND (COALESCE(b.v, 0) = 0 OR t.v >= b.v * 3)
            ORDER BY t.v DESC
            LIMIT 100
            """
        ).fetchall()

        out: list[AlertCandidate] = []
        for game_id, name, platform, today_v, base_v in rows:
            ratio = (today_v / base_v) if base_v else float("inf")
            severity = self._social_severity(ratio, today_v)
            ratio_txt = "∞" if base_v == 0 else f"{ratio:.1f}×"
            reason = (
                f"{platform}: 今日播放 {self._fmt_num(today_v)} "
                f"(7日均值 {self._fmt_num(base_v)}, {ratio_txt})"
            )
            out.append(
                AlertCandidate(
                    game_id=game_id,
                    game_name=name,
                    alert_type=AlertType.SOCIAL_BURST,
                    severity=severity,
                    reason=reason,
                    score=self._lookup_score(conn, game_id),
                    metadata={
                        "platform": platform,
                        "today_views": int(today_v),
                        "baseline_views": int(base_v or 0),
                        "ratio": None if base_v == 0 else round(ratio, 2),
                    },
                    suggested_actions=[
                        "generate_report",
                        "add_to_watchlist",
                    ],
                )
            )
        return out

    def detect_steam_momentum(
        self, conn: psycopg.Connection
    ) -> list[AlertCandidate]:
        """Steam listing + today's rank rising + ranking_velocity score >= 60."""
        rows = conn.execute(
            """
            SELECT g.id,
                   COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
                   ps.ranking_velocity,
                   ps.overall_score,
                   COALESCE(MAX(rs.rank_change), 0) AS rank_delta,
                   COALESCE(MIN(rs.rank_position), 0) AS best_rank
            FROM games g
            JOIN potential_scores ps
                 ON ps.game_id = g.id
                AND ps.scored_at = CURRENT_DATE
            JOIN platform_listings pl
                 ON pl.game_id = g.id
                AND pl.platform = 'steam'
            LEFT JOIN ranking_snapshots rs
                 ON rs.platform_listing_id = pl.id
                AND rs.snapshot_date = CURRENT_DATE
                AND rs.rank_change > 0
            WHERE ps.ranking_velocity >= 60
            GROUP BY g.id, name, ps.ranking_velocity, ps.overall_score
            ORDER BY ps.ranking_velocity DESC
            LIMIT 100
            """
        ).fetchall()

        out: list[AlertCandidate] = []
        for game_id, name, velocity, overall_score, rank_delta, best_rank in rows:
            if velocity >= 80:
                severity = Severity.P1
            elif velocity >= 60:
                severity = Severity.P2
            else:
                continue

            rank_part = (
                f"今日上升 {rank_delta} 名 (#{best_rank})" if rank_delta > 0
                else "榜单稳定上行"
            )
            reason = f"Steam 动量分 {velocity}/100, {rank_part}"
            out.append(
                AlertCandidate(
                    game_id=game_id,
                    game_name=name,
                    alert_type=AlertType.STEAM_MOMENTUM,
                    severity=severity,
                    reason=reason,
                    score=int(overall_score) if overall_score is not None else None,
                    metadata={
                        "ranking_velocity": int(velocity),
                        "rank_delta": int(rank_delta),
                        "best_rank": int(best_rank),
                    },
                    suggested_actions=[
                        "generate_report",
                        "view_dashboard",
                        "add_to_watchlist",
                    ],
                )
            )
        return out

    def detect_taptap_heat(
        self, conn: psycopg.Connection
    ) -> list[AlertCandidate]:
        """TapTap hot-chart rise > 10 OR rating_count jump > 500 today."""
        # Hot-chart movers
        chart_rows = conn.execute(
            """
            SELECT g.id,
                   COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
                   rs.chart_type,
                   rs.rank_position,
                   rs.rank_change
            FROM ranking_snapshots rs
            JOIN platform_listings pl ON pl.id = rs.platform_listing_id
            JOIN games g              ON g.id  = pl.game_id
            WHERE pl.platform = 'taptap'
              AND rs.snapshot_date = CURRENT_DATE
              AND rs.rank_change IS NOT NULL
              AND rs.rank_change > 10
            ORDER BY rs.rank_change DESC
            LIMIT 100
            """
        ).fetchall()

        seen: set[int] = set()
        out: list[AlertCandidate] = []
        for game_id, name, chart_type, rank_position, rank_change in chart_rows:
            seen.add(game_id)
            severity = Severity.P2 if rank_change >= 25 else Severity.P3
            reason = (
                f"TapTap {chart_type}: #{rank_position} "
                f"(今日上升 {rank_change} 名)"
            )
            out.append(
                AlertCandidate(
                    game_id=game_id,
                    game_name=name,
                    alert_type=AlertType.TAPTAP_HEAT,
                    severity=severity,
                    reason=reason,
                    score=self._lookup_score(conn, game_id),
                    metadata={
                        "chart_type": chart_type,
                        "rank_position": int(rank_position),
                        "rank_change": int(rank_change),
                        "source": "chart",
                    },
                    suggested_actions=[
                        "generate_report",
                        "add_to_watchlist",
                    ],
                )
            )

        # Rating-count surges: compare today vs yesterday on the same listing
        rating_rows = conn.execute(
            """
            WITH pairs AS (
                SELECT pl.game_id,
                       pl.rating_count AS now_count,
                       pl.last_updated,
                       LAG(pl.rating_count) OVER (
                           PARTITION BY pl.id ORDER BY pl.last_updated
                       ) AS prev_count
                FROM platform_listings pl
                WHERE pl.platform = 'taptap'
                  AND pl.rating_count IS NOT NULL
            )
            SELECT p.game_id,
                   COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
                   (p.now_count - p.prev_count) AS delta,
                   p.now_count
            FROM pairs p
            JOIN games g ON g.id = p.game_id
            WHERE p.prev_count IS NOT NULL
              AND (p.now_count - p.prev_count) > 500
              AND p.last_updated = CURRENT_DATE
            ORDER BY delta DESC
            LIMIT 100
            """
        ).fetchall()

        for game_id, name, delta, now_count in rating_rows:
            if game_id in seen:
                continue
            severity = Severity.P2 if delta >= 2000 else Severity.P3
            reason = (
                f"TapTap 评分数激增 +{delta} "
                f"(当前 {self._fmt_num(now_count)})"
            )
            out.append(
                AlertCandidate(
                    game_id=game_id,
                    game_name=name,
                    alert_type=AlertType.TAPTAP_HEAT,
                    severity=severity,
                    reason=reason,
                    score=self._lookup_score(conn, game_id),
                    metadata={
                        "rating_delta": int(delta),
                        "rating_count": int(now_count),
                        "source": "rating_count",
                    },
                    suggested_actions=[
                        "generate_report",
                        "add_to_watchlist",
                    ],
                )
            )
        return out

    def detect_review_burst(
        self, conn: psycopg.Connection
    ) -> list[AlertCandidate]:
        """Today's review count >= 3x 7-day mean AND >= 20 reviews.

        Review table may not be populated yet — if so, return [].
        """
        try:
            rows = conn.execute(
                """
                WITH today AS (
                    SELECT pl.game_id, COUNT(*)::INT AS c
                    FROM reviews r
                    JOIN platform_listings pl ON pl.id = r.platform_listing_id
                    WHERE r.posted_at >= CURRENT_DATE
                      AND r.posted_at <  CURRENT_DATE + INTERVAL '1 day'
                    GROUP BY pl.game_id
                ),
                baseline AS (
                    SELECT pl.game_id,
                           (COUNT(*) / 7.0)::FLOAT AS c
                    FROM reviews r
                    JOIN platform_listings pl ON pl.id = r.platform_listing_id
                    WHERE r.posted_at >= CURRENT_DATE - INTERVAL '8 days'
                      AND r.posted_at <  CURRENT_DATE - INTERVAL '1 day'
                    GROUP BY pl.game_id
                )
                SELECT t.game_id,
                       COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
                       t.c AS today_c,
                       COALESCE(b.c, 0) AS base_c
                FROM today t
                JOIN games g ON g.id = t.game_id
                LEFT JOIN baseline b USING (game_id)
                WHERE t.c >= 20
                  AND (COALESCE(b.c, 0) = 0 OR t.c >= b.c * 3)
                ORDER BY t.c DESC
                LIMIT 100
                """
            ).fetchall()
        except psycopg.errors.UndefinedTable:
            logger.info("reviews table not present, skipping review_burst detector")
            conn.rollback()
            return []
        except psycopg.Error as e:
            logger.warning(f"review_burst query failed: {e}")
            conn.rollback()
            return []

        out: list[AlertCandidate] = []
        for game_id, name, today_c, base_c in rows:
            ratio = (today_c / base_c) if base_c else float("inf")
            if today_c >= 200 or ratio >= 10:
                severity = Severity.P1
            elif ratio >= 5:
                severity = Severity.P2
            else:
                severity = Severity.P3
            ratio_txt = "∞" if not base_c else f"{ratio:.1f}×"
            reason = (
                f"今日评论 {today_c} 条 "
                f"(7日日均 {base_c:.1f} 条, {ratio_txt})"
            )
            out.append(
                AlertCandidate(
                    game_id=game_id,
                    game_name=name,
                    alert_type=AlertType.REVIEW_BURST,
                    severity=severity,
                    reason=reason,
                    score=self._lookup_score(conn, game_id),
                    metadata={
                        "today_reviews": int(today_c),
                        "baseline_daily_avg": round(float(base_c or 0), 2),
                        "ratio": None if not base_c else round(ratio, 2),
                    },
                    suggested_actions=[
                        "generate_report",
                        "view_dashboard",
                    ],
                )
            )
        return out

    # --------------------------------------------------------
    # Severity helpers
    # --------------------------------------------------------
    @staticmethod
    def _ranking_severity(rank_change: int, rising_platforms: int) -> Severity:
        if rank_change >= 50 and rising_platforms >= 2:
            return Severity.P1
        if rank_change >= 30:
            return Severity.P2
        return Severity.P3

    @staticmethod
    def _social_severity(ratio: float, today_views: int) -> Severity:
        if ratio >= 10 or today_views >= 1_000_000:
            return Severity.P1
        if ratio >= 5:
            return Severity.P2
        return Severity.P3

    # --------------------------------------------------------
    # Dedup / cooldown / db helpers
    # --------------------------------------------------------
    @staticmethod
    def _dedupe(cands: list[AlertCandidate]) -> list[AlertCandidate]:
        """Collapse to one candidate per game (highest severity wins).

        When two alerts tie on severity we keep the first, which preserves the
        detector order used in evaluate_alerts().
        """
        best: dict[int, AlertCandidate] = {}
        for c in cands:
            cur = best.get(c.game_id)
            if cur is None or _SEVERITY_RANK[c.severity] > _SEVERITY_RANK[cur.severity]:
                best[c.game_id] = c
        return list(best.values())

    def _ensure_system_alerts(
        self, conn: psycopg.Connection
    ) -> dict[AlertType, int]:
        """Make sure one system Alert row exists per detector type.

        Returns mapping AlertType -> alerts.id so alert_events can FK it.
        """
        mapping: dict[AlertType, int] = {}
        for at in AlertType:
            system_name = f"__system__{at.value}"
            row = conn.execute(
                "SELECT id FROM alerts WHERE name = %s LIMIT 1",
                (system_name,),
            ).fetchone()
            if row:
                mapping[at] = row[0]
                continue

            row = conn.execute(
                """
                INSERT INTO alerts
                  (name, alert_type, severity, conditions,
                   notify_channel, is_active, cooldown_hours)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
                RETURNING id
                """,
                (
                    system_name,
                    at.value,
                    Severity.P2.value,
                    json.dumps({"system": True}),
                    "feishu",
                    True,
                    12,
                ),
            ).fetchone()
            mapping[at] = row[0]
        return mapping

    def _is_in_cooldown(
        self,
        conn: psycopg.Connection,
        alert_id: int,
        game_id: int,
        cooldown_hours: int,
    ) -> bool:
        cutoff = datetime.now() - timedelta(hours=cooldown_hours)
        row = conn.execute(
            """
            SELECT 1 FROM alert_events
            WHERE alert_id = %s AND game_id = %s AND triggered_at >= %s
            LIMIT 1
            """,
            (alert_id, game_id, cutoff),
        ).fetchone()
        return row is not None

    @staticmethod
    def _lookup_score(conn: psycopg.Connection, game_id: int) -> int | None:
        row = conn.execute(
            """
            SELECT overall_score FROM potential_scores
            WHERE game_id = %s AND scored_at = CURRENT_DATE
            LIMIT 1
            """,
            (game_id,),
        ).fetchone()
        return int(row[0]) if row else None

    @staticmethod
    def _fmt_num(n: int | float | None) -> str:
        if n is None:
            return "0"
        n = int(n)
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)

    # --------------------------------------------------------
    # Legacy user-rule path (backward compat)
    # --------------------------------------------------------
    def _evaluate_user_rules(
        self,
        conn: psycopg.Connection,
        system_alert_ids: dict[AlertType, int],
    ) -> int:
        """Evaluate rows in `alerts` that are user-defined (not system rows)."""
        system_ids = set(system_alert_ids.values())
        rules = conn.execute(
            """
            SELECT id, name, alert_type, severity, conditions,
                   webhook_url, cooldown_hours
            FROM alerts
            WHERE is_active = TRUE
            """
        ).fetchall()

        fired = 0
        for (
            alert_id, name, alert_type, severity, conditions,
            webhook_url, cooldown_hours,
        ) in rules:
            if alert_id in system_ids:
                continue
            try:
                conds = (
                    conditions if isinstance(conditions, dict)
                    else json.loads(conditions)
                )
            except (TypeError, json.JSONDecodeError):
                logger.warning(f"alert {alert_id} has invalid conditions, skipping")
                continue

            matches = self._find_matching_games(conn, conds)
            for game_id, game_name, score in matches:
                if self._is_in_cooldown(conn, alert_id, game_id, cooldown_hours):
                    continue
                try:
                    at_enum = AlertType(alert_type) if alert_type else AlertType.RANKING_JUMP
                except ValueError:
                    at_enum = AlertType.RANKING_JUMP
                try:
                    sev_enum = Severity(severity) if severity else Severity.P2
                except ValueError:
                    sev_enum = Severity.P2

                conn.execute(
                    """
                    INSERT INTO alert_events
                      (alert_id, game_id, score, alert_type, severity, reason)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        alert_id, game_id, score,
                        at_enum.value, sev_enum.value,
                        f"用户规则「{name}」命中, 评分 {score}",
                    ),
                )
                cand = AlertCandidate(
                    game_id=game_id,
                    game_name=game_name,
                    alert_type=at_enum,
                    severity=sev_enum,
                    reason=f"用户规则「{name}」命中, 评分 {score}/100",
                    score=score,
                    metadata={"rule_id": alert_id, "rule_name": name},
                    suggested_actions=["generate_report", "view_dashboard"],
                )
                self._send_notification(webhook_url or self.feishu_webhook, cand)
                fired += 1
        return fired

    def _find_matching_games(
        self, conn: psycopg.Connection, conditions: dict
    ) -> list[tuple[int, str, int]]:
        """Find games matching user rule conditions."""
        where_clauses = ["1=1"]
        params: list = []

        min_score = conditions.get("min_score")
        if min_score is not None:
            where_clauses.append("ps.overall_score >= %s")
            params.append(min_score)

        genres = conditions.get("genres")
        if genres:
            where_clauses.append("g.genre = ANY(%s)")
            params.append(genres)

        min_velocity = conditions.get("min_velocity")
        if min_velocity is not None:
            where_clauses.append("ps.ranking_velocity >= %s")
            params.append(min_velocity)

        platforms = conditions.get("platforms")
        if platforms:
            where_clauses.append(
                """EXISTS (
                    SELECT 1 FROM platform_listings pl
                    WHERE pl.game_id = g.id AND pl.platform = ANY(%s)
                )"""
            )
            params.append(platforms)

        where_sql = " AND ".join(where_clauses)

        rows = conn.execute(
            f"""
            SELECT g.id, COALESCE(g.name_zh, g.name_en, 'Unknown'),
                   ps.overall_score
            FROM games g
            JOIN potential_scores ps ON g.id = ps.game_id
            WHERE ps.scored_at = CURRENT_DATE
              AND {where_sql}
            ORDER BY ps.overall_score DESC
            LIMIT 50
            """,
            params,
        ).fetchall()
        return rows

    # --------------------------------------------------------
    # Notification (Feishu interactive card)
    # --------------------------------------------------------
    def _send_notification(
        self,
        webhook_url: str | None,
        candidate: AlertCandidate,
    ):
        """Send a rich Feishu interactive card for one candidate."""
        if not webhook_url:
            logger.warning("No webhook URL configured, skipping notification")
            return

        emoji, label = _ALERT_TYPE_META[candidate.alert_type]
        template = _SEVERITY_TEMPLATE[candidate.severity]

        score_line = (
            f"**潜力评分**: {candidate.score}/100\n"
            if candidate.score is not None
            else ""
        )
        body_md = (
            f"**游戏**: {candidate.game_name}\n"
            f"**等级**: `{candidate.severity.value}`\n"
            f"{score_line}"
            f"**触发原因**: {candidate.reason}"
        )

        elements: list[dict] = [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": body_md},
            },
            {"tag": "hr"},
            self._build_actions(candidate),
        ]

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"{emoji} {label}",
                    },
                    "template": template,
                },
                "elements": elements,
            },
        }

        try:
            with httpx.Client() as client:
                resp = client.post(webhook_url, json=payload, timeout=10)
                resp.raise_for_status()
                logger.info(
                    f"Sent {candidate.alert_type.value}/{candidate.severity.value} "
                    f"alert for '{candidate.game_name}'"
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to send notification: {e}")

    def _build_actions(self, candidate: AlertCandidate) -> dict:
        """Build a Feishu card `action` element with suggested buttons.

        We always show the three core actions (view, IAA analysis, watchlist)
        but highlight the ones in `suggested_actions` with a darker button type.
        """
        base_url = self.app_url.rstrip("/")
        suggested = set(candidate.suggested_actions)

        def _btn(text: str, url: str, key: str, default_type: str = "default") -> dict:
            btn_type = "primary" if key in suggested else default_type
            return {
                "tag": "button",
                "text": {"tag": "plain_text", "content": text},
                "type": btn_type,
                "url": url,
            }

        return {
            "tag": "action",
            "actions": [
                _btn(
                    "查看战报",
                    f"{base_url}/games/{candidate.game_id}",
                    "view_dashboard",
                    default_type="primary",
                ),
                _btn(
                    "生成 IAA 分析",
                    f"{base_url}/iaa/{candidate.game_id}",
                    "generate_report",
                ),
                _btn(
                    "纳入监控池",
                    f"{base_url}/api/games/{candidate.game_id}/watchlist",
                    "add_to_watchlist",
                ),
            ],
        }
