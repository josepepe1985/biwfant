#!/usr/bin/env python3
"""
Jornada deadline alert.

Checks if the current jornada's lineup lock is within DEADLINE_ALERT_HOURS.
If so, and if we haven't confirmed a lineup today, sends a Telegram alert
with ✅/❌ inline buttons that trigger the full lineup flow.

Runs every 30 minutes via .github/workflows/deadline_check.yml.
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
from engine.fixtures import refresh_fixtures
from actions.lineup import get_best_lineup, build_lineup_message
from engine.llm_advisor import narrate_lineup
from bot.telegram_bot import send_message, send_with_confirmation, wait_for_callback

ALERT_HOURS: float = float(os.environ.get("DEADLINE_ALERT_HOURS", "3"))


def main() -> None:
    client = BiwengerClient()
    client.login()

    # ─── Check deadline ──────────────────────────────────────────────────────
    round_info = client.get_next_round()
    if not round_info:
        logger.info("No upcoming round found — nothing to do.")
        return

    hours_left = None
    if round_info.get("deadline_utc"):
        from api.models import RoundInfo
        ri = RoundInfo(**round_info)
        hours_left = ri.hours_until_deadline

    logger.info(
        f"Next round: {round_info['name']} | "
        f"deadline: {round_info.get('deadline_utc', 'unknown')} | "
        f"hours_left: {hours_left}"
    )

    if hours_left is None or hours_left > ALERT_HOURS:
        logger.info(
            f"Deadline is {hours_left:.1f}h away — no alert needed (threshold {ALERT_HOURS}h)."
        )
        return

    if hours_left < 0:
        logger.info("Deadline already passed.")
        return

    # ─── Already confirmed today? ─────────────────────────────────────────────
    if store.lineup_confirmed_today():
        logger.info("Lineup already confirmed today — skipping alert.")
        return

    logger.info(f"⏰ Deadline in {hours_left:.1f}h — sending alert!")

    # ─── Build optimal lineup ─────────────────────────────────────────────────
    squad_raw = client.get_squad()
    players: list[Player] = [Player(**p) for p in squad_raw.get("all_players", [])]
    current_formation: str = (squad_raw.get("lineup") or {}).get("type", "4-4-2")
    current_ids: list[int] = squad_raw.get("lineup_player_ids", [])

    fixture_map = {}
    try:
        fixture_map = refresh_fixtures(ssl_verify=settings.ssl_verify)
    except Exception:
        pass

    starting_xi, formation, predicted_pts = get_best_lineup(players, current_formation, fixture_map)

    if not starting_xi:
        send_message(
            f"⏰ *{round_info['name']} cierra en {hours_left:.0f}h*\n"
            "⚠️ No se pudo calcular alineación óptima."
        )
        return

    narrative = narrate_lineup(starting_xi, formation, predicted_pts, fixture_map)
    lineup_msg = (
        f"⏰ *{round_info['name']} — cierra en {hours_left:.0f}h*\n\n"
        + build_lineup_message(starting_xi, formation, predicted_pts, current_ids, fixture_map, narrative)
    )

    if settings.dry_run:
        send_message(lineup_msg + "\n\n_(dry-run: no ejecutado)_")
        return

    send_with_confirmation(lineup_msg, action_id="lineup_deadline", action_label="Alinear ahora")
    confirmed = wait_for_callback("lineup_deadline", timeout=600)  # 10 min window

    if confirmed:
        starting_ids = [p.id for p in starting_xi]
        bench = [p for p in players if p.id not in set(starting_ids)]
        client.set_lineup(formation, starting_ids, [p.id for p in bench[:3]])
        store.save_decision(
            "lineup", None, formation,
            f"Deadline alert: {formation} {predicted_pts:.1f}pts",
            confirmed=True, executed=True,
        )
        send_message(f"✅ Alineación *{formation}* confirmada antes del cierre.")
    else:
        send_message("⏭ Alineación omitida — recuerda confirmarla manualmente.")


if __name__ == "__main__":
    main()
