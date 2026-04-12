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
from datetime import datetime, timedelta, timezone
from datetime import date
from typing import Any

import httpx
import psycopg

from src.llm import PoeClient, get_model_for_task
from src.llm.prompts.gameplay_intel import (
    GAMEPLAY_INTEL_PROMPT,
    GameplayIntelReport,
    build_gameplay_intel_messages,
)
from src.scrapers.gameplay_web import (
    WebSource,
    fetch_page_content,
    search_bing_for_game,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = GAMEPLAY_INTEL_PROMPT.version  # "v1"
DEFAULT_CONCURRENCY = 4
WEB_CACHE_TTL = timedelta(days=7)
WEB_SOURCES_PER_GAME = 8
WEB_BODY_MAX_CHARS = 800


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
    evidence is thin. Web sources (game_web_sources table) are read
    from cache here; the fresh-fetch step happens in ``_refresh_web_sources``
    before calling this function.
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

    web_rows = conn.execute(
        """
        SELECT source_site, url, title, snippet, content_text
        FROM game_web_sources
        WHERE game_id = %s
          AND content_text IS NOT NULL
          AND LENGTH(content_text) > 0
        ORDER BY fetched_at DESC
        LIMIT %s
        """,
        (game_id, WEB_SOURCES_PER_GAME),
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
        "web_sources": [
            {
                "source_site": r[0],
                "url": r[1],
                "title": r[2] or "",
                "snippet": r[3] or "",
                "content": r[4] or "",
            }
            for r in web_rows
        ],
    }


def _refresh_web_sources(
    conn: psycopg.Connection,
    game_id: int,
    game_name: str,
) -> int:
    """Run Bing search → page fetch → upsert into game_web_sources.

    Skips pages that are already cached within ``WEB_CACHE_TTL``. Returns
    the number of rows written (including refreshed ones).
    """
    # Which urls are still fresh in cache? Don't burn a network request.
    cached_urls_row = conn.execute(
        """
        SELECT url FROM game_web_sources
        WHERE game_id = %s AND ttl_expires_at > NOW()
        """,
        (game_id,),
    ).fetchall()
    cached_urls = {r[0] for r in cached_urls_row}

    try:
        hits = search_bing_for_game(game_name)
    except Exception as exc:
        logger.warning(
            f"[gameplay_intel] Bing search failed for game {game_id} "
            f"({game_name}): {exc}"
        )
        return 0

    if not hits:
        logger.info(
            f"[gameplay_intel] Bing returned 0 hits for {game_name}"
        )
        return 0

    fresh_hits = [h for h in hits if h["url"] not in cached_urls]
    if not fresh_hits:
        logger.info(
            f"[gameplay_intel] game {game_id} ({game_name}): "
            f"all {len(hits)} hits already cached"
        )
        return 0

    # Fetch up to WEB_SOURCES_PER_GAME fresh pages via a shared client.
    written = 0
    ttl_cutoff = datetime.now(timezone.utc) + WEB_CACHE_TTL
    with httpx.Client(
        trust_env=False, timeout=15, follow_redirects=True
    ) as client:
        for h in fresh_hits[: WEB_SOURCES_PER_GAME * 2]:
            if written >= WEB_SOURCES_PER_GAME:
                break
            try:
                result = fetch_page_content(
                    h["url"],
                    game_name,
                    client=client,
                    max_chars=WEB_BODY_MAX_CHARS,
                )
            except Exception as exc:
                logger.debug(
                    f"[gameplay_intel] fetch failed for "
                    f"{h['url'][:80]}: {exc}"
                )
                result = None

            content = (result or {}).get("body") or ""
            # Even pages that failed relevance still give us title+snippet
            # from Bing — we persist them with empty content_text so the
            # query doesn't re-issue them within the TTL window.
            conn.execute(
                """
                INSERT INTO game_web_sources (
                    game_id, source_site, url, title, snippet,
                    content_text, query, http_status,
                    fetched_at, ttl_expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s
                )
                ON CONFLICT (game_id, source_site, url) DO UPDATE SET
                    title = EXCLUDED.title,
                    snippet = EXCLUDED.snippet,
                    content_text = EXCLUDED.content_text,
                    query = EXCLUDED.query,
                    http_status = EXCLUDED.http_status,
                    fetched_at = NOW(),
                    ttl_expires_at = EXCLUDED.ttl_expires_at
                """,
                (
                    game_id,
                    h["source_site"],
                    h["url"],
                    h["title"][:500],
                    (h["snippet"] or "")[:800],
                    content,
                    h.get("query"),
                    h.get("http_status"),
                    ttl_cutoff,
                ),
            )
            if content:
                written += 1
    conn.commit()
    logger.info(
        f"[gameplay_intel] game {game_id} ({game_name}): "
        f"fetched {written} new web sources "
        f"({len(cached_urls)} previously cached)"
    )
    return written


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


def _fmt_web_sources(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "（未从公开 Web 抓到任何可读正文 — Bing 搜索无结果 / "
            "页面 403 / 内容过短。你在 rationale 里不得引用"
            "'媒体报道/玩家社区'作证据）"
        )
    lines = []
    for i, r in enumerate(rows):
        snippet = (r.get("snippet") or "").strip()
        content = (r.get("content") or "").strip()
        # Use content when available; otherwise snippet
        body = content if content else snippet
        if len(body) > 500:
            body = body[:500] + "…"
        title = (r.get("title") or "").strip()
        site = r.get("source_site") or "web"
        lines.append(
            f"--- [{i}] {site} | {title[:60]}\n"
            f"    url: {r.get('url', '')[:120]}\n"
            f"    body: {body}"
        )
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
    # Count web sources as one bucket regardless of count.
    web = sources.get("web_sources") or []
    if any((w.get("content") or "").strip() for w in web):
        n += 1
    return n


def _source_breakdown(sources: dict[str, Any]) -> str:
    """Compact per-field diagnostic string for logs."""
    desc = sources.get("editor_intro") or ""
    shots = sources.get("screenshots") or []
    topics = sources.get("review_topics") or []
    hooks = sources.get("hooks") or []
    web = sources.get("web_sources") or []
    web_with_body = sum(1 for w in web if (w.get("content") or "").strip())
    return (
        f"desc_len={len(desc)} shots={len(shots)} "
        f"topics={len(topics)} hooks={len(hooks)} "
        f"web={web_with_body}/{len(web)}"
    )


def _build_stub_intel(sources: dict[str, Any]) -> dict[str, Any]:
    """Synthesize a low-confidence 'no data' record without calling the LLM.

    Used when a game has zero input evidence — we still want the row to
    exist so the dashboard can show a "waiting for fetch_details" state
    instead of silently dropping the game.
    """
    missing: list[str] = []
    if not sources.get("editor_intro"):
        missing.append("应用宝 editor_intro 未抓到或为空")
    if not sources.get("screenshots"):
        missing.append("截图列表为空（fetch_details 未写入 metadata.screenshots）")
    if not sources.get("review_topics"):
        missing.append("玩家评论话题聚类未生成（topic_clustering 尚未产出）")
    if not sources.get("hooks"):
        missing.append("社媒 hook 短句未抽取（hook_extraction 尚未产出）")
    web = sources.get("web_sources") or []
    if not any((w.get("content") or "").strip() for w in web):
        missing.append(
            "公开 Web 抓取零正文（Bing 0 结果 / 页面 403 / 内容过短）"
        )

    return {
        "gameplay_intro": (
            "暂无足够公开资料可供分析——应用宝 editor_intro、截图、"
            "玩家评论话题、社媒 hook 和公开 Web 媒体正文均未进入数据管道。"
            "请先运行 run_fetch_details + gameplay_intel 一套完整流程"
            "（会顺带抓 Bing 公开资料），本记录会在下一次调度时自动刷新。"
        ),
        "features": [],
        "art_style_primary": None,
        "art_style_secondary": [],
        "art_style_evidence": [],
        "screenshot_refs": [],
        "confidence": 0.1,
        "source_count": 0,
        "prompt_version": PROMPT_VERSION,
        "model_used": "stub-no-llm",
        "generated_at": _now_iso(),
        "data_blind_spots": missing,
    }


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
        # Step 1: look up the game so we know its display name.
        name_row = conn.execute(
            "SELECT COALESCE(name_zh, name_en, '') FROM games WHERE id = %s",
            (game_id,),
        ).fetchone()
        if not name_row or not name_row[0]:
            logger.debug(f"[gameplay_intel] game {game_id} not found")
            return None
        game_name = name_row[0]

        # Step 2: refresh the Bing-backed web sources cache for this game.
        # Cached hits within 7 days are reused as-is.
        try:
            _refresh_web_sources(conn, game_id, game_name)
        except Exception as exc:
            logger.warning(
                f"[gameplay_intel] web refresh failed for game {game_id} "
                f"({game_name}): {exc} — continuing with existing cache"
            )

        # Step 3: gather ALL input blocks (now including fresh web rows).
        sources = _gather_game_sources(conn, game_id)
        if sources is None:
            logger.debug(f"[gameplay_intel] game {game_id} vanished mid-run")
            return None

        breakdown = _source_breakdown(sources)
        evidence_count = _estimate_evidence_sources(sources)

        # Zero-evidence path: write a stub record without calling the LLM.
        # Don't silently skip — the dashboard should show "waiting for data"
        # instead of rendering a confusing empty state.
        if evidence_count == 0:
            logger.info(
                f"[gameplay_intel] game {game_id} ({sources['name']}) "
                f"zero evidence ({breakdown}) — writing stub record"
            )
            stub_blob = _build_stub_intel(sources)
            conn.execute(
                """
                UPDATE games
                   SET metadata = metadata || jsonb_build_object(
                       'gameplay_intel', %s::jsonb
                   ),
                       updated_at = NOW()
                 WHERE id = %s
                """,
                (json.dumps(stub_blob, ensure_ascii=False), game_id),
            )
            conn.commit()
            return stub_blob

        logger.info(
            f"[gameplay_intel] game {game_id} ({sources['name']}) "
            f"gathering ({breakdown}) — calling LLM"
        )

        messages = build_gameplay_intel_messages(
            game_id=sources["game_id"],
            game_name=sources["name"],
            genre=sources["genre"],
            developer=sources["developer"],
            editor_intro_block=_fmt_editor_intro(sources["editor_intro"]),
            screenshots_block=_fmt_screenshots(sources["screenshots"]),
            review_topics_block=_fmt_review_topics(sources["review_topics"]),
            hook_phrases_block=_fmt_hooks(sources["hooks"]),
            web_sources_block=_fmt_web_sources(sources.get("web_sources") or []),
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
            # Align shape with stub path so the frontend never has to
            # branch on "missing field" vs "empty list".
            "data_blind_spots": [],
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
