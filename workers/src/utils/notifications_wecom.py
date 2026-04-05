"""WeCom (企业微信) webhook notification utility."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


def send_wecom_text(
    webhook_url: str,
    text: str,
    mentioned_list: list[str] | None = None,
) -> None:
    """Send a plain text message via WeCom webhook.

    mentioned_list entries are WeCom user IDs, or "@all" to mention everyone.
    """
    payload: dict = {"msgtype": "text", "text": {"content": text}}
    if mentioned_list:
        payload["text"]["mentioned_list"] = mentioned_list
    _post(webhook_url, payload)


def send_wecom_markdown(webhook_url: str, markdown: str) -> None:
    """Send a markdown-formatted message via WeCom webhook.

    WeCom supports a limited markdown subset (headings, bold, links, colors).
    """
    payload = {"msgtype": "markdown", "markdown": {"content": markdown}}
    _post(webhook_url, payload)


def send_wecom_news(webhook_url: str, articles: list[dict]) -> None:
    """Send a news/card message with up to 8 articles.

    Each article dict: {title, description, url, picurl}.
    """
    payload = {"msgtype": "news", "news": {"articles": articles[:8]}}
    _post(webhook_url, payload)


def _post(webhook_url: str, payload: dict) -> None:
    try:
        with httpx.Client() as client:
            resp = client.post(webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode") != 0:
                logger.error(f"WeCom error: {data}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"WeCom notification failed: {e}")
