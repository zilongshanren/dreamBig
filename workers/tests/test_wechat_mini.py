"""Tests for the WeChat Mini Games scraper's __NEXT_DATA__ parsing path.

Uses a saved HTML fixture from https://sj.qq.com/wechat-game/popular-game-rank
so we don't hit the real site during testing. When Tencent changes their
page structure, this test will fail and point at the broken parser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.scrapers.wechat_mini import (
    _collect_rank_items,
    _extract_next_data,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sjqq_popular_rank.html"


@pytest.fixture(scope="module")
def popular_html() -> str:
    if not FIXTURE_PATH.exists():
        pytest.skip(f"fixture not present: {FIXTURE_PATH}")
    return FIXTURE_PATH.read_text(encoding="utf-8")


def test_extract_next_data_returns_dict(popular_html: str) -> None:
    data = _extract_next_data(popular_html)
    assert data is not None
    assert "props" in data
    assert "pageProps" in data["props"]


def test_extract_next_data_handles_missing_tag() -> None:
    assert _extract_next_data("<html>no hydration here</html>") is None


def test_extract_next_data_handles_invalid_json() -> None:
    # __NEXT_DATA__ tag with garbage JSON inside
    broken = '<script id="__NEXT_DATA__" type="application/json">{"not":</script>'
    assert _extract_next_data(broken) is None


def test_rank_items_parsed_from_popular_rank(popular_html: str) -> None:
    data = _extract_next_data(popular_html)
    items = _collect_rank_items(data, "hot")
    # The saved fixture had 20 items — allow some slack if Tencent trims.
    assert len(items) >= 10, f"expected >= 10 items, got {len(items)}"

    first = items[0]
    # Every ranked mini-game must have app_id + a display name
    assert first.get("app_id"), "first item missing app_id"
    assert (first.get("app_name") or first.get("name")), (
        "first item missing both app_name and name"
    )

    # App IDs should look like Tencent numeric strings
    for x in items[:5]:
        aid = str(x.get("app_id") or "")
        assert aid.isdigit(), f"expected numeric app_id, got {aid!r}"


def test_rank_items_empty_on_missing_components() -> None:
    fake = {"props": {"pageProps": {"dynamicCardResponse": {"data": {}}}}}
    assert _collect_rank_items(fake, "hot") == []


def test_rank_items_fallback_to_hot_card_on_unknown_chart() -> None:
    # Synthetic: two components, one is the canonical HOT card
    fake = {
        "props": {
            "pageProps": {
                "dynamicCardResponse": {
                    "data": {
                        "components": [
                            {
                                "cardId": "YYB_SOMETHING_ELSE",
                                "data": {"itemData": [{"app_id": "999", "name": "x"}]},
                            },
                            {
                                "cardId": "YYB_HOME_HOT_WECHAT_GAME",
                                "data": {
                                    "itemData": [
                                        {"app_id": "111", "name": "hot-a"},
                                        {"app_id": "222", "name": "hot-b"},
                                    ]
                                },
                            },
                        ]
                    }
                }
            }
        }
    }
    items = _collect_rank_items(fake, "totally_unknown_chart")
    assert [i["app_id"] for i in items] == ["111", "222"]


# ---------------------------------------------------------------------------
# Fixtures for the extra chart types added later
# ---------------------------------------------------------------------------
def _load(name: str) -> str:
    p = FIXTURE_PATH.parent / name
    if not p.exists():
        pytest.skip(f"fixture not present: {p}")
    return p.read_text(encoding="utf-8")


def test_parse_featured_list() -> None:
    """小游戏精选榜 /wechat-game/choice-game-list — single component.

    Fixture had 8 slots but one is an empty placeholder (ad carousel slot),
    so we only assert that the majority of slots resolve to real games
    with numeric Tencent app_ids — matching the scraper's own skip logic.
    """
    html = _load("sjqq_featured_list.html")
    data = _extract_next_data(html)
    items = _collect_rank_items(data, "featured")
    assert len(items) >= 5
    valid = [
        x for x in items
        if str(x.get("app_id", "")).isdigit()
        and (x.get("app_name") or x.get("name"))
    ]
    assert len(valid) >= 5, f"expected >= 5 valid items, got {len(valid)}"


def test_parse_tag_page_returns_category_games() -> None:
    """Tag pages like /wechat-game-tag/xiuxianyizhi behave like single-component lists."""
    html = _load("sjqq_tag_puzzle.html")
    data = _extract_next_data(html)
    items = _collect_rank_items(data, "tag_puzzle")
    assert len(items) >= 10
    # Make sure game records look complete
    first = items[0]
    assert first.get("app_id")
    assert first.get("app_name") or first.get("name")


def test_parse_tag_rpg_page() -> None:
    html = _load("sjqq_tag_rpg.html")
    data = _extract_next_data(html)
    items = _collect_rank_items(data, "tag_rpg")
    assert len(items) >= 5
