"""Feishu bot command worker — processes pending commands and sends replies.

Picks up rows from ``feishu_bot_commands`` where ``status = 'pending'``,
dispatches to the appropriate command handler, calls the Feishu Open API
to send a text reply, and updates the row to ``success`` or ``failed``.

Supported commands:
  /analyze <game>   — fetch GameReport summary
  /iaa <game>       — fetch IAA advice summary
  /similar <game>   — list similar games via embedding cosine distance
  /trending [genre] — top rising games (optionally filtered by genre)
  /help             — list commands
"""

from __future__ import annotations

import json
import logging
import os
import time

import httpx
import psycopg

logger = logging.getLogger(__name__)

FEISHU_OPEN_HOST = "https://open.feishu.cn/open-apis"

# Simple in-memory token cache
_token_cache: dict = {}


def get_tenant_access_token() -> str | None:
    """Get Feishu tenant access token, cached in-memory."""
    if _token_cache.get("token") and _token_cache.get("expires_at", 0) > time.time():
        return _token_cache["token"]

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        logger.warning("FEISHU_APP_ID / FEISHU_APP_SECRET not configured")
        return None

    try:
        with httpx.Client() as client:
            resp = client.post(
                f"{FEISHU_OPEN_HOST}/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.error(f"Feishu token error: {data}")
                return None
            _token_cache["token"] = data["tenant_access_token"]
            _token_cache["expires_at"] = time.time() + (
                data.get("expire", 7200) - 60
            )
            return _token_cache["token"]
    except Exception as e:
        logger.error(f"Feishu token failed: {e}")
        return None


def send_feishu_reply(receive_id: str, text: str, receive_id_type: str = "chat_id") -> bool:
    """Send a text message to a Feishu chat or user."""
    token = get_tenant_access_token()
    if not token:
        return False

    try:
        with httpx.Client() as client:
            resp = client.post(
                f"{FEISHU_OPEN_HOST}/im/v1/messages?receive_id_type={receive_id_type}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={
                    "receive_id": receive_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}),
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.error(f"Feishu send error: {data}")
                return False
            return True
    except Exception as e:
        logger.error(f"Feishu send failed: {e}")
        return False


def find_game_by_name(conn: psycopg.Connection, name: str) -> tuple[int, str] | None:
    """Fuzzy find a game by name. Returns (id, display_name) or None."""
    row = conn.execute(
        """
        SELECT id, COALESCE(name_zh, name_en) AS n
        FROM games
        WHERE name_zh ILIKE %s OR name_en ILIKE %s
        ORDER BY id
        LIMIT 1
        """,
        (f"%{name}%", f"%{name}%"),
    ).fetchone()
    return row if row else None


def handle_analyze(conn: psycopg.Connection, args: str) -> str:
    """/analyze <game> — fetch report summary."""
    if not args.strip():
        return "用法: /analyze <游戏名>"
    game = find_game_by_name(conn, args.strip())
    if not game:
        return f"未找到游戏: {args}"

    game_id, name = game
    row = conn.execute(
        "SELECT payload FROM game_reports WHERE game_id = %s",
        (game_id,),
    ).fetchone()

    if not row:
        return f"📋 {name}\n尚未生成 AI 战报。可在 web 端触发生成。"

    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    positioning = payload.get("positioning", "-")
    grade = payload.get("iaa_advice", {}).get("overall_grade", "-")
    confidence = payload.get("overall_confidence", 0)

    app_url = os.environ.get("NEXT_PUBLIC_APP_URL", "http://localhost:3000")
    return (
        f"📋 {name}\n"
        f"定位: {positioning}\n"
        f"IAA 等级: {grade}\n"
        f"置信度: {int(confidence * 100)}%\n"
        f"详情: {app_url}/games/{game_id}"
    )


def handle_iaa(conn: psycopg.Connection, args: str) -> str:
    """/iaa <game> — fetch IAA advice summary."""
    if not args.strip():
        return "用法: /iaa <游戏名>"
    game = find_game_by_name(conn, args.strip())
    if not game:
        return f"未找到游戏: {args}"

    game_id, name = game
    row = conn.execute(
        "SELECT payload FROM game_reports WHERE game_id = %s",
        (game_id,),
    ).fetchone()

    if not row:
        return f"🎯 {name}\n尚未生成 IAA 分析。"

    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    advice = payload.get("iaa_advice", {})

    app_url = os.environ.get("NEXT_PUBLIC_APP_URL", "http://localhost:3000")
    lines = [f"🎯 {name} - IAA {advice.get('overall_grade', '-')}"]
    sp = advice.get("suitable_placements", [])
    if sp:
        lines.append("适合广告位:")
        for p in sp[:3]:
            lines.append(f"  • {p}")
    risks = advice.get("risks", [])
    if risks:
        lines.append("风险:")
        for r in risks[:2]:
            lines.append(f"  ⚠️ {r}")
    lines.append(f"详情: {app_url}/iaa/{game_id}")
    return "\n".join(lines)


def handle_similar(conn: psycopg.Connection, args: str) -> str:
    """/similar <game> — list similar games."""
    if not args.strip():
        return "用法: /similar <游戏名>"
    game = find_game_by_name(conn, args.strip())
    if not game:
        return f"未找到游戏: {args}"

    game_id, name = game
    try:
        rows = conn.execute(
            """
            SELECT g.id, COALESCE(g.name_zh, g.name_en)
            FROM game_embeddings target
            JOIN game_embeddings other ON other.game_id != target.game_id
            JOIN games g ON g.id = other.game_id
            WHERE target.game_id = %s
            ORDER BY target.embedding <=> other.embedding
            LIMIT 5
            """,
            (game_id,),
        ).fetchall()
    except Exception:
        conn.rollback()
        rows = []

    if not rows:
        return f"🔍 {name}\n尚未生成 embedding，暂无相似游戏。"

    lines = [f"🔍 {name} - 相似游戏:"]
    for i, (_id, n) in enumerate(rows, 1):
        lines.append(f"{i}. {n}")
    return "\n".join(lines)


def handle_trending(conn: psycopg.Connection, args: str) -> str:
    """/trending [genre] — top rising games."""
    genre = args.strip() or None

    sql = """
        SELECT g.id, COALESCE(g.name_zh, g.name_en), ps.overall_score, ps.ranking_velocity
        FROM games g
        JOIN potential_scores ps ON ps.game_id = g.id AND ps.scored_at = CURRENT_DATE
        WHERE ps.ranking_velocity >= 50
    """
    params: list = []
    if genre:
        sql += " AND g.genre = %s"
        params.append(genre)
    sql += " ORDER BY ps.ranking_velocity DESC LIMIT 5"

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return "📈 暂无显著上升的游戏"

    title = f"📈 上升最快 (品类: {genre})" if genre else "📈 上升最快"
    lines = [title]
    for i, (_id, n, score, v) in enumerate(rows, 1):
        lines.append(f"{i}. {n} - 评分 {score} / 速度 {v}")
    return "\n".join(lines)


def handle_help() -> str:
    return (
        "🤖 DreamBig Bot 命令:\n"
        "/analyze <游戏名> - 查看 AI 战报摘要\n"
        "/iaa <游戏名> - 查看 IAA 改造建议\n"
        "/similar <游戏名> - 查找相似游戏\n"
        "/trending [品类] - 今日上升最快\n"
        "/help - 显示此帮助"
    )


COMMAND_HANDLERS = {
    "analyze": handle_analyze,
    "iaa": handle_iaa,
    "similar": handle_similar,
    "trending": handle_trending,
}


def process_pending_commands(db_url: str) -> int:
    """Process pending FeishuBotCommand rows. Returns count processed."""
    processed = 0
    with psycopg.connect(db_url) as conn:
        rows = conn.execute(
            """
            SELECT id, message_id, user_open_id, chat_id, command, args
            FROM feishu_bot_commands
            WHERE status = 'pending'
            ORDER BY created_at
            LIMIT 20
            """
        ).fetchall()

        for cmd_id, _message_id, user_open_id, chat_id, command, args in rows:
            conn.execute(
                "UPDATE feishu_bot_commands SET status = 'processing' WHERE id = %s",
                (cmd_id,),
            )
            conn.commit()

            try:
                if command == "help":
                    response = handle_help()
                elif command in COMMAND_HANDLERS:
                    response = COMMAND_HANDLERS[command](conn, args or "")
                else:
                    response = f"未知命令: /{command}\n发送 /help 查看所有命令"

                # Send reply — prefer chat_id, fall back to user open_id
                receive_id = chat_id or user_open_id
                receive_id_type = "chat_id" if chat_id else "open_id"
                if receive_id and send_feishu_reply(
                    receive_id, response, receive_id_type=receive_id_type
                ):
                    conn.execute(
                        """
                        UPDATE feishu_bot_commands
                        SET status = 'success', response = %s, response_at = NOW()
                        WHERE id = %s
                        """,
                        (response[:2000], cmd_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE feishu_bot_commands
                        SET status = 'failed', error_msg = 'send_failed', response_at = NOW()
                        WHERE id = %s
                        """,
                        (cmd_id,),
                    )
                conn.commit()
                processed += 1
            except Exception as e:
                logger.error(f"Command {cmd_id} failed: {e}")
                conn.rollback()
                conn.execute(
                    """
                    UPDATE feishu_bot_commands
                    SET status = 'failed', error_msg = %s, response_at = NOW()
                    WHERE id = %s
                    """,
                    (str(e)[:500], cmd_id),
                )
                conn.commit()

    logger.info(f"Feishu command worker: processed {processed} commands")
    return processed


def run_feishu_command_processor(db_url: str) -> int:
    """Entry point for worker.py / scheduler.py."""
    return process_pending_commands(db_url)
