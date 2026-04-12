from __future__ import annotations

from pathlib import Path

import pytest

from src.scrapers.baidu_baike import (
    _extract_page_data,
    _parse_baike_html,
    _pick_game_navigation_candidate,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "baidu_baike_yanglegeyang.html"


@pytest.fixture(scope="module")
def baike_html() -> str:
    if not FIXTURE_PATH.exists():
        pytest.skip(f"fixture not present: {FIXTURE_PATH}")
    return FIXTURE_PATH.read_text(encoding="utf-8")


def test_extract_page_data_from_baike_fixture(baike_html: str) -> None:
    data = _extract_page_data(baike_html)
    assert data is not None
    assert data["lemmaTitle"] == "羊了个羊"
    assert data["lemmaDesc"].endswith("游戏")


def test_parse_baike_html_prefers_gameplay_text_and_images(
    baike_html: str,
) -> None:
    details, page_data = _parse_baike_html(
        baike_html,
        game_name="羊了个羊",
        final_url="https://baike.baidu.com/item/%E7%BE%8A%E4%BA%86%E4%B8%AA%E7%BE%8A/61983244",
        max_screenshots=5,
    )

    assert page_data is not None
    assert details is not None
    assert details.title == "羊了个羊"
    assert "栏框内一共可以储存7张卡牌" in details.description
    assert details.screenshots[:4] == [
        "https://bkimg.cdn.bcebos.com/pic/cover-abstract",
        "https://bkimg.cdn.bcebos.com/pic/cover-shots",
        "https://bkimg.cdn.bcebos.com/pic/shot-1",
        "https://bkimg.cdn.bcebos.com/pic/shot-2",
    ]


def test_pick_game_navigation_candidate_prefers_game_entry() -> None:
    candidate = _pick_game_navigation_candidate(
        {
            "navigation": {
                "lemmas": [
                    {
                        "lemmaId": 1,
                        "lemmaTitle": "元梦之星",
                        "lemmaDesc": "歌曲",
                        "classify": ["音乐作品"],
                    },
                    {
                        "lemmaId": 2,
                        "lemmaTitle": "元梦之星",
                        "lemmaDesc": "2023年腾讯天美工作室群研发的手机游戏",
                        "classify": ["文娱活动"],
                    },
                ]
            }
        },
        "元梦之星",
    )
    assert candidate is not None
    assert candidate["lemmaId"] == 2
