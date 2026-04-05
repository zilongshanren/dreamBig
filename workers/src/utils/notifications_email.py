"""Email notification utility (SMTP).

Reads SMTP configuration from environment variables:
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_email(
    to: str | list[str],
    subject: str,
    body_html: str,
    body_text: str | None = None,
) -> bool:
    """Send an email via SMTP. Returns True on success, False otherwise."""
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    from_addr = os.environ.get("SMTP_FROM", user)

    if not all([host, user, password, from_addr]):
        logger.warning("SMTP not configured; skipping email")
        return False

    recipients = [to] if isinstance(to, str) else list(to)
    if not recipients:
        logger.warning("No recipients provided; skipping email")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)

    if body_text:
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, recipients, msg.as_string())
        logger.info(f"Email sent to {recipients}: {subject}")
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"Email send failed: {e}")
        return False
