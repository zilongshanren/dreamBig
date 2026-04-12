"""Baidu Baike fallback scraper for missing game descriptions/screenshots.

This is intentionally lightweight:
- Fetch the canonical ``/item/<game_name>`` page directly.
- Parse the server-rendered ``window.PAGE_DATA`` JSON blob when present.
- Prefer gameplay-related paragraphs/albums when the page exposes them.
- Fall back to meta description / og:image so we still return something
  useful when the structured blob is sparse.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_PAGE_DATA_RE = re.compile(
    r"window\.PAGE_DATA\s*=\s*(\{.*?\})\s*</script>",
    re.S,
)
_BAIKE_ITEM_BASE = "https://baike.baidu.com/item/"

_GAME_HINTS = (
    "游戏",
    "手游",
    "端游",
    "网游",
    "微信小游戏",
    "小程序游戏",
    "电竞",
    "卡牌",
    "RPG",
)
_NON_GAME_HINTS = (
    "歌曲",
    "专辑",
    "电影",
    "电视剧",
    "人物",
    "演员",
    "小说",
    "诗歌",
    "品牌",
)
_GAMEPLAY_SECTION_HINTS = (
    "玩法",
    "操作",
    "规则",
    "模式",
    "系统",
    "关卡",
    "战斗",
    "地图",
    "道具",
    "物品",
    "技能",
)
_SCREENSHOT_ALBUM_HINTS = (
    "玩法",
    "游戏",
    "截图",
    "界面",
    "场景",
    "地图",
    "战斗",
    "关卡",
    "模式",
    "系统",
    "道具",
    "物品",
    "词条图片",
    "概述",
)
_LOW_PRIORITY_ALBUM_HINTS = (
    "角色",
    "人物",
    "皮肤",
    "时装",
)


@dataclass
class BaikeGameDetails:
    url: str
    title: str
    description: str
    screenshots: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _build_item_url(game_name: str, lemma_id: int | str | None = None) -> str:
    encoded = quote(game_name, safe="")
    if lemma_id:
        return f"{_BAIKE_ITEM_BASE}{encoded}/{lemma_id}"
    return f"{_BAIKE_ITEM_BASE}{encoded}"


def _extract_page_data(html: str) -> dict[str, Any] | None:
    m = _PAGE_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        logger.debug(f"[baidu_baike] PAGE_DATA JSON decode failed: {exc}")
        return None


def _is_game_like_text(text: str) -> bool:
    text = _clean_text(text)
    if not text:
        return False
    has_game_hint = any(k in text for k in _GAME_HINTS)
    has_non_game_hint = any(k in text for k in _NON_GAME_HINTS)
    return has_game_hint and not has_non_game_hint


def _pick_game_navigation_candidate(
    page_data: dict[str, Any], game_name: str
) -> dict[str, Any] | None:
    navigation = page_data.get("navigation") or {}
    lemmas = navigation.get("lemmas") or []
    best: dict[str, Any] | None = None
    best_score = -1

    for lemma in lemmas:
        title = _clean_text(lemma.get("lemmaTitle") or "")
        if not title or game_name not in title:
            continue

        desc = _clean_text(lemma.get("lemmaDesc") or "")
        classify = " ".join(lemma.get("classify") or [])
        combined = f"{title} {desc} {classify}"

        score = 0
        if title == game_name:
            score += 3
        if _is_game_like_text(combined):
            score += 5
        if lemma.get("isCurrent"):
            score += 1
        if lemma.get("isDefault"):
            score += 1

        if any(k in combined for k in _NON_GAME_HINTS):
            score -= 4

        if score > best_score:
            best = lemma
            best_score = score

    return best if best_score >= 4 else None


def _extract_meta(soup: BeautifulSoup, key: str) -> str:
    node = soup.find("meta", attrs={"property": key}) or soup.find(
        "meta", attrs={"name": key}
    )
    return _clean_text(node.get("content", "")) if node else ""


def _normalize_baike_image_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"https://bkimg.cdn.bcebos.com/pic/{value}"


def _image_dedupe_key(value: str) -> str:
    return _normalize_baike_image_url(value).split("?", 1)[0]


def _extract_gameplay_paragraphs(page_data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    knowledge = ((page_data.get("modules") or {}).get("knowledge") or {}).get(
        "data"
    ) or []
    for block in knowledge:
        for item in block.get("data") or []:
            item_data = item.get("data") or {}
            titles = [
                _clean_text(x.get("title") or "") for x in item_data.get("catalog") or []
            ]
            title_text = " ".join(titles)
            paragraphs = [
                _clean_text(x.get("text") or "")
                for x in item_data.get("content") or []
                if x.get("type") == "paragraph" and x.get("text")
            ]
            if not paragraphs:
                continue
            if not any(h in title_text for h in _GAMEPLAY_SECTION_HINTS):
                continue
            for para in paragraphs:
                if para and para not in seen:
                    out.append(para)
                    seen.add(para)
    return out


def _extract_description(page_data: dict[str, Any], meta_description: str) -> str:
    gameplay_paragraphs = _extract_gameplay_paragraphs(page_data)
    if gameplay_paragraphs:
        return _clean_text(" ".join(gameplay_paragraphs[:2]))[:500]

    for candidate in (
        page_data.get("description"),
        meta_description,
        page_data.get("lemmaDesc"),
    ):
        text = _clean_text(candidate or "")
        if text:
            return text[:500]
    return ""


def _album_priority(album: dict[str, Any]) -> int:
    desc = _clean_text(album.get("desc") or "")
    score = 0
    if any(k in desc for k in _SCREENSHOT_ALBUM_HINTS):
        score += 3
    if any(k in desc for k in _LOW_PRIORITY_ALBUM_HINTS):
        score -= 1
    return score


def _append_image(out: list[str], seen: set[str], raw: str, max_items: int) -> None:
    url = _normalize_baike_image_url(raw)
    dedupe_key = _image_dedupe_key(raw)
    if not url or dedupe_key in seen:
        return
    out.append(url)
    seen.add(dedupe_key)
    if len(out) > max_items:
        del out[max_items:]


def _extract_screenshots(
    page_data: dict[str, Any],
    meta_image: str,
    *,
    max_items: int = 5,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    abstract_album = page_data.get("abstractAlbum") or {}
    cover = abstract_album.get("coverPic") or {}
    _append_image(out, seen, cover.get("url") or cover.get("src"), max_items)

    albums = page_data.get("albums") or []
    for album in sorted(albums, key=_album_priority, reverse=True):
        cover_pic = album.get("coverPic") or {}
        _append_image(out, seen, cover_pic.get("url") or cover_pic.get("src"), max_items)
        for item in album.get("content") or []:
            _append_image(
                out,
                seen,
                item.get("url") or item.get("origSrc") or item.get("src"),
                max_items,
            )
            if len(out) >= max_items:
                return out[:max_items]

    _append_image(out, seen, meta_image, max_items)
    return out[:max_items]


def _looks_like_target_game(
    *,
    game_name: str,
    title: str,
    lemma_title: str,
    lemma_desc: str,
    description: str,
) -> bool:
    haystack = " ".join(
        x for x in (title, lemma_title, lemma_desc, description) if x
    )
    if game_name not in haystack:
        return False
    return _is_game_like_text(" ".join((lemma_desc, description, title)))


def _parse_baike_html(
    html: str,
    *,
    game_name: str,
    final_url: str,
    max_screenshots: int = 5,
) -> tuple[BaikeGameDetails | None, dict[str, Any] | None]:
    soup = BeautifulSoup(html, "lxml")
    page_data = _extract_page_data(html)

    title_node = soup.find("title")
    title = _clean_text(title_node.get_text(" ", strip=True)) if title_node else ""
    meta_description = _extract_meta(soup, "og:description") or _extract_meta(
        soup, "description"
    )
    meta_image = _extract_meta(soup, "og:image") or _extract_meta(soup, "image")

    lemma_title = _clean_text((page_data or {}).get("lemmaTitle") or "")
    lemma_desc = _clean_text((page_data or {}).get("lemmaDesc") or "")
    description = _extract_description(page_data or {}, meta_description)

    if not _looks_like_target_game(
        game_name=game_name,
        title=title,
        lemma_title=lemma_title,
        lemma_desc=lemma_desc,
        description=description or meta_description,
    ):
        return None, page_data

    screenshots = _extract_screenshots(
        page_data or {},
        meta_image,
        max_items=max_screenshots,
    )
    details = BaikeGameDetails(
        url=final_url,
        title=lemma_title or title or game_name,
        description=description,
        screenshots=screenshots,
        metadata={
            "lemma_title": lemma_title or game_name,
            "lemma_desc": lemma_desc,
            "catalog_titles": [
                _clean_text(x.get("title") or "")
                for x in (page_data or {}).get("catalog") or []
                if x.get("title")
            ],
        },
    )
    return details, page_data


def fetch_baike_game_details(
    game_name: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 15.0,
    max_screenshots: int = 5,
    rate_limit: float = 1.5,
) -> BaikeGameDetails | None:
    """Fetch a Baidu Baike entry and extract fallback game details.

    We first hit the generic ``/item/<name>`` URL. If the resolved page is not
    game-like but exposes a disambiguation list, we follow the best game-like
    candidate once.

    ``rate_limit`` — seconds to sleep after each HTTP request to avoid
    triggering Baidu's anti-scraping (captcha / 403).
    """
    game_name = _clean_text(game_name)
    if not game_name:
        return None

    owned = client is None
    proxy_url = None
    if owned:
        from src.utils.proxy import get_proxy_url
        proxy_url = get_proxy_url()

    c = client or httpx.Client(
        trust_env=False,
        timeout=timeout,
        follow_redirects=True,
        proxy=proxy_url,
        headers={
            "User-Agent": _UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )

    try:
        first_url = _build_item_url(game_name)
        try:
            resp = c.get(first_url)
            if rate_limit > 0:
                time.sleep(rate_limit)
        except Exception as exc:
            logger.info(f"[baidu_baike] GET {first_url} failed: {exc}")
            return None
        if resp.status_code != 200:
            logger.info(
                f"[baidu_baike] {first_url} returned {resp.status_code}"
            )
            return None

        details, page_data = _parse_baike_html(
            resp.text,
            game_name=game_name,
            final_url=str(resp.url),
            max_screenshots=max_screenshots,
        )
        if details is not None:
            return details

        candidate = _pick_game_navigation_candidate(page_data or {}, game_name)
        if not candidate or not candidate.get("lemmaId"):
            return None

        candidate_url = _build_item_url(game_name, candidate["lemmaId"])
        try:
            resp2 = c.get(candidate_url)
            if rate_limit > 0:
                time.sleep(rate_limit)
        except Exception as exc:
            logger.info(f"[baidu_baike] GET {candidate_url} failed: {exc}")
            return None
        if resp2.status_code != 200:
            logger.info(
                f"[baidu_baike] {candidate_url} returned {resp2.status_code}"
            )
            return None

        details, _ = _parse_baike_html(
            resp2.text,
            game_name=game_name,
            final_url=str(resp2.url),
            max_screenshots=max_screenshots,
        )
        return details
    finally:
        if owned:
            c.close()


__all__ = [
    "BaikeGameDetails",
    "fetch_baike_game_details",
    "_extract_page_data",
    "_parse_baike_html",
    "_pick_game_navigation_candidate",
]
