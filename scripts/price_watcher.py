#!/usr/bin/env python3
"""
Price movement watcher.

Runs every 2h. Compares each squad player's current price against the
last stored snapshot and alerts on moves > PRICE_DROP_THRESHOLD (€150K default).

Also checks the watchlist for market / price / score events.

Sends a single consolidated Telegram message — no confirmation needed.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

from config import settings
from api.client import BiwengerClient
from api.models import Player
from data import store

THRESHOLD: int = int(os.environ.get("PRICE_DROP_THRESHOLD", "150000"))


def _fmt_delta(delta: int) -> str:
    sign = "+" if delta >= 0 else ""
    return f"{sign}€{delta:,.0f}"


def check_squad_prices(client: BiwengerClient) -> list[str]:
    """Return alert lines for squad players with significant price moves."""
    squad_raw = client.get_squad()
    players: list[Player] = [Player(**p) for p in squad_raw.get("all_players", [])]
    alerts: list[str] = []

    for p in players:
        last_price = store.get_latest_price(p.id)
        if last_price is None:
            continue
        delta = p.price - last_price
        if abs(delta) >= THRESHOLD:
            emoji = "📈" if delta > 0 else "📉"
            alerts.append(
                f"{emoji} *{p.name}* ({p.position_name}): "
                f"€{p.price/1e6:.2f}M {_fmt_delta(delta)}"
            )
        # Save updated snapshot
        store.save_player_snapshot({
            "id": p.id, "name": p.name, "position": p.position,
            "team_name": p.team.name if p.team else None,
            "price": p.price, "price_increment": p.priceIncrement,
            "points": p.points, "games_played": p.games_played,
            "fitness": p.fitness, "status": p.status,
        })

    return alerts


def check_squad_on_market(client: BiwengerClient, my_squad_ids: set[int]) -> list[str]:
    """Detect if any rival is selling our squad players (forcing sell pressure)."""
    market = client.get_market()
    alerts: list[str] = []
    for sale in market.get("sales", []):
        pid = (sale.get("player") or {}).get("id")
        if pid and pid in my_squad_ids:
            seller = sale.get("user") or {}
            seller_name = seller.get("name", "Rival")
            price = sale.get("price", 0)
            alerts.append(
                f"⚠️ *Rival '{seller_name}' vende a tu jugador* "
                f"(id {pid}) por €{price/1e6:.2f}M — ¿precio en caída?"
            )
    return alerts


def check_watchlist(client: BiwengerClient, my_squad_ids: set[int]) -> list[str]:
    """Check each watched player for market/price/score triggers."""
    watchlist = store.get_watchlist()
    if not watchlist:
        return []

    market = client.get_market()
    market_ids = {
        (sale.get("player") or {}).get("id"): sale
        for sale in market.get("sales", [])
        if (sale.get("player") or {}).get("id")
    }

    alerts: list[str] = []
    for entry in watchlist:
        pid = entry["player_id"]
        name = entry["player_name"]

        if pid in my_squad_ids:
            continue  # already own them

        # Market alert
        if entry["alert_on_market"] and pid in market_ids:
            sale = market_ids[pid]
            price = sale.get("price", 0)
            source = "pool libre 🏪" if not sale.get("user") else "rival 👤"
            alerts.append(
                f"🎯 *WATCHLIST: {name}* disponible en el mercado ({source}) "
                f"por €{price/1e6:.2f}M"
            )

        # Price drop alert
        if entry["alert_on_price_drop"] and entry.get("price_at_add"):
            try:
                raw = client.get_player(pid)
                current_price = raw.get("price", 0)
                price_at_add = entry["price_at_add"]
                drop = price_at_add - current_price
                if drop >= 100_000:
                    alerts.append(
                        f"📉 *WATCHLIST: {name}* bajó {_fmt_delta(-drop)} "
                        f"desde que lo añadiste (€{current_price/1e6:.2f}M ahora)"
                    )
            except Exception as exc:
                logger.debug(f"Watchlist price check failed for {name}: {exc}")

        # Score alert — check last fitness entry
        if entry["alert_on_score"]:
            try:
                raw = client.get_player(pid)
                fitness = raw.get("fitness") or []
                last_score = next(
                    (f for f in reversed(fitness) if isinstance(f, (int, float))), None
                )
                if last_score and last_score >= entry.get("score_threshold", 7.0):
                    alerts.append(
                        f"⭐ *WATCHLIST: {name}* anotó *{last_score} pts* "
                        f"en la última jornada — ¿es momento de comprarlo?"
                    )
            except Exception as exc:
                logger.debug(f"Watchlist score check failed for {name}: {exc}")

    return alerts


def main() -> None:
    logger.info("💰 Price watcher starting…")

    client = BiwengerClient()
    client.login()

    squad_raw = client.get_squad()
    my_squad_ids = {
        p["id"] for p in (squad_raw.get("all_players") or []) if p.get("id")
    }

    price_alerts = check_squad_prices(client)
    market_alerts = check_squad_on_market(client, my_squad_ids)
    watch_alerts = check_watchlist(client, my_squad_ids)

    all_alerts = price_alerts + market_alerts + watch_alerts

    if not all_alerts:
        logger.info("No price movements or watchlist triggers.")
        return

    lines = ["💰 *Alertas de precio y watchlist*\n"]
    lines.extend(all_alerts)
    from bot.telegram_bot import send_message
    send_message("\n".join(lines))
    logger.info(f"Sent {len(all_alerts)} alert(s).")


if __name__ == "__main__":
    main()
