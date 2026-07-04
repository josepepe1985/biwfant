#!/usr/bin/env python3
"""
Telegram command listener.

Handles text commands from Telegram:
  /squad    — show current squad with scores
  /market   — top 5 market opportunities right now
  /watchlist — show current watchlist
  /watch <name>   — add player to watchlist (fuzzy match)
  /unwatch <name> — remove from watchlist
  /status   — quick status: balance, position, next jornada deadline
  /help     — list commands

Runs for LISTENER_TIMEOUT seconds (default 120s) polling for updates.
Designed to be triggered via workflow_dispatch or as a periodic 2h job
that checks for any pending commands alongside price_watcher.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

from config import settings
from api.client import BiwengerClient
from api.models import Player
from data import store
from engine.scorer import score_player
from engine.fixtures import refresh_fixtures
from bot.telegram_bot import send_message, _api, resolve_chat_id

TIMEOUT: int = int(os.environ.get("LISTENER_TIMEOUT", "120"))

HELP_TEXT = (
    "🤖 *Comandos disponibles:*\n\n"
    "/squad — Tu plantilla actual con puntuaciones\n"
    "/market — Top 5 oportunidades en el mercado\n"
    "/watchlist — Tu lista de seguimiento\n"
    "/watch <nombre> — Añadir jugador al seguimiento\n"
    "/unwatch <nombre> — Eliminar del seguimiento\n"
    "/status — Balance, posición y próximo cierre\n"
    "/help — Este mensaje"
)


def _fuzzy_find_player(name_query: str, client: BiwengerClient) -> dict | None:
    """
    Find a player by fuzzy name match.
    Searches recent market snapshots + squad for a name match.
    Returns raw player dict or None.
    """
    name_query = name_query.lower().strip()
    # Check squad first
    try:
        squad_raw = client.get_squad()
        for p_raw in squad_raw.get("all_players", []):
            if name_query in p_raw.get("name", "").lower():
                return p_raw
    except Exception:
        pass
    # Check recent market data from DB
    with store._conn() as db:
        row = db.execute(
            "SELECT player_id, name FROM market_snapshots WHERE LOWER(name) LIKE ? ORDER BY captured_at DESC LIMIT 1",
            (f"%{name_query}%",),
        ).fetchone()
    if row:
        try:
            return client.get_player(row["player_id"])
        except Exception:
            return {"id": row["player_id"], "name": row["name"], "position": 3, "price": 0}
    return None


def handle_command(cmd: str, args: str, client: BiwengerClient, fixture_map: dict) -> str:
    """Dispatch a command and return a Telegram-formatted reply."""

    if cmd == "/help":
        return HELP_TEXT

    elif cmd == "/status":
        try:
            squad_raw = client.get_squad()
            balance = squad_raw.get("balance", 0)
            pts = squad_raw.get("points", 0)
            n_players = len(squad_raw.get("all_players", []))
            standings = store.get_standings()
            my_entry = next(
                (u for u in standings if u.get("user_id") == settings.biwenger_user_id), None
            )
            pos_line = f"📊 Posición: *{my_entry['position']}ª*" if my_entry else ""
            round_info = client.get_next_round()
            deadline_line = ""
            if round_info and round_info.get("deadline_utc"):
                from api.models import RoundInfo
                ri = RoundInfo(**round_info)
                h = ri.hours_until_deadline
                if h is not None and h > 0:
                    deadline_line = f"⏰ {round_info['name']} cierra en *{h:.0f}h*"
            lines = [
                "📊 *Estado actual*",
                f"💰 Balance: €{balance:,.0f}",
                f"🏆 Puntos: {pts}",
                f"👥 Plantilla: {n_players} jugadores",
            ]
            if pos_line:
                lines.append(pos_line)
            if deadline_line:
                lines.append(deadline_line)
            return "\n".join(lines)
        except Exception as exc:
            return f"⚠️ Error: {exc}"

    elif cmd == "/squad":
        try:
            squad_raw = client.get_squad()
            players = [Player(**p) for p in squad_raw.get("all_players", [])]
            xi_ids = set(squad_raw.get("lineup_player_ids", []))
            lines = ["👥 *Tu plantilla*\n"]
            for pos, label in [(1, "🧤"), (2, "🛡"), (3, "⚙️"), (4, "🔴")]:
                pp = [p for p in players if p.position == pos]
                if not pp:
                    continue
                for p in pp:
                    diff = fixture_map.get(p.team.slug if p.team else "", 1.0)
                    score = score_player(p, diff)
                    xi = "⭐" if p.id in xi_ids else "🪑"
                    lines.append(
                        f"{xi} {label} *{p.name}* {p.trend_emoji} — "
                        f"{score:.1f}pts pred | €{p.price/1e6:.2f}M"
                    )
            return "\n".join(lines)
        except Exception as exc:
            return f"⚠️ Error: {exc}"

    elif cmd == "/market":
        try:
            from engine.market_scanner import scan_market
            squad_raw = client.get_squad()
            my_ids = {p["id"] for p in (squad_raw.get("all_players") or []) if p.get("id")}
            opps = scan_market(client, my_ids, fixture_map)[:5]
            if not opps:
                return "📊 No hay oportunidades destacadas en el mercado ahora mismo."
            lines = ["🛒 *Top 5 mercado*\n"]
            for i, opp in enumerate(opps, 1):
                p = opp["player"]
                src = "🏪" if opp["is_free_pool"] else "👤"
                lines.append(
                    f"{i}. *{p.name}* ({p.position_name}) {src}\n"
                    f"   €{opp['market_price']/1e6:.2f}M | {opp['predicted_points']:.1f}pts | "
                    f"{opp['value_efficiency']:.2f}pts/M"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"⚠️ Error: {exc}"

    elif cmd == "/watchlist":
        wl = store.get_watchlist()
        if not wl:
            return "📋 Tu watchlist está vacía. Usa /watch <nombre> para añadir jugadores."
        lines = ["📋 *Tu watchlist*\n"]
        for entry in wl:
            lines.append(
                f"• *{entry['player_name']}* — {entry.get('reason') or 'sin nota'}\n"
                f"  Añadido: {entry['added_at'][:10]}"
            )
        return "\n".join(lines)

    elif cmd == "/watch":
        if not args:
            return "Uso: /watch <nombre del jugador>"
        raw = _fuzzy_find_player(args, client)
        if not raw:
            return f"❌ No encontré ningún jugador que coincida con *{args}*.\nPrueba con el apellido."
        pid = raw.get("id")
        name = raw.get("name", args)
        pos = raw.get("position", 3)
        team = (raw.get("team") or {}).get("name")
        price = raw.get("price", 0)
        store.add_to_watchlist(pid, name, pos, team, price, reason=f"añadido via /watch")
        return (
            f"✅ *{name}* añadido a tu watchlist.\n"
            f"Te avisaré si aparece en el mercado, baja de precio o supera 7 pts."
        )

    elif cmd == "/unwatch":
        if not args:
            return "Uso: /unwatch <nombre del jugador>"
        # Find in watchlist by name
        wl = store.get_watchlist()
        match = next(
            (e for e in wl if args.lower() in e["player_name"].lower()), None
        )
        if not match:
            return f"❌ *{args}* no está en tu watchlist."
        store.remove_from_watchlist(match["player_id"])
        return f"🗑 *{match['player_name']}* eliminado de tu watchlist."

    return f"❓ Comando desconocido: *{cmd}*. Usa /help."


def main() -> None:
    logger.info(f"👂 Bot listener starting (timeout {TIMEOUT}s)…")

    client = BiwengerClient()
    client.login()

    fixture_map: dict = {}
    try:
        fixture_map = refresh_fixtures(ssl_verify=settings.ssl_verify)
    except Exception:
        pass

    my_chat_id = resolve_chat_id()
    deadline = time.time() + TIMEOUT
    last_update_id = 0

    while time.time() < deadline:
        remaining = int(deadline - time.time())
        poll_timeout = min(30, remaining)
        if poll_timeout <= 0:
            break

        try:
            updates = _api("getUpdates", offset=last_update_id + 1, timeout=poll_timeout)
        except Exception as exc:
            logger.warning(f"getUpdates error: {exc}")
            time.sleep(5)
            continue

        for update in updates.get("result", []):
            last_update_id = update["update_id"]
            msg = update.get("message") or {}
            text = (msg.get("text") or "").strip()
            chat_id = str((msg.get("chat") or {}).get("id", ""))

            if not text or not text.startswith("/"):
                continue

            # Only respond to our chat
            if chat_id != str(my_chat_id):
                logger.debug(f"Ignoring message from unknown chat {chat_id}")
                continue

            parts = text.split(None, 1)
            cmd = parts[0].lower().split("@")[0]  # strip @botname suffix
            args = parts[1].strip() if len(parts) > 1 else ""

            logger.info(f"Command: {cmd!r} args={args!r}")
            reply = handle_command(cmd, args, client, fixture_map)
            send_message(reply)

    logger.info("👂 Listener finished.")


if __name__ == "__main__":
    main()
