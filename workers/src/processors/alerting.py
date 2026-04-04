"""Alert evaluation and notification system.

Checks game scores against user-configured alert rules
and sends notifications via Feishu webhook.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta

import httpx
import psycopg

logger = logging.getLogger(__name__)


class AlertEngine:
    """Evaluates alert rules and sends notifications."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.feishu_webhook = os.environ.get("FEISHU_WEBHOOK_URL")

    def evaluate_alerts(self) -> int:
        """Evaluate all active alert rules against current game scores."""
        triggered = 0

        with psycopg.connect(self.db_url) as conn:
            # Get all active alert rules
            alerts = conn.execute(
                "SELECT id, name, conditions, webhook_url, cooldown_hours "
                "FROM alerts WHERE is_active = TRUE"
            ).fetchall()

            for alert_id, name, conditions, webhook_url, cooldown_hours in alerts:
                conds = conditions if isinstance(conditions, dict) else json.loads(conditions)
                matches = self._find_matching_games(conn, conds)

                for game in matches:
                    game_id, game_name, score = game

                    # Check cooldown
                    if self._is_in_cooldown(conn, alert_id, game_id, cooldown_hours):
                        continue

                    # Record alert event
                    conn.execute(
                        """
                        INSERT INTO alert_events (alert_id, game_id, score)
                        VALUES (%s, %s, %s)
                        """,
                        (alert_id, game_id, score),
                    )

                    # Send notification
                    self._send_notification(
                        webhook_url or self.feishu_webhook,
                        alert_name=name,
                        game_name=game_name,
                        score=score,
                        game_id=game_id,
                    )
                    triggered += 1

            conn.commit()

        logger.info(f"Evaluated {len(alerts)} rules, triggered {triggered} alerts")
        return triggered

    def _find_matching_games(
        self, conn: psycopg.Connection, conditions: dict
    ) -> list[tuple[int, str, int]]:
        """Find games matching alert conditions."""
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

    def _is_in_cooldown(
        self, conn: psycopg.Connection, alert_id: int, game_id: int, cooldown_hours: int
    ) -> bool:
        """Check if this alert+game combination is in cooldown."""
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

    def _send_notification(
        self,
        webhook_url: str | None,
        alert_name: str,
        game_name: str,
        score: int,
        game_id: int,
    ):
        """Send alert notification via Feishu webhook."""
        if not webhook_url:
            logger.warning("No webhook URL configured, skipping notification")
            return

        app_url = os.environ.get("NEXT_PUBLIC_APP_URL", "http://localhost:3000")

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"🎯 {alert_name}"},
                    "template": "green" if score >= 75 else "yellow",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": (
                                f"**游戏**: {game_name}\n"
                                f"**潜力评分**: {score}/100\n"
                                f"**详情**: [查看]({app_url}/games/{game_id})"
                            ),
                        },
                    }
                ],
            },
        }

        try:
            with httpx.Client() as client:
                resp = client.post(webhook_url, json=payload, timeout=10)
                resp.raise_for_status()
                logger.info(f"Sent alert for '{game_name}' (score: {score})")
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
