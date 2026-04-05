"""Asset analysis processor — runs vision analysis on game screenshots."""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal

import psycopg
from pydantic import ValidationError

from src.llm.prompts.visual_analysis import (
    COLOR_SYSTEM_PROMPT,
    OCR_SYSTEM_PROMPT,
    SCENE_SYSTEM_PROMPT,
    UI_SYSTEM_PROMPT,
    ColorPalette,
    SceneDescription,
    TextOCR,
    UILayout,
)
from src.llm.vision_client import DEFAULT_VISION_MODEL, VisionClient

logger = logging.getLogger(__name__)

# Map analysis_type → (system_prompt, user_instruction, pydantic_model)
ANALYSIS_TYPES = {
    "scene_description": (
        SCENE_SYSTEM_PROMPT,
        "Describe this game screenshot.",
        SceneDescription,
    ),
    "color_palette": (
        COLOR_SYSTEM_PROMPT,
        "Extract color palette info.",
        ColorPalette,
    ),
    "ui_layout": (UI_SYSTEM_PROMPT, "Describe the UI layout.", UILayout),
    "text_ocr": (OCR_SYSTEM_PROMPT, "Extract all visible text.", TextOCR),
}


class AssetAnalyzer:
    def __init__(self, db_url: str, vision_client: VisionClient | None = None):
        self.db_url = db_url
        self.client = vision_client or VisionClient()

    async def analyze_screenshot(
        self,
        game_id: int,
        screenshot_url: str,
        analysis_types: list[str] | None = None,
    ) -> int:
        """Run all (or selected) analysis types on one screenshot. Returns count of analyses written."""
        types = analysis_types or list(ANALYSIS_TYPES.keys())
        written = 0

        with psycopg.connect(self.db_url) as conn:
            for atype in types:
                if atype not in ANALYSIS_TYPES:
                    continue

                # Skip if already analyzed recently (within 30 days)
                existing = conn.execute(
                    """
                    SELECT id FROM game_asset_analysis
                    WHERE game_id = %s AND asset_url = %s AND analysis_type = %s
                      AND analyzed_at > NOW() - INTERVAL '30 days'
                    LIMIT 1
                    """,
                    (game_id, screenshot_url, atype),
                ).fetchone()
                if existing:
                    continue

                system, user_instr, model_cls = ANALYSIS_TYPES[atype]
                try:
                    tokens_before_in = self.client.usage.input_tokens
                    tokens_before_out = self.client.usage.output_tokens
                    cost_before = self.client.usage.cost_usd

                    result = await self.client.analyze_image(
                        image_url=screenshot_url,
                        system_prompt=system,
                        user_prompt=user_instr,
                        response_format={"type": "json_object"},
                    )

                    # Validate via Pydantic
                    try:
                        parsed = model_cls.model_validate_json(result["content"])
                    except ValidationError as ve:
                        logger.warning(f"Validation failed for {atype}: {ve}")
                        continue

                    tokens_used = (
                        self.client.usage.input_tokens - tokens_before_in
                    ) + (self.client.usage.output_tokens - tokens_before_out)
                    cost_delta = self.client.usage.cost_usd - cost_before

                    confidence = getattr(parsed, "confidence", None)

                    conn.execute(
                        """
                        INSERT INTO game_asset_analysis
                        (game_id, asset_type, asset_url, analysis_type, result, model_used, confidence, tokens_used, cost_usd, analyzed_at)
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, NOW())
                        """,
                        (
                            game_id,
                            "screenshot",
                            screenshot_url,
                            atype,
                            parsed.model_dump_json(),
                            DEFAULT_VISION_MODEL,
                            confidence,
                            tokens_used,
                            Decimal(f"{cost_delta:.6f}"),
                        ),
                    )
                    conn.commit()
                    written += 1
                except Exception as e:
                    logger.warning(
                        f"Analysis {atype} failed for game {game_id}: {e}"
                    )

        return written

    async def process_pending_games(
        self, limit: int = 10, screenshots_per_game: int = 2
    ) -> int:
        """Find games with screenshots lacking analysis, process them."""
        total = 0
        with psycopg.connect(self.db_url) as conn:
            # Games with screenshots (in metadata.screenshots) and no asset_analysis rows yet
            rows = conn.execute(
                """
                SELECT id, metadata->'screenshots' AS screenshots
                FROM games
                WHERE metadata ? 'screenshots'
                  AND jsonb_typeof(metadata->'screenshots') = 'array'
                  AND jsonb_array_length(metadata->'screenshots') > 0
                  AND NOT EXISTS (
                    SELECT 1 FROM game_asset_analysis a
                    WHERE a.game_id = games.id
                      AND a.analyzed_at > NOW() - INTERVAL '30 days'
                  )
                ORDER BY id
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

        for game_id, screenshots_json in rows:
            urls = (
                screenshots_json[:screenshots_per_game]
                if isinstance(screenshots_json, list)
                else json.loads(screenshots_json or "[]")[:screenshots_per_game]
            )
            for url in urls:
                if not isinstance(url, str) or not url.startswith("http"):
                    continue
                written = await self.analyze_screenshot(game_id, url)
                total += written
                logger.info(
                    f"Game {game_id}: wrote {written} analyses for {url[:60]}"
                )

        return total


def run_asset_analysis(
    db_url: str, limit: int = 10, screenshots_per_game: int = 2
) -> int:
    async def _run():
        analyzer = AssetAnalyzer(db_url)
        try:
            count = await analyzer.process_pending_games(
                limit=limit, screenshots_per_game=screenshots_per_game
            )
            logger.info(
                f"Asset analysis complete: {count} records. "
                f"Total cost: ${analyzer.client.usage.cost_usd:.4f}"
            )
            return count
        finally:
            await analyzer.client.close()

    return asyncio.run(_run())


__all__ = [
    "AssetAnalyzer",
    "ANALYSIS_TYPES",
    "run_asset_analysis",
]
