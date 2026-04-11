"""WeChat mini-game gameplay intel processor.

For each top cross-chart WeChat game, aggregate the public signals
already captured elsewhere in the pipeline (editor_intro, screenshot
URLs, review topic clusters, social hook phrases) and ask Sonnet to
synthesize a structured fact sheet:

    - gameplay_intro (1-3 Chinese sentences)
    - features       (3-5 short Chinese tags)
    - art_style      (primary + secondary + evidence quotes)

The result is persisted into ``games.metadata.gameplay_intel`` as a
JSON sub-object — zero-migration — so the web game detail page can
render it directly.

Design philosophy (mirroring the wechat_intelligence v2 rewrite):
- No new scraping. We reuse run_fetch_details' output.
- The LLM is explicitly told to keep quiet when signals are thin —
  features/art_style return empty and confidence drops below 0.5.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any

import psycopg

from src.llm import PoeClient, get_model_for_task
from src.llm.prompts.gameplay_intel import (
    GAMEPLAY_INTEL_PROMPT,
    GameplayIntelReport,
    build_gameplay_intel_messages,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = GAMEPLAY_INTEL_PROMPT.version  # "v1"
DEFAULT_CONCURRENCY = 4


# ============================================================
# Target selection + source gathering
# ============================================================


def _select_target_games(
    conn: psycopg.Connection, limit: int, target_date: date | None
) -> list[int]:
    """Pick the top WeChat mini games to analyze today.

    Priority: games in today's cross-chart top-100 ordered by number of
    charts they appear on (descending). Falls back to games with a
    non-empty ``metadata->>'description'`` if ranking is empty.
    """
    today = target_date or date.today()
    rows = conn.execute(
        """
        SELECT g.id
        FROM ranking_snapshots rs
        JOIN platform_listings pl ON rs.platform_listing_id = pl.id
        JOIN games g ON pl.game_id = g.id
        WHERE pl.platform = 'wechat_mini'
          AND rs.snapshot_date = %s
          AND rs.rank_position <= 100
        GROUP BY g.id
        ORDER BY COUNT(DISTINCT rs.chart_type) DESC,
                 MIN(rs.rank_position) ASC
        LIMIT %s
        """,
        (today, limit),
    ).fetchall()
    if rows:
        return [int(r[0]) for r in rows]

    # Fallback: any WeChat game that already has some description text.
    fallback = conn.execute(
        """
        SELECT g.id
        FROM games g
        JOIN platform_listings pl ON pl.game_id = g.id
        WHERE pl.platform = 'wechat_mini'
          AND COALESCE(NULLIF(g.metadata->>'description', ''), '') <> ''
        ORDER BY g.updated_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [int(r[0]) for r in fallback]


def _gather_game_sources(
    conn: psycopg.Connection, game_id: int
) -> dict[str, Any] | None:
    """Pull every input block needed to generate one gameplay intel report.

    Returns None if the game row doesn't even exist; always returns a
    dict otherwise — callers must inspect the blocks to know if the
    evidence is thin.
    """
    game_row = conn.execute(
        """
        SELECT id,
               COALESCE(name_zh, name_en, 'Unknown') AS name,
               COALESCE(developer, '-') AS developer,
               COALESCE(genre, '-') AS genre,
               metadata
        FROM games
        WHERE id = %s
        """,
        (game_id,),
    ).fetchone()
    if not game_row:
        return None

    metadata = game_row[4] or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}

    editor_intro = (metadata.get("description") or "").strip()

    raw_shots = metadata.get("screenshots") or []
    if isinstance(raw_shots, str):
        try:
            raw_shots = json.loads(raw_shots)
        except json.JSONDecodeError:
            raw_shots = []
    screenshots: list[str] = [
        s for s in raw_shots if isinstance(s, str) and s.startswith("http")
    ]

    review_topic_rows = conn.execute(
        """
        SELECT sentiment, topic, review_count, snippet
        FROM review_topic_summaries
        WHERE game_id = %s
        ORDER BY computed_at DESC, review_count DESC
        LIMIT 12
        """,
        (game_id,),
    ).fetchall()

    hook_rows = conn.execute(
        """
        SELECT hook_phrase, platform, view_count
        FROM social_content_samples
        WHERE game_id = %s
          AND hook_phrase IS NOT NULL
          AND LENGTH(hook_phrase) > 0
        ORDER BY view_count DESC
        LIMIT 8
        """,
        (game_id,),
    ).fetchall()

    return {
        "game_id": int(game_row[0]),
        "name": game_row[1],
        "developer": game_row[2],
        "genre": game_row[3],
        "editor_intro": editor_intro,
        "screenshots": screenshots,
        "review_topics": [
            {
                "sentiment": r[0],
                "topic": r[1],
                "count": int(r[2] or 0),
                "snippet": (r[3] or "").strip(),
            }
            for r in review_topic_rows
        ],
        "hooks": [
            {
                "hook": r[0],
                "platform": r[1],
                "views": int(r[2] or 0),
            }
            for r in hook_rows
        ],
    }


# ============================================================
# Block formatters (sources → prompt-friendly text)
# ============================================================


def _fmt_editor_intro(text: str) -> str:
    if not text:
        return "（应用宝 editor_intro 为空 — 没有官方介绍文案）"
    return text.strip()


def _fmt_screenshots(urls: list[str]) -> str:
    if not urls:
        return "（没有截图 URL — screenshot_refs 必须返回空列表）"
    lines = [f"[{i}] {u}" for i, u in enumerate(urls)]
    return "\n".join(lines)


def _fmt_review_topics(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "（没有评论话题聚类 — 不能引用玩家反馈作为证据）"
    lines = []
    for r in rows:
        snippet = (r.get("snippet") or "")[:60]
        lines.append(
            f"- [{r['sentiment']}] {r['topic']} "
            f"(×{r['count']}) 片段: {snippet}"
        )
    return "\n".join(lines)


def _fmt_hooks(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "（没有 hook 短句 — 不能推断传播点 / 短视频钩子）"
    lines = []
    for r in rows:
        views = r["views"]
        view_str = f"{views / 10000:.1f}w" if views >= 10000 else str(views)
        lines.append(f'- [{r["platform"]},{view_str}] "{r["hook"]}"')
    return "\n".join(lines)


def _estimate_evidence_sources(sources: dict[str, Any]) -> int:
    """Count distinct input blocks that actually carry evidence."""
    n = 0
    if sources.get("editor_intro"):
        n += 1
    if sources.get("screenshots"):
        n += 1
    if sources.get("review_topics"):
        n += 1
    if sources.get("hooks"):
        n += 1
    return n


# ============================================================
# Generator
# ============================================================


class GameplayIntelGenerator:
    """Produces and persists structured gameplay intel for WeChat games."""

    def __init__(self, db_url: str, client: PoeClient | None = None):
        self.db_url = db_url
        self.client = client or PoeClient()

    async def generate_one(
        self, conn: psycopg.Connection, game_id: int
    ) -> dict[str, Any] | None:
        sources = _gather_game_sources(conn, game_id)
        if sources is None:
            logger.debug(f"[gameplay_intel] game {game_id} not found")
            return None

        evidence_count = _estimate_evidence_sources(sources)
        if evidence_count == 0:
            logger.info(
                f"[gameplay_intel] game {game_id} ({sources['name']}) "
                f"has zero evidence — skipping"
            )
            return None

        messages = build_gameplay_intel_messages(
            game_id=sources["game_id"],
            game_name=sources["name"],
            genre=sources["genre"],
            developer=sources["developer"],
            editor_intro_block=_fmt_editor_intro(sources["editor_intro"]),
            screenshots_block=_fmt_screenshots(sources["screenshots"]),
            review_topics_block=_fmt_review_topics(sources["review_topics"]),
            hook_phrases_block=_fmt_hooks(sources["hooks"]),
        )

        model = get_model_for_task("gameplay_intel")
        try:
            report = await self.client.chat_json(
                messages=messages,
                model=model,
                schema=GameplayIntelReport,
            )
        except Exception as exc:
            logger.error(
                f"[gameplay_intel] LLM failed for game {game_id}: {exc}"
            )
            return None

        screenshots = sources["screenshots"]
        safe_refs = [
            idx for idx in report.screenshot_refs
            if isinstance(idx, int) and 0 <= idx < len(screenshots)
        ]

        intel_blob: dict[str, Any] = {
            "gameplay_intro": report.gameplay_intro,
            "features": list(report.features),
            "art_style_primary": report.art_style_primary,
            "art_style_secondary": list(report.art_style_secondary),
            "art_style_evidence": list(report.art_style_evidence),
            "screenshot_refs": safe_refs,
            "confidence": float(report.confidence),
            "source_count": evidence_count,
            "prompt_version": PROMPT_VERSION,
            "model_used": model,
            "generated_at": _now_iso(),
        }

        conn.execute(
            """
            UPDATE games
               SET metadata = metadata || jsonb_build_object(
                   'gameplay_intel', %s::jsonb
               ),
                   updated_at = NOW()
             WHERE id = %s
            """,
            (json.dumps(intel_blob, ensure_ascii=False), game_id),
        )
        conn.commit()

        logger.info(
            f"[gameplay_intel] game {game_id} ({sources['name']}) "
            f"confidence={report.confidence:.2f} "
            f"features={len(report.features)} "
            f"art={report.art_style_primary or '-'} "
            f"sources={evidence_count}"
        )
        return intel_blob

    async def generate_batch(
        self, limit: int = 50, target_date: date | None = None
    ) -> dict[str, int]:
        """Run the generator over the top `limit` target games.

        Concurrency is capped via a semaphore — each target runs in its
        own transactional scope so a single failure does not block the
        batch.
        """
        with psycopg.connect(self.db_url) as conn:
            ids = _select_target_games(conn, limit=limit, target_date=target_date)

        if not ids:
            logger.warning("[gameplay_intel] no target games found, skipping")
            return {"target_count": 0, "success": 0, "failed": 0}

        logger.info(f"[gameplay_intel] processing {len(ids)} target games")

        sem = asyncio.Semaphore(DEFAULT_CONCURRENCY)

        async def _one(game_id: int) -> bool:
            async with sem:
                try:
                    with psycopg.connect(self.db_url) as c:
                        result = await self.generate_one(c, game_id)
                        return result is not None
                except Exception as exc:
                    logger.warning(
                        f"[gameplay_intel] game {game_id} failed: {exc}"
                    )
                    return False

        outcomes = await asyncio.gather(*(_one(g) for g in ids))
        success = sum(1 for o in outcomes if o)
        failed = len(outcomes) - success
        logger.info(
            f"[gameplay_intel] batch done: success={success} failed={failed}"
        )
        return {
            "target_count": len(ids),
            "success": success,
            "failed": failed,
        }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def run_gameplay_intel(
    db_url: str,
    limit: int = 50,
    target_date: date | None = None,
) -> dict[str, int]:
    """Sync entry point for workers / schedulers."""

    async def _run() -> dict[str, int]:
        gen = GameplayIntelGenerator(db_url)
        try:
            return await gen.generate_batch(limit=limit, target_date=target_date)
        finally:
            try:
                await gen.client.close()
            except Exception:
                pass

    return asyncio.run(_run())


__all__ = [
    "GameplayIntelGenerator",
    "run_gameplay_intel",
    "PROMPT_VERSION",
]
