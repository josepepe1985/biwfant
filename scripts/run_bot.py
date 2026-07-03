#!/usr/bin/env python3
"""
Main bot entry point.

One run = one full cycle:
  1. Fetch squad + market data
  2. Compute optimal lineup  → send to Telegram → wait for confirmation
  3. Scan market             → send recommendations → wait per sell
  4. Execute confirmed actions
  5. Summary message

Designed to run inside GitHub Actions (cron or manual dispatch).
"""

from __future__ import annotations

import os
import sys

# Allow running from the repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

from config import settings
from api.client import BiwengerClient
from api.models import Player
from engine.optimizer import optimize_lineup
from engine.market_scanner import scan_market, find_sell_candidates
from engine.scorer import score_player
from actions.lineup import get_best_lineup, build_lineup_message
from actions.transfers import build_transfers_message
from bot.telegram_bot import (
    send_message,
    send_with_confirmation,
    wait_for_callback,
)


def main() -> None:
    logger.info("🤖 Biwfant bot starting…")
    logger.info(f"Mode: {'⚠️ DRY-RUN' if settings.dry_run else '🟢 LIVE'}")

    client = BiwengerClient()
    client.login()

    # ---------------------------------------------------------------- squad
    logger.info("Fetching squad…")
    squad_raw = client.get_squad()

    players: list[Player] = [Player(**p) for p in squad_raw.get("all_players", [])]
    balance: int = squad_raw.get("balance", 0)
    total_points: int = squad_raw.get("points", 0)
    lineup_raw = squad_raw.get("lineup") or {}
    current_formation: str = lineup_raw.get("type", "4-4-2")
    current_player_ids: list[int] = squad_raw.get("lineup_player_ids", [])

    logger.info(
        f"Squad: {len(players)} players | "
        f"Balance: €{balance:,.0f} | Points: {total_points}"
    )

    # Status summary
    send_message(
        f"🤖 *Vampiros United — ciclo del bot*\n"
        f"💰 Balance: €{balance:,.0f}\n"
        f"📊 Puntos: {total_points}\n"
        f"👥 Plantilla: {len(players)} jugadores\n"
        f"{'⚠️ *MODO DRY\\-RUN*' if settings.dry_run else '🟢 MODO LIVE'}"
    )

    # --------------------------------------------------------------- lineup
    logger.info("Computing optimal lineup…")
    starting_xi, best_formation, predicted_pts = get_best_lineup(
        players, current_formation
    )

    if starting_xi:
        lineup_msg = build_lineup_message(
            starting_xi, best_formation, predicted_pts, current_player_ids
        )

        if settings.dry_run:
            send_message(lineup_msg + "\n\n_(dry-run: alineación NO aplicada)_")
            confirmed = False
        else:
            send_with_confirmation(
                lineup_msg, action_id="lineup", action_label="Alinear"
            )
            confirmed = wait_for_callback(
                "lineup", timeout=settings.confirmation_timeout_seconds
            )

        if confirmed and not settings.dry_run:
            starting_ids = [p.id for p in starting_xi]
            bench = [p for p in players if p.id not in set(starting_ids)]
            reserves = [p.id for p in bench[:3]]
            client.set_lineup(best_formation, starting_ids, reserves)
            send_message(
                f"✅ Alineación *{best_formation}* aplicada "
                f"({predicted_pts:.1f} pts esperados)"
            )
    else:
        send_message("⚠️ No se pudo calcular alineación óptima.")

    # ------------------------------------------------------------- market
    logger.info("Scanning market…")
    my_squad_ids = {p.id for p in players}

    try:
        opportunities = scan_market(client, my_squad_ids)
        sell_candidates = find_sell_candidates(players, opportunities)

        if not opportunities and not sell_candidates:
            send_message("📊 *Mercado*: Sin recomendaciones destacadas.")
        else:
            transfers_msg = build_transfers_message(
                sell_candidates, opportunities[:5], balance
            )

            if settings.dry_run or not sell_candidates:
                send_message(transfers_msg)
            else:
                # Ask for each sell candidate individually (max 2 per cycle)
                send_message(transfers_msg)
                for candidate in sell_candidates[:2]:
                    p: Player = candidate["player"]
                    ask = candidate["ask_price"]
                    sell_msg = (
                        f"💸 ¿Listo a *{p.name}* ({p.position_name}) "
                        f"por *€{ask:,.0f}*?\n"
                        f"Razones: {', '.join(candidate['triggers'])}"
                    )
                    send_with_confirmation(
                        sell_msg,
                        action_id=f"sell_{p.id}",
                        action_label=f"Vender {p.name}",
                    )
                    confirmed = wait_for_callback(
                        f"sell_{p.id}", timeout=300  # 5 min per sell decision
                    )
                    if confirmed:
                        client.list_player_for_sale(p.id, ask)
                        send_message(
                            f"✅ *{p.name}* puesto en venta por €{ask:,.0f}"
                        )

    except Exception as exc:
        logger.error(f"Market scan failed: {exc}")
        send_message(f"⚠️ Error en escaneo de mercado: {exc}")

    logger.info("✅ Bot cycle complete.")
    send_message("🏁 Ciclo completado. ¡Hasta la próxima!")


if __name__ == "__main__":
    main()
