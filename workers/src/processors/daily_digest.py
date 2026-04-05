"""Daily digest generator for subscriptions.

For each active subscription with schedule='daily' that hasn't been sent today:
- Build a filter based on (dimension, value)
- Query today's data matching the filter
- Render a personalized digest (markdown for feishu/wecom, HTML for email)
- Dispatch via the subscription's configured channel
- Update last_sent_at on success
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date

import psycopg

from src.utils.notifications import send_feishu_text
from src.utils.notifications_email import send_email
from src.utils.notifications_wecom import send_wecom_markdown

logger = logging.getLogger(__name__)


# ============================================================
# DigestBuilder — one subscription worth of content
# ============================================================
class DigestBuilder:
    """Builds a digest payload for one subscription."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def build(self, dimension: str, value: str) -> dict:
        """Return dict with: top_potential, rank_movers, iaa_candidates, social_bursts."""
        filt_sql, filt_params = self._build_filter(dimension, value)

        top_potential = self._top_potential(filt_sql, filt_params)
        rank_movers = self._rank_movers(filt_sql, filt_params)
        iaa_candidates = self._iaa_candidates(filt_sql, filt_params)
        social_bursts = self._social_bursts(filt_sql, filt_params)

        return {
            "date": date.today().isoformat(),
            "dimension": dimension,
            "value": value,
            "top_potential": top_potential,
            "rank_movers": rank_movers,
            "iaa_candidates": iaa_candidates,
            "social_bursts": social_bursts,
        }

    def _build_filter(self, dimension: str, value: str) -> tuple[str, list]:
        """Return SQL fragment (applied to games g) + params."""
        if dimension == "platform":
            return (
                """EXISTS (
                    SELECT 1 FROM platform_listings pl
                    WHERE pl.game_id = g.id AND pl.platform = %s
                )""",
                [value],
            )
        if dimension == "genre":
            return ("g.genre = %s", [value])
        if dimension == "region":
            return (
                """EXISTS (
                    SELECT 1 FROM platform_listings pl
                    JOIN ranking_snapshots rs ON rs.platform_listing_id = pl.id
                    WHERE pl.game_id = g.id
                      AND rs.region = %s
                      AND rs.snapshot_date = CURRENT_DATE
                )""",
                [value],
            )
        if dimension == "keyword":
            return (
                "(g.name_zh ILIKE %s OR g.name_en ILIKE %s OR %s = ANY(g.gameplay_tags))",
                [f"%{value}%", f"%{value}%", value],
            )
        if dimension == "game":
            try:
                return ("g.id = %s", [int(value)])
            except (ValueError, TypeError):
                return ("FALSE", [])
        return ("TRUE", [])

    def _top_potential(self, filt_sql: str, filt_params: list) -> list[dict]:
        try:
            rows = self.conn.execute(
                f"""
                SELECT g.id, COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
                       ps.overall_score
                FROM games g
                JOIN potential_scores ps
                     ON ps.game_id = g.id AND ps.scored_at = CURRENT_DATE
                WHERE ps.overall_score >= 50 AND ({filt_sql})
                ORDER BY ps.overall_score DESC
                LIMIT 5
                """,
                filt_params,
            ).fetchall()
        except psycopg.Error as e:
            logger.warning(f"top_potential query failed: {e}")
            self.conn.rollback()
            return []
        return [{"id": r[0], "name": r[1], "score": int(r[2])} for r in rows]

    def _rank_movers(self, filt_sql: str, filt_params: list) -> list[dict]:
        try:
            rows = self.conn.execute(
                f"""
                SELECT g.id, COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
                       pl.platform, rs.rank_change, rs.rank_position
                FROM games g
                JOIN platform_listings pl ON pl.game_id = g.id
                JOIN ranking_snapshots rs ON rs.platform_listing_id = pl.id
                WHERE rs.snapshot_date = CURRENT_DATE
                  AND rs.rank_change IS NOT NULL
                  AND rs.rank_change > 0
                  AND ({filt_sql})
                ORDER BY rs.rank_change DESC
                LIMIT 10
                """,
                filt_params,
            ).fetchall()
        except psycopg.Error as e:
            logger.warning(f"rank_movers query failed: {e}")
            self.conn.rollback()
            return []
        return [
            {
                "id": r[0],
                "name": r[1],
                "platform": r[2],
                "change": int(r[3]),
                "position": int(r[4]),
            }
            for r in rows
        ]

    def _iaa_candidates(self, filt_sql: str, filt_params: list) -> list[dict]:
        try:
            rows = self.conn.execute(
                f"""
                SELECT g.id, COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
                       ps.overall_score, g.iaa_suitability, g.iaa_grade
                FROM games g
                JOIN potential_scores ps
                     ON ps.game_id = g.id AND ps.scored_at = CURRENT_DATE
                WHERE g.iaa_suitability >= 70
                  AND ps.overall_score >= 60
                  AND ({filt_sql})
                ORDER BY ps.overall_score DESC
                LIMIT 5
                """,
                filt_params,
            ).fetchall()
        except psycopg.Error as e:
            logger.warning(f"iaa_candidates query failed: {e}")
            self.conn.rollback()
            return []
        return [
            {
                "id": r[0],
                "name": r[1],
                "score": int(r[2]),
                "iaa_suit": int(r[3]) if r[3] is not None else 0,
                "grade": r[4],
            }
            for r in rows
        ]

    def _social_bursts(self, filt_sql: str, filt_params: list) -> list[dict]:
        try:
            rows = self.conn.execute(
                f"""
                SELECT g.id, COALESCE(g.name_zh, g.name_en, 'Unknown') AS name,
                       SUM(ss.view_count) AS views
                FROM games g
                JOIN social_signals ss ON ss.game_id = g.id
                WHERE ss.signal_date >= CURRENT_DATE - INTERVAL '3 days'
                  AND ({filt_sql})
                GROUP BY g.id, g.name_zh, g.name_en
                ORDER BY views DESC NULLS LAST
                LIMIT 5
                """,
                filt_params,
            ).fetchall()
        except psycopg.Error as e:
            logger.warning(f"social_bursts query failed: {e}")
            self.conn.rollback()
            return []
        return [
            {"id": r[0], "name": r[1], "views": int(r[2] or 0)}
            for r in rows
            if (r[2] or 0) > 0
        ]


# ============================================================
# DigestDispatcher — find subs, build, send, mark sent
# ============================================================
class DigestDispatcher:
    """Reads active subscriptions and dispatches digests via each channel."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.app_url = os.environ.get(
            "NEXT_PUBLIC_APP_URL", "http://localhost:3000"
        ).rstrip("/")

    def dispatch_daily(self) -> int:
        """Find active 'daily' subscriptions not sent today and dispatch."""
        sent = 0
        with psycopg.connect(self.db_url) as conn:
            try:
                subs = conn.execute(
                    """
                    SELECT s.id, s.user_id, s.dimension, s.value,
                           s.channel, s.channel_config,
                           u.email, u.name
                    FROM subscriptions s
                    LEFT JOIN users u ON u.id = s.user_id
                    WHERE s.is_active = TRUE
                      AND s.schedule = 'daily'
                      AND (s.last_sent_at IS NULL OR s.last_sent_at < CURRENT_DATE)
                    """,
                ).fetchall()
            except psycopg.Error as e:
                logger.error(f"Failed to load subscriptions: {e}")
                return 0

            builder = DigestBuilder(conn)

            for (
                sub_id, user_id, dimension, value,
                channel, channel_config, user_email, user_name,
            ) in subs:
                try:
                    digest = builder.build(dimension, value)
                    if not any(
                        digest[k]
                        for k in (
                            "top_potential",
                            "rank_movers",
                            "iaa_candidates",
                            "social_bursts",
                        )
                    ):
                        logger.info(
                            f"Empty digest for sub {sub_id} ({dimension}={value}), skipping"
                        )
                        continue

                    cfg = self._parse_config(channel_config)

                    dispatched = False
                    if channel == "feishu":
                        dispatched = self._send_feishu(cfg, digest, user_name)
                    elif channel == "wecom":
                        dispatched = self._send_wecom(cfg, digest, user_name)
                    elif channel == "email":
                        dispatched = self._send_email(
                            user_email, cfg, digest, user_name
                        )
                    else:
                        logger.warning(
                            f"Unknown channel '{channel}' for sub {sub_id}"
                        )
                        continue

                    if dispatched:
                        conn.execute(
                            "UPDATE subscriptions SET last_sent_at = NOW() WHERE id = %s",
                            (sub_id,),
                        )
                        conn.commit()
                        sent += 1
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Failed digest for sub {sub_id}: {e}")
                    conn.rollback()

        logger.info(f"Daily digest dispatch: {sent} sent")
        return sent

    # --------------------------------------------------------
    # Channel senders
    # --------------------------------------------------------
    @staticmethod
    def _parse_config(channel_config) -> dict:
        if isinstance(channel_config, dict):
            return channel_config
        if not channel_config:
            return {}
        try:
            return json.loads(channel_config)
        except (TypeError, json.JSONDecodeError):
            return {}

    def _send_feishu(self, cfg: dict, digest: dict, user_name: str | None) -> bool:
        webhook = cfg.get("webhook_url") or os.environ.get("FEISHU_WEBHOOK_URL")
        if not webhook:
            logger.warning("Feishu webhook not configured, skipping")
            return False
        content = self._build_markdown(digest, user_name)
        send_feishu_text(webhook, content)
        return True

    def _send_wecom(self, cfg: dict, digest: dict, user_name: str | None) -> bool:
        webhook = cfg.get("webhook_url")
        if not webhook:
            logger.warning("WeCom webhook not configured in subscription, skipping")
            return False
        md = self._build_markdown(digest, user_name)
        send_wecom_markdown(webhook, md)
        return True

    def _send_email(
        self,
        user_email: str | None,
        cfg: dict,
        digest: dict,
        user_name: str | None,
    ) -> bool:
        to = cfg.get("email") or user_email
        if not to:
            logger.warning("No email address for subscription, skipping")
            return False
        html = self._build_html(digest, user_name)
        text = self._build_markdown(digest, user_name)
        return send_email(
            to, f"DreamBig 日报 - {digest['date']}", html, body_text=text
        )

    # --------------------------------------------------------
    # Renderers
    # --------------------------------------------------------
    def _build_markdown(self, digest: dict, user_name: str | None) -> str:
        """Build a markdown-formatted digest with all sections."""
        lines: list[str] = [f"# DreamBig 日报 - {digest['date']}", ""]

        greeting = (
            f"Hi {user_name}，" if user_name else "Hi，"
        ) + f"这是你订阅的 **{digest['dimension']} = {digest['value']}** 今日摘要：\n"
        lines.append(greeting)

        if digest["top_potential"]:
            lines.append("## 🏆 高潜力 Top 5")
            for g in digest["top_potential"]:
                lines.append(
                    f"- [{g['name']}]({self.app_url}/games/{g['id']}) "
                    f"- 评分 {g['score']}"
                )
            lines.append("")

        if digest["rank_movers"]:
            lines.append("## 📈 榜单上升 Top 10")
            for g in digest["rank_movers"]:
                lines.append(
                    f"- [{g['name']}]({self.app_url}/games/{g['id']}) "
                    f"- {g['platform']} 上升 {g['change']} 名 (#{g['position']})"
                )
            lines.append("")

        if digest["iaa_candidates"]:
            lines.append("## 🎯 IAA 候选 Top 5")
            for g in digest["iaa_candidates"]:
                grade = g.get("grade") or "-"
                lines.append(
                    f"- [{g['name']}]({self.app_url}/iaa/{g['id']}) "
                    f"- 评分 {g['score']} · IAA {grade}"
                )
            lines.append("")

        if digest["social_bursts"]:
            lines.append("## 🔥 社交爆发 Top 5")
            for g in digest["social_bursts"]:
                lines.append(
                    f"- [{g['name']}]({self.app_url}/games/{g['id']}) "
                    f"- 3日播放 {g['views']:,}"
                )
            lines.append("")

        lines.append("---")
        lines.append("DreamBig · 爆款游戏监控平台")
        return "\n".join(lines)

    def _build_html(self, digest: dict, user_name: str | None) -> str:
        """Build a minimal HTML email body from the markdown digest."""
        md = self._build_markdown(digest, user_name)
        escaped = (
            md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "</head>"
            '<body style="font-family: -apple-system, BlinkMacSystemFont, '
            "'Segoe UI', Roboto, sans-serif; max-width: 640px; "
            'margin: 20px auto; padding: 20px; background: #fafafa;">'
            '<div style="background: white; padding: 24px; border-radius: 8px; '
            'box-shadow: 0 1px 3px rgba(0,0,0,0.1);">'
            '<div style="white-space: pre-wrap; line-height: 1.6; color: #333;">'
            f"{escaped}"
            "</div></div>"
            '<p style="color:#888;font-size:12px;text-align:center;margin-top:16px;">'
            "DreamBig - 爆款游戏监控平台"
            "</p>"
            "</body></html>"
        )


# ============================================================
# Entry point (for worker.py scheduler)
# ============================================================
def run_daily_digest(db_url: str) -> int:
    """Entry function for worker.py scheduler. Returns number dispatched."""
    return DigestDispatcher(db_url).dispatch_daily()
