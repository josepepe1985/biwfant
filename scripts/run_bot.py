#!/usr/bin/env python3
"""
Main bot entry point.

One run = one full cycle:
  1. Fetch squad + fixtures
  2. Compute optimal lineup (fixture-aware) → LLM narrative → Telegram → confirm
  3. Scan market (fixture-aware) → LLM transfer advice → Telegram → confirm sells
  4. Execute confirmed actions
  5. Save snapshots to SQLite history
  6. Summary message

Designed to run inside GitHub Actions (cron or manual dispatch).
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
from engine.optimizer import optimize_lineup
from engine.market_scanner import scan_market, find_sell_candidates
from engine.scorer import score_player
from engine.llm_advisor import advise_transfers, narrate_lineup, summarise_market_scan
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
    logger.info(f"LLM: {'✅ enabled (' + settings.llm_model + ')' if settings.llm_enabled else '⚠️ disabled (no LLM_API_KEY)'}")

    client = BiwengerClient()
    client.login()

    # ---------------------------------------------------------------- fixtures
    logger.info("Refreshing fixture difficulty data…")
    try:
        fixture_map = refresh_fixtures(ssl_verify=settings.ssl_verify)
        logger.info(f"Fixture map: {len(fixture_map)} teams loaded")
    except Exception as exc:
        logger.warning(f"Fixture refresh failed, using neutral difficulty: {exc}")
        fixture_map = {}

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

    # Save player snapshots for ML history
    for p in players:
        try:
            store.save_player_snapshot({
                "id": p.id, "name": p.name, "position": p.position,
                "team_name": p.team.name if p.team else None,
                "price": p.price, "price_increment": p.priceIncrement,
                "points": p.points, "games_played": p.games_played,
                "fitness": p.fitness, "status": p.status,
            })
        except Exception as exc:
            logger.debug(f"Snapshot save failed for {p.name}: {exc}")

    # ---------------------------------------------------------- standings + rivalry
    rivalry_ctx: dict = {}
    standings_line = ""
    try:
        raw_standings = client.get_standings()
        store.save_standings(raw_standings)
        my_entry = next((u for u in raw_standings if u.get("id") == settings.biwenger_user_id), None)
        if my_entry:
            my_pos = my_entry.get("position", 0)
            my_pts = my_entry.get("points", 0)
            leader = raw_standings[0] if raw_standings else {}
            rival_above = next((u for u in raw_standings if u.get("position") == my_pos - 1), None)
            rival_below = next((u for u in raw_standings if u.get("position") == my_pos + 1), None)
            standings_line = (
                f"📊 Posición: *{my_pos}ª* | Gap líder: *-{leader.get('points',0)-my_pts}pts*"
            )
            if rival_above:
                gap = rival_above.get("points", 0) - my_pts
                standings_line += f" | Rival: *{rival_above['name']}* (-{gap}pts)"
            rivalry_ctx = {
                "my_position": my_pos,
                "rival_above": rival_above,
                "rival_below": rival_below,
                "gap_to_leader": leader.get("points", 0) - my_pts,
            }
            logger.info(f"Standings: {my_pos}ª, {gap if rival_above else 'N/A'}pts from rival above")
    except Exception as exc:
        logger.warning(f"Standings fetch failed: {exc}")

    # Status summary
    llm_status = "✅ IA activa" if settings.llm_enabled else "⚠️ Sin IA"
    status_lines = [
        f"🤖 *Vampiros United — ciclo del bot*",
        f"💰 Balance: €{balance:,.0f}",
        f"📊 Puntos: {total_points}",
        f"👥 Plantilla: {len(players)} jugadores",
        f"🧠 {llm_status}",
    ]
    if standings_line:
        status_lines.append(standings_line)
    status_lines.append(f"{'⚠️ *MODO DRY\\-RUN*' if settings.dry_run else '🟢 MODO LIVE'}")
    send_message("\n".join(status_lines))

    # --------------------------------------------------------------- lineup
    logger.info("Computing optimal lineup…")
    starting_xi, best_formation, predicted_pts = get_best_lineup(
        players, current_formation, fixture_map
    )

    if starting_xi:
        # Ask LLM for narrative explanation
        lineup_narrative = narrate_lineup(starting_xi, best_formation, predicted_pts, fixture_map)

        lineup_msg = build_lineup_message(
            starting_xi, best_formation, predicted_pts,
            current_player_ids, fixture_map, lineup_narrative
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

        store.save_decision(
            "lineup", None, best_formation,
            f"Formation: {best_formation}, predicted: {predicted_pts:.1f}",
            confirmed=confirmed if not settings.dry_run else None,
            executed=confirmed and not settings.dry_run,
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
        opportunities = scan_market(client, my_squad_ids, fixture_map)
        sell_candidates = find_sell_candidates(players, opportunities, fixture_map)

        # Save market snapshots
        for opp in opportunities[:10]:
            try:
                store.save_market_snapshot(opp)
            except Exception as exc:
                logger.debug(f"Market snapshot failed: {exc}")

        # Build squad summary string for LLM context
        squad_summary = (
            f"{len(players)} jugadores, "
            f"formación actual {current_formation}, "
            f"balance €{balance/1_000_000:.1f}M"
        )
        if rivalry_ctx:
            pos = rivalry_ctx.get("my_position")
            gap = rivalry_ctx.get("gap_to_leader")
            ra = rivalry_ctx.get("rival_above") or {}
            squad_summary += (
                f", posición {pos}ª en liga, "
                f"gap líder -{gap}pts, "
                f"rival inmediato: {ra.get('name','?')}"
            )

        # Ask LLM for transfer advice
        llm_advice = advise_transfers(
            balance=balance,
            jornada=None,
            sell_candidates=sell_candidates,
            buy_opportunities=opportunities[:5],
            squad_summary=squad_summary,
        )

        # One-line market summary
        market_summary = summarise_market_scan(opportunities[:5], balance)

        if not opportunities and not sell_candidates:
            send_message("📊 *Mercado*: Sin recomendaciones destacadas.")
        else:
            transfers_msg = build_transfers_message(
                sell_candidates, opportunities[:5], balance,
                llm_advice=llm_advice,
                market_summary=market_summary,
            )

            # Record LLM decision
            if llm_advice:
                store.save_decision(
                    "transfer_advice", None,
                    llm_advice.get("buy", {}).get("player"),
                    reasoning=llm_advice.get("summary_es", ""),
                    confidence=llm_advice.get("confidence"),
                )

            if settings.dry_run or not sell_candidates:
                send_message(transfers_msg)
            else:
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
                        f"sell_{p.id}", timeout=300
                    )
                    store.save_decision(
                        "sell", p.id, p.name,
                        reasoning=", ".join(candidate["triggers"]),
                        confirmed=confirmed, executed=confirmed,
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



if __name__ == "__main__":
    main()
