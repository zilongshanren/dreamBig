"""Language detection utility for multi-language review content.

Primary strategy is a character-range heuristic that handles the
CJK scripts (Chinese, Japanese, Korean) without any dependency —
these are the ones most commonly mis-detected by statistical
libraries that train on Latin-alphabet text. For alphabetic
scripts, we fall through to langdetect if available, otherwise
assume English when the sample is ASCII-only.

Use :func:`detect_language` after building each ReviewEntry so
the `language` field is consistent across platforms regardless
of what metadata the upstream API happens to provide.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Simple character-range heuristic used as primary detector (no dep needed for
# the common case). Falls through to langdetect for ambiguous text.
_CJK_RANGES = [
    (0x4E00, 0x9FFF),   # CJK Unified
    (0x3400, 0x4DBF),   # CJK Extension A
]
_HIRAGANA = (0x3040, 0x309F)
_KATAKANA = (0x30A0, 0x30FF)
_HANGUL_SYLLABLES = (0xAC00, 0xD7A3)
_HANGUL_JAMO = (0x1100, 0x11FF)


def _count_range(text: str, lo: int, hi: int) -> int:
    return sum(1 for ch in text if lo <= ord(ch) <= hi)


def detect_language(text: str) -> str | None:
    """Return ISO-639-1-ish language code, or None if undetermined.

    Returns one of: "zh", "ja", "ko", "en", "ru", "ar", or None.
    """
    if not text or not text.strip():
        return None
    sample = text[:500]
    total = len(sample.strip())
    if total == 0:
        return None

    hiragana = _count_range(sample, *_HIRAGANA)
    katakana = _count_range(sample, *_KATAKANA)
    if (hiragana + katakana) / max(total, 1) > 0.10:
        return "ja"

    hangul = _count_range(sample, *_HANGUL_SYLLABLES) + _count_range(sample, *_HANGUL_JAMO)
    if hangul / max(total, 1) > 0.10:
        return "ko"

    cjk_total = 0
    for lo, hi in _CJK_RANGES:
        cjk_total += _count_range(sample, lo, hi)
    if cjk_total / max(total, 1) > 0.15:
        return "zh"

    # Fall through to langdetect for alphabetic scripts
    try:
        from langdetect import DetectorFactory, detect
        DetectorFactory.seed = 0  # deterministic
        code = detect(sample)
        # Normalize a few codes
        if code.startswith("zh"):
            return "zh"
        return code[:2]
    except Exception:
        # ASCII letters only -> assume English
        if re.match(r"^[\x00-\x7F]+$", sample):
            return "en"
        return None


def normalize_lang_code(code: str | None) -> str | None:
    """Normalize various language code variants to our standard set."""
    if not code:
        return None
    code = code.lower().strip().replace("_", "-")
    if code in ("zh", "zh-cn", "zh-hans", "chi-sim", "schinese"):
        return "zh"
    if code in ("zh-tw", "zh-hk", "zh-hant", "tchinese"):
        return "zh-tw"
    if code in ("en", "en-us", "en-gb", "english"):
        return "en"
    if code in ("ja", "jp", "jpn", "japanese"):
        return "ja"
    if code in ("ko", "kor", "korean"):
        return "ko"
    return code[:5]
