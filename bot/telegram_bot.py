"""
Telegram notification layer.

Uses plain requests (no async) so it runs cleanly inside GitHub Actions
ephemeral jobs.  Confirmation is done via inline keyboards + long-polling
getUpdates — the GH Actions job waits up to CONFIRMATION_TIMEOUT_SECONDS
before giving up and skipping the action.
"""

from __future__ import annotations

import time

import requests
import urllib3
from loguru import logger

from config import settings

urllib3.disable_warnings()

_TG_BASE = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


# ------------------------------------------------------------------ low level


def _api(method: str, **kwargs) -> dict:
    r = requests.post(
        f"{_TG_BASE}/{method}",
        json=kwargs,
        verify=settings.ssl_verify,
        timeout=35,
    )
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------- chat id helpers


def resolve_chat_id() -> str:
    """
    Return a numeric chat_id string.

    If settings.telegram_chat_id is already numeric, return as-is.
    Otherwise try to resolve @username from recent getUpdates.
    """
    chat_id = settings.telegram_chat_id

    if chat_id.lstrip("-").isdigit():
        return chat_id

    # Try to resolve @username from update history
    try:
        updates = _api("getUpdates")
        for update in updates.get("result", []):
            msg = update.get("message") or (
                update.get("callback_query") or {}
            ).get("message")
            if not msg:
                continue
            chat = msg.get("chat", {})
            uname = chat.get("username") or ""
            if uname and f"@{uname}".lower() == chat_id.lower():
                numeric = str(chat["id"])
                logger.info(f"Resolved {chat_id} → {numeric}")
                return numeric
    except Exception as exc:
        logger.warning(f"getUpdates failed while resolving chat_id: {exc}")

    # Return raw value — sendMessage will fail with a useful error if wrong
    return chat_id


# ------------------------------------------------------------------ public API


def send_message(text: str, reply_markup: dict | None = None) -> dict:
    chat_id = resolve_chat_id()
    kwargs: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    try:
        return _api("sendMessage", **kwargs)
    except Exception as exc:
        logger.error(f"sendMessage failed: {exc}")
        return {}


def send_with_confirmation(
    text: str,
    action_id: str,
    action_label: str = "Ejecutar",
) -> dict:
    """Send message with ✅ / ❌ inline keyboard."""
    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": f"✅ {action_label}",
                    "callback_data": f"confirm:{action_id}",
                },
                {
                    "text": "❌ Saltar",
                    "callback_data": f"skip:{action_id}",
                },
            ]
        ]
    }
    return send_message(text, reply_markup=keyboard)


def wait_for_callback(
    action_id: str,
    timeout: int | None = None,
) -> bool:
    """
    Block until the user taps ✅ or ❌ in Telegram, or until timeout.

    Returns True if confirmed, False if skipped or timed out.

    Safe to call from GH Actions — uses long-polling getUpdates so it
    doesn't spin unnecessarily.
    """
    if timeout is None:
        timeout = settings.confirmation_timeout_seconds

    deadline = time.time() + timeout
    last_update_id = 0

    logger.info(
        f"Waiting up to {timeout // 60}m for '{action_id}' confirmation…"
    )

    while time.time() < deadline:
        remaining = int(deadline - time.time())
        poll_timeout = min(30, remaining)
        if poll_timeout <= 0:
            break

        try:
            updates = _api(
                "getUpdates",
                offset=last_update_id + 1,
                timeout=poll_timeout,
            )
        except Exception as exc:
            logger.warning(f"getUpdates error: {exc}")
            time.sleep(5)
            continue

        for update in updates.get("result", []):
            last_update_id = update["update_id"]
            cbq = update.get("callback_query")
            if not cbq:
                continue

            data = cbq.get("data", "")

            # Acknowledge the button press immediately
            try:
                _api("answerCallbackQuery", callback_query_id=cbq["id"])
            except Exception:
                pass

            if f":{action_id}" not in data:
                continue

            if data.startswith("confirm:"):
                logger.info(f"✅ Confirmed: {action_id}")
                return True
            if data.startswith("skip:"):
                logger.info(f"❌ Skipped: {action_id}")
                return False

    logger.info(f"⏱ Timed out waiting for '{action_id}' — auto-skipping")
    return False
