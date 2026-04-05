"""Visual analysis prompts and schemas for game screenshot understanding.

Four distinct analysis types — each with its own system prompt and Pydantic
schema. They are designed to be run independently against GPT-4o-mini's
vision capability, one image at a time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --- Scene description (main analysis) ---
class SceneDescription(BaseModel):
    scene_type: Literal[
        "menu",
        "gameplay",
        "cutscene",
        "store",
        "map",
        "dialog",
        "loading",
        "other",
    ]
    description: str  # 1-2 sentences Chinese
    visible_ui_elements: list[str] = Field(default_factory=list, max_length=10)
    main_characters: list[str] = Field(default_factory=list, max_length=5)
    art_style_tags: list[str] = Field(
        default_factory=list, max_length=5
    )  # e.g. "2d_pixel","3d_cel_shaded","cartoon"
    confidence: float = Field(ge=0, le=1)


SCENE_SYSTEM_PROMPT = """你是游戏视觉分析师。给定一张游戏截图，描述：
- 场景类型 (菜单/游戏中/过场动画/商店/地图/对话/加载/其他)
- 场景内容（1-2 句中文）
- 可见 UI 元素（按钮、HUD、图标、进度条等，最多 10 项）
- 主要角色/对象（如有）
- 美术风格标签（2D/3D/像素/卡通/写实/赛博朋克/日系等，最多 5 项）

规则：
- 只返回 JSON
- 不要猜测未显现的内容
- confidence 反映场景的清晰程度
"""


# --- Color palette ---
class ColorPalette(BaseModel):
    dominant_colors: list[str] = Field(
        default_factory=list, max_length=5, description="Hex codes like #FF5733"
    )
    mood: Literal["warm", "cool", "neutral", "vibrant", "muted", "dark", "bright"]
    contrast: Literal["high", "medium", "low"]
    ui_theme: str  # Chinese description


COLOR_SYSTEM_PROMPT = """你是视觉设计师。分析游戏截图的色彩风格：
- 提取 3-5 种主色调 (hex)
- 判断整体情绪 (暖/冷/中性/鲜艳/低饱和/暗色/明亮)
- 判断对比度
- 用 1 句中文描述 UI 主题

只返回 JSON。
"""


# --- UI layout ---
class UILayout(BaseModel):
    layout_type: Literal["portrait", "landscape", "square"]
    hud_density: Literal["minimal", "medium", "dense"]
    primary_cta_location: str  # Chinese description
    navigation_pattern: str  # Chinese description
    accessibility_notes: list[str] = Field(default_factory=list, max_length=3)


UI_SYSTEM_PROMPT = """你是游戏 UX 分析师。分析截图的 UI 布局：
- 画面方向 (竖屏/横屏/方形)
- HUD 信息密度 (简约/中等/密集)
- 主要行动按钮位置 (用中文描述)
- 导航模式 (如底部 tabs、顶部菜单、抽屉、手势等)
- 易用性观察 (最多 3 条)

只返回 JSON。
"""


# --- OCR / text extraction ---
class TextOCR(BaseModel):
    visible_text: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="All visible text in original language",
    )
    translated_text: list[str] = Field(
        default_factory=list, max_length=20, description="Chinese translation per item"
    )
    primary_language: str  # ISO-639-1 like "en"/"zh"/"ja"
    has_cta_text: bool


OCR_SYSTEM_PROMPT = """你是 OCR 专家。提取截图中所有可见文字：
- 按出现顺序列出原文 (最多 20 条)
- 如果不是中文，给出对应中文翻译
- 判断主要语言
- 判断是否有行动号召文字 (如"立即下载"/"Start"/"Play Now")

只返回 JSON。
"""


def build_vision_messages(system: str, user_instructions: str):
    """Helper — just bundles system + user text; caller adds image."""
    return system, user_instructions


__all__ = [
    "SceneDescription",
    "ColorPalette",
    "UILayout",
    "TextOCR",
    "SCENE_SYSTEM_PROMPT",
    "COLOR_SYSTEM_PROMPT",
    "UI_SYSTEM_PROMPT",
    "OCR_SYSTEM_PROMPT",
    "build_vision_messages",
]
