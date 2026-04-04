"""Notification utilities for sending alerts via various channels."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


def send_feishu_text(webhook_url: str, text: str):
    """Send a simple text message via Feishu webhook."""
    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        with httpx.Client() as client:
            resp = client.post(webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
    except Exception as e:
        logger.error(f"Feishu notification failed: {e}")


def notify_scrape_error(platform: str, error: str):
    """Notify team about a scraper error."""
    webhook = os.environ.get("FEISHU_WEBHOOK_URL")
    if not webhook:
        return
    send_feishu_text(webhook, f"⚠️ [{platform}] 爬虫出错: {error}")


def notify_stale_data(platform: str, hours_since: int):
    """Notify team about stale data."""
    webhook = os.environ.get("FEISHU_WEBHOOK_URL")
    if not webhook:
        return
    send_feishu_text(
        webhook,
        f"⚠️ [{platform}] 数据已过期 {hours_since} 小时，请检查爬虫状态",
    )
