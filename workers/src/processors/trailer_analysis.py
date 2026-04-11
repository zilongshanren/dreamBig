"""Trailer hook analysis processor (PRD §18 / ROADMAP P4-2).

End-to-end pipeline:
    1. Find a trailer URL for a game (social_content_samples -> games.metadata).
    2. Download it with yt-dlp, capped at the first 60 seconds.
    3. Extract representative frames with ffmpeg (1s, 2s, 3s, 10s, 20s, 30s, 45s).
    4. Base64-encode each JPG frame and send them all in a single GPT-4o-mini
       vision call via ``VisionClient.analyze_images``.
    5. Persist the parsed result into ``game_asset_analysis`` with
       ``asset_type='trailer'`` and ``analysis_type='trailer_hook'``.

Graceful degradation: if ``yt-dlp`` (the Python package) or the ``ffmpeg``
system binary is missing, we log a warning and return ``None`` — we do NOT
raise, so the scheduled worker job stays green.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from pydantic import ValidationError

from src.llm.prompts.trailer_analysis import (
    TRAILER_ANALYSIS_SYSTEM_PROMPT,
    TRAILER_ANALYSIS_USER_INSTRUCTION,
    TrailerHookAnalysis,
)
from src.llm.vision_client import DEFAULT_VISION_MODEL, VisionClient

logger = logging.getLogger(__name__)


# Frame extraction schedule (seconds into the trailer).
# First three cover the hook window; the tail samples the mid/late beats.
FRAME_TIMESTAMPS: tuple[float, ...] = (1.0, 2.0, 3.0, 10.0, 20.0, 30.0, 45.0)

# Cap downloads at this many seconds — trailers tend to blow their load early,
# and this keeps both disk and vision costs bounded.
MAX_DOWNLOAD_SECONDS = 60

# Scratch directory for downloads + frames. Created lazily.
TEMP_ROOT = Path(os.environ.get("DREAMBIG_TRAILER_TMP", "/tmp/dreambig_trailers"))


# --- Optional-dep probes ----------------------------------------------------


def _has_ffmpeg() -> bool:
    """Return True if the ``ffmpeg`` binary is on PATH."""
    return shutil.which("ffmpeg") is not None


def _import_yt_dlp() -> Any | None:
    """Import yt-dlp lazily. Returns the module or None if unavailable."""
    try:
        import yt_dlp  # type: ignore
    except Exception as e:  # pragma: no cover - env-dependent
        logger.warning(f"yt-dlp not available: {e}")
        return None
    return yt_dlp


# --- Trailer URL discovery --------------------------------------------------


_TRAILER_PLATFORMS = ("youtube", "bilibili")


def _find_trailer_url(conn: psycopg.Connection, game_id: int) -> str | None:
    """Locate the best trailer URL for a game.

    Priority:
      1. ``social_content_samples`` rows on YouTube/Bilibili where
         ``metadata->>'is_trailer' = 'true'``, most-viewed first.
      2. Highest-view YouTube/Bilibili video for the game overall.
      3. ``games.metadata->>'trailer_url'`` fallback.
    """
    row = conn.execute(
        """
        SELECT url
        FROM social_content_samples
        WHERE game_id = %s
          AND platform = ANY(%s)
          AND url IS NOT NULL
          AND COALESCE(metadata->>'is_trailer', 'false') = 'true'
        ORDER BY view_count DESC NULLS LAST
        LIMIT 1
        """,
        (game_id, list(_TRAILER_PLATFORMS)),
    ).fetchone()
    if row and row[0]:
        return row[0]

    row = conn.execute(
        """
        SELECT url
        FROM social_content_samples
        WHERE game_id = %s
          AND platform = ANY(%s)
          AND url IS NOT NULL
        ORDER BY view_count DESC NULLS LAST
        LIMIT 1
        """,
        (game_id, list(_TRAILER_PLATFORMS)),
    ).fetchone()
    if row and row[0]:
        return row[0]

    row = conn.execute(
        """
        SELECT metadata->>'trailer_url'
        FROM games
        WHERE id = %s
        """,
        (game_id,),
    ).fetchone()
    if row and row[0]:
        return row[0]

    return None


# --- Download + frame extraction --------------------------------------------


def _download_trailer(yt_dlp_mod: Any, url: str, out_dir: Path) -> Path | None:
    """Download a trailer via yt-dlp, capped to the first N seconds.

    Returns the path to the downloaded media file, or None on failure.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(out_dir / "trailer.%(ext)s")

    ydl_opts: dict[str, Any] = {
        "outtmpl": out_template,
        "format": "best[height<=720]/best",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # Ask yt-dlp to only pull the first MAX_DOWNLOAD_SECONDS seconds.
        # Requires ffmpeg, which we already probed for above.
        "download_ranges": yt_dlp_mod.utils.download_range_func(
            None, [(0, MAX_DOWNLOAD_SECONDS)]
        ),
        "force_keyframes_at_cuts": True,
        # Don't bail out on individual stream errors.
        "ignoreerrors": True,
    }

    try:
        with yt_dlp_mod.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None
            filename = ydl.prepare_filename(info)
    except Exception as e:
        logger.warning(f"yt-dlp failed for {url}: {e}")
        return None

    path = Path(filename)
    if not path.exists():
        # yt-dlp may have rewritten the extension post-processing; pick any file.
        candidates = sorted(out_dir.glob("trailer.*"))
        if not candidates:
            return None
        path = candidates[0]
    return path


def _extract_frames(video_path: Path, out_dir: Path) -> list[Path]:
    """Extract frames at FRAME_TIMESTAMPS via ffmpeg. Returns list of JPG paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for idx, ts in enumerate(FRAME_TIMESTAMPS):
        frame_path = out_dir / f"frame_{idx:02d}_{int(ts * 1000):06d}ms.jpg"
        # -ss before -i is fast seek; scale down to 512px wide for cheap 'low' detail vision.
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{ts:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=512:-2",
            "-q:v",
            "5",
            str(frame_path),
        ]
        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"ffmpeg timed out extracting frame at {ts}s")
            continue
        except FileNotFoundError:
            logger.warning("ffmpeg binary disappeared mid-run")
            return frames

        if result.returncode != 0:
            logger.debug(
                f"ffmpeg frame@{ts}s rc={result.returncode} "
                f"stderr={result.stderr.decode('utf-8', 'replace')[:200]}"
            )
            continue
        if frame_path.exists() and frame_path.stat().st_size > 0:
            frames.append(frame_path)
    return frames


def _encode_frame_as_data_url(path: Path) -> str:
    """Base64-encode a JPG file into a ``data:image/jpeg;base64,...`` URL."""
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


# --- Main processor ---------------------------------------------------------


class TrailerAnalyzer:
    """Analyze game trailers for hook/pacing/visual cues with GPT-4o-mini vision."""

    def __init__(self, db_url: str, vision_client: VisionClient | None = None):
        self.db_url = db_url
        self.client = vision_client or VisionClient()

    # -- public API ----------------------------------------------------------

    def analyze_for_game(self, game_id: int) -> dict | None:
        """Full pipeline for a single game. Returns the parsed result dict or None.

        Returns None (without raising) for any of:
          - missing yt-dlp or ffmpeg
          - no trailer URL discoverable
          - download / extract / vision failure
          - LLM output validation failure
        """
        yt_dlp_mod = _import_yt_dlp()
        if yt_dlp_mod is None:
            logger.warning("Skipping trailer analysis: yt-dlp not installed")
            return None
        if not _has_ffmpeg():
            logger.warning("Skipping trailer analysis: ffmpeg binary not on PATH")
            return None

        with psycopg.connect(self.db_url) as conn:
            trailer_url = _find_trailer_url(conn, game_id)
            if not trailer_url:
                logger.info(f"No trailer URL found for game {game_id}")
                return None

            # Skip if we already have a recent trailer_hook analysis for this URL.
            existing = conn.execute(
                """
                SELECT id FROM game_asset_analysis
                WHERE game_id = %s
                  AND asset_type = 'trailer'
                  AND analysis_type = 'trailer_hook'
                  AND asset_url = %s
                  AND analyzed_at > NOW() - INTERVAL '30 days'
                LIMIT 1
                """,
                (game_id, trailer_url),
            ).fetchone()
            if existing:
                logger.info(
                    f"Game {game_id}: trailer already analyzed recently, skipping"
                )
                return None

        work_dir = Path(tempfile.mkdtemp(prefix=f"game{game_id}_", dir=str(TEMP_ROOT)))
        try:
            video_path = _download_trailer(yt_dlp_mod, trailer_url, work_dir)
            if not video_path or not video_path.exists():
                logger.warning(
                    f"Game {game_id}: trailer download failed for {trailer_url}"
                )
                return None

            frames_dir = work_dir / "frames"
            frames = _extract_frames(video_path, frames_dir)
            if len(frames) < 2:
                logger.warning(
                    f"Game {game_id}: too few frames extracted ({len(frames)}) — aborting"
                )
                return None

            data_urls = [_encode_frame_as_data_url(p) for p in frames]

            parsed = asyncio.run(self._run_vision(data_urls))
            if parsed is None:
                return None

            tokens_used = (
                self.client.usage.input_tokens + self.client.usage.output_tokens
            )
            cost_delta = self.client.usage.cost_usd

            result_dict = parsed.model_dump()
            self._persist_result(
                game_id=game_id,
                trailer_url=trailer_url,
                result_json=parsed.model_dump_json(),
                confidence=float(parsed.confidence),
                tokens_used=tokens_used,
                cost_usd=cost_delta,
            )
            logger.info(
                f"Game {game_id}: trailer analysis stored "
                f"(hook='{parsed.hook_in_first_3s[:50]}...', frames={len(frames)})"
            )
            return result_dict
        finally:
            # Always clean up scratch files, even on failure.
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"cleanup failed for {work_dir}: {e}")

    # -- helpers -------------------------------------------------------------

    async def _run_vision(
        self, image_data_urls: list[str]
    ) -> TrailerHookAnalysis | None:
        """Send the frames to GPT-4o-mini and validate the JSON response."""
        try:
            resp = await self.client.analyze_images(
                image_urls=image_data_urls,
                system_prompt=TRAILER_ANALYSIS_SYSTEM_PROMPT,
                user_prompt=TRAILER_ANALYSIS_USER_INSTRUCTION,
                response_format={"type": "json_object"},
                max_tokens=700,
            )
        except Exception as e:
            logger.warning(f"Vision call failed for trailer: {e}")
            return None

        content = resp.get("content") or ""
        if not content.strip():
            logger.warning("Vision call returned empty content")
            return None

        try:
            return TrailerHookAnalysis.model_validate_json(content)
        except ValidationError as ve:
            logger.warning(f"TrailerHookAnalysis validation failed: {ve}")
            logger.debug(f"Raw content: {content[:500]}")
            return None
        except json.JSONDecodeError as je:
            logger.warning(f"Trailer analysis returned non-JSON: {je}")
            return None

    def _persist_result(
        self,
        *,
        game_id: int,
        trailer_url: str,
        result_json: str,
        confidence: float,
        tokens_used: int,
        cost_usd: float,
    ) -> None:
        with psycopg.connect(self.db_url) as conn:
            conn.execute(
                """
                INSERT INTO game_asset_analysis
                    (game_id, asset_type, asset_url, analysis_type, result,
                     model_used, confidence, tokens_used, cost_usd, analyzed_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, NOW())
                """,
                (
                    game_id,
                    "trailer",
                    trailer_url,
                    "trailer_hook",
                    result_json,
                    DEFAULT_VISION_MODEL,
                    confidence,
                    tokens_used,
                    Decimal(f"{cost_usd:.6f}"),
                ),
            )
            conn.commit()


# --- Top-level batch runner -------------------------------------------------


def _pick_candidate_games(conn: psycopg.Connection, limit: int) -> list[int]:
    """Return up to ``limit`` game_ids with overall_score>=60 and no trailer_hook yet."""
    rows = conn.execute(
        """
        SELECT g.id
        FROM games g
        JOIN potential_scores ps
             ON ps.game_id = g.id AND ps.scored_at = CURRENT_DATE
        WHERE ps.overall_score >= 60
          AND NOT EXISTS (
              SELECT 1 FROM game_asset_analysis a
              WHERE a.game_id = g.id
                AND a.asset_type = 'trailer'
                AND a.analysis_type = 'trailer_hook'
                AND a.analyzed_at > NOW() - INTERVAL '30 days'
          )
        ORDER BY ps.overall_score DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [r[0] for r in rows]


def run_trailer_analysis(db_url: str, limit: int = 10) -> int:
    """Run trailer hook analysis for the top-N high-potential games.

    Picks games with today's ``potential_scores.overall_score >= 60`` that
    do not yet have a ``trailer_hook`` analysis, and processes them one by
    one. Returns the number of analyses successfully written.
    """
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(db_url) as conn:
        game_ids = _pick_candidate_games(conn, limit)

    if not game_ids:
        logger.info("No candidate games for trailer analysis")
        return 0

    logger.info(f"Trailer analysis batch: {len(game_ids)} candidate games")
    analyzer = TrailerAnalyzer(db_url)
    written = 0
    try:
        for game_id in game_ids:
            try:
                result = analyzer.analyze_for_game(game_id)
                if result is not None:
                    written += 1
            except Exception as e:
                logger.warning(
                    f"Trailer analysis failed for game {game_id}: {e}",
                    exc_info=True,
                )
    finally:
        try:
            asyncio.run(analyzer.client.close())
        except RuntimeError:
            # Event loop already closed — nothing to do.
            pass

    logger.info(
        f"Trailer analysis complete: {written}/{len(game_ids)} games. "
        f"Total vision cost: ${analyzer.client.usage.cost_usd:.4f}"
    )
    return written


__all__ = [
    "TrailerAnalyzer",
    "run_trailer_analysis",
    "FRAME_TIMESTAMPS",
    "MAX_DOWNLOAD_SECONDS",
]
