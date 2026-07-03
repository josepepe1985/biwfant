#!/usr/bin/env python3
"""
One-shot helper: prints your personal Telegram chat ID.

Run this AFTER sending /start (or any message) to the Biwenger bot
from your personal Telegram account.

Usage:
    python scripts/setup_chat_id.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import urllib3
from config import settings

urllib3.disable_warnings()

BASE = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


def main() -> None:
    print("Fetching recent updates from the bot…\n")
    r = requests.get(
        f"{BASE}/getUpdates",
        verify=settings.ssl_verify,
        timeout=15,
    )
    r.raise_for_status()
    updates = r.json().get("result", [])

    if not updates:
        print(
            "No updates found.\n"
            "➡  Open Telegram, find your bot, and send it any message.\n"
            "➡  Then re-run this script."
        )
        return

    seen: dict[str, dict] = {}
    for update in updates:
        msg = update.get("message") or (
            update.get("callback_query") or {}
        ).get("message")
        if not msg:
            continue
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        chat_type = chat.get("type", "")
        name = (
            chat.get("first_name", "")
            + " "
            + chat.get("last_name", "")
        ).strip() or chat.get("title", "")
        username = chat.get("username", "")
        if chat_id not in seen:
            seen[chat_id] = {
                "type": chat_type,
                "name": name,
                "username": f"@{username}" if username else "",
            }

    print("Chats found:\n")
    for cid, info in seen.items():
        print(
            f"  chat_id: {cid}  |  type: {info['type']}  |  "
            f"name: {info['name']}  |  username: {info['username']}"
        )

    print(
        "\n➡  Copy your personal chat_id and set it as:\n"
        "   TELEGRAM_CHAT_ID=<your_chat_id>  in .env\n"
        "   and as a GitHub Actions secret named TELEGRAM_CHAT_ID"
    )


if __name__ == "__main__":
    main()
