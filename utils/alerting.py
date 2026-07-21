"""
Alerting System
================
Sends critical notifications to external channels (Telegram/Slack)
so a human is looped in fast.

Alert triggers:
- Risk-gate trips (daily loss breaker, overtrading guard)
- Broker connectivity loss
- Order rejections
- Unhandled exceptions in the main loop

Ref: Blueprint Section 10
"""

import logging
import requests
from datetime import datetime, timezone

log = logging.getLogger("gold_bot.alerting")


def send_alert(message: str, severity: str = "INFO") -> bool:
    """
    Pushes critical events to an external channel.
    Supports Telegram Bot API and generic Slack-style webhooks.

    Args:
        message: The alert message text.
        severity: Severity level — 'INFO', 'WARNING', 'ERROR', 'CRITICAL'.

    Returns:
        True if alert was sent successfully, False otherwise.
    """
    from config.settings import ALERT_WEBHOOK_URL, ALERT_CHAT_ID

    if not ALERT_WEBHOOK_URL:
        log.warning("Alert webhook URL not configured — alert not sent: %s", message)
        return False

    severity_emoji = {
        "INFO": "ℹ️",
        "WARNING": "⚠️",
        "ERROR": "🔴",
        "CRITICAL": "🚨",
    }
    emoji = severity_emoji.get(severity, "📢")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    formatted_message = (
        f"{emoji} *Gold Bot Alert — {severity}*\n"
        f"─────────────────────\n"
        f"{message}\n"
        f"─────────────────────\n"
        f"🕐 {timestamp}"
    )

    try:
        # Detect if this is a Telegram Bot API URL
        if "api.telegram.org" in ALERT_WEBHOOK_URL:
            response = _send_telegram(ALERT_WEBHOOK_URL, ALERT_CHAT_ID, formatted_message)
        else:
            # Generic webhook (Slack, Discord, etc.)
            response = _send_webhook(ALERT_WEBHOOK_URL, formatted_message)

        if response and response.status_code == 200:
            log.debug("Alert sent successfully: [%s] %s", severity, message)
            return True
        else:
            status = response.status_code if response else "no response"
            log.error("Alert delivery failed (status %s): %s", status, message)
            return False

    except requests.RequestException as e:
        log.error("Alert delivery exception: %s — original alert: [%s] %s", e, severity, message)
        return False


def _send_telegram(bot_url: str, chat_id: str, message: str) -> requests.Response:
    """Sends a message via Telegram Bot API."""
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    return requests.post(bot_url, json=payload, timeout=10)


def _send_webhook(url: str, message: str) -> requests.Response:
    """Sends a message via generic webhook (Slack/Discord format)."""
    payload = {"text": message}
    return requests.post(url, json=payload, timeout=10)


def send_startup_alert() -> None:
    """Sends a bot startup notification."""
    from config.settings import SYMBOL, TIMEFRAME, TRADING_MODE
    send_alert(
        f"🤖 Gold Trading Bot started\n"
        f"Symbol: {SYMBOL}\n"
        f"Timeframe: {TIMEFRAME}\n"
        f"Mode: {TRADING_MODE.upper()}",
        severity="INFO"
    )


def send_shutdown_alert(reason: str = "Normal shutdown") -> None:
    """Sends a bot shutdown notification."""
    send_alert(f"🛑 Gold Trading Bot stopped\nReason: {reason}", severity="WARNING")


def send_risk_gate_alert(gate_name: str, details: str = "") -> None:
    """Sends an alert when a risk gate is triggered."""
    msg = f"Risk gate triggered: {gate_name}"
    if details:
        msg += f"\nDetails: {details}"
    send_alert(msg, severity="WARNING")


def send_error_alert(error_msg: str, context: str = "") -> None:
    """Sends an alert for errors and exceptions."""
    msg = f"Error: {error_msg}"
    if context:
        msg += f"\nContext: {context}"
    send_alert(msg, severity="ERROR")
