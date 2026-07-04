#!/usr/bin/env python3
"""
Weekly performance report.

Runs every Monday 10:00 UTC after jornada results are published.
Compares predicted vs actual points, tracks model accuracy,
and reports league standings with rivalry context.
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
from engine.scorer import score_player
from engine.fixtures import refresh_fixtures
from bot.telegram_bot import send_message


def _get_last_jornada_scores(client: BiwengerClient, players: list[Player]) -> dict[int, float]:
    """
    Extract last jornada actual scores from fitness field.
    fitness[-1] is the most recent jornada result.
    Returns dict[player_id → actual_pts].
    """
    scores: dict[int, float] = {}
    for p in players:
        fitness = p.fitness
        last = next(
            (f for f in reversed(fitness) if isinstance(f, (int, float))), None
        )
        if last is not None:
            scores[p.id] = float(last)
    return scores


def _get_current_jornada_number(client: BiwengerClient) -> int | None:
    """Best-effort current jornada number from rounds API."""
    try:
        rounds = client.get_rounds_with_dates()
        # Find last finished round
        for r in reversed(rounds):
            if r.get("status") == "finished":
                return r.get("id")
    except Exception as exc:
        logger.warning(f"Could not determine jornada number: {exc}")
    return None


def build_squad_performance_section(
    players: list[Player],
    actual_scores: dict[int, float],
    xi_ids: set[int],
    jornada: int | None,
    fixture_map: dict,
) -> list[str]:
    """Build the squad performance block of the report."""
    lines = ["⚽ *Rendimiento de la plantilla*\n"]

    scored = [
        (p, actual_scores[p.id])
        for p in players
        if p.id in actual_scores
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    total_xi_pts = sum(
        s for p, s in scored if p.id in xi_ids
    )
    lines.append(f"📊 Puntos totales XI: *{total_xi_pts:.0f}*\n")

    lines.append("🏆 *Top scorers:*")
    for p, pts in scored[:5]:
        xi_flag = "⭐" if p.id in xi_ids else "🪑"
        predicted = score_player(p, fixture_map.get(p.team.slug if p.team else "", 1.0))
        delta = pts - predicted
        delta_str = f"({'+'if delta>=0 else ''}{delta:.1f} vs pred)"
        lines.append(f"  {xi_flag} *{p.name}* — {pts:.0f} pts {delta_str}")

    if len(scored) > 5:
        lines.append("\n😬 *Peores rendimientos:*")
        for p, pts in scored[-3:]:
            xi_flag = "⭐" if p.id in xi_ids else "🪑"
            lines.append(f"  {xi_flag} {p.name} — {pts:.0f} pts")

    return lines


def build_model_accuracy_section(
    players: list[Player],
    actual_scores: dict[int, float],
    jornada: int | None,
    fixture_map: dict,
) -> list[str]:
    """Save results and compute model accuracy."""
    lines = ["\n🎯 *Precisión del modelo*\n"]

    xi_ids: set[int] = set()  # we don't have this here, skip

    for p in players:
        if p.id not in actual_scores:
            continue
        diff = fixture_map.get(p.team.slug if p.team else "", 1.0)
        predicted = score_player(p, diff)
        actual = actual_scores[p.id]
        store.save_jornada_result(
            player_id=p.id,
            name=p.name,
            position=p.position,
            jornada=jornada or 0,
            actual_pts=actual,
            predicted_pts=predicted,
        )

    accuracy = store.get_model_accuracy(last_n_jornadas=5)
    if accuracy["mae"] is not None:
        lines.append(f"  MAE (últimas 5 jornadas): *{accuracy['mae']:.2f} pts*")
        lines.append(
            f"  Dirección correcta: *{accuracy['direction_accuracy']:.0%}* "
            f"({accuracy['n_samples']} muestras)"
        )
    else:
        lines.append("  Datos insuficientes aún (necesita ≥5 jornadas)")

    return lines


def build_standings_section(
    client: BiwengerClient,
    my_user_id: int,
) -> list[str]:
    """Build league standings block with rivalry context."""
    lines = ["\n📊 *Clasificación — Pez loco*\n"]

    try:
        raw_standings = client.get_standings()
    except Exception as exc:
        logger.warning(f"Could not fetch standings: {exc}")
        return lines + ["  (No disponible)"]

    store.save_standings(raw_standings)

    my_pos = None
    for u in raw_standings:
        if u.get("id") == my_user_id:
            my_pos = u.get("position", 0)
            my_pts = u.get("points", 0)
            break

    # Show top 5 + own position
    shown_ids: set = set()
    for u in raw_standings[:5]:
        uid = u.get("id")
        shown_ids.add(uid)
        marker = "👑" if u.get("position") == 1 else ("⚡" if uid == my_user_id else "  ")
        lines.append(
            f"  {marker} {u.get('position')}. *{u.get('name')}* "
            f"— {u.get('points', 0)} pts"
        )

    if my_pos and my_pos > 5:
        lines.append("  ...")
        my_data = next((u for u in raw_standings if u.get("id") == my_user_id), None)
        if my_data:
            lines.append(
                f"  ⚡ {my_pos}. *{my_data.get('name')}* — {my_pts} pts"
            )
        # Nearest rivals
        rival_above = next(
            (u for u in raw_standings if u.get("position") == my_pos - 1), None
        )
        rival_below = next(
            (u for u in raw_standings if u.get("position") == my_pos + 1), None
        )
        if rival_above:
            gap = rival_above.get("points", 0) - my_pts
            lines.append(f"\n  🎯 Superar a *{rival_above['name']}*: +{gap} pts")
        if rival_below:
            gap = my_pts - rival_below.get("points", 0)
            lines.append(f"  🛡 Ventaja sobre *{rival_below['name']}*: +{gap} pts")

    leader = raw_standings[0] if raw_standings else None
    if leader and my_pos and my_pos > 1:
        gap_to_leader = leader.get("points", 0) - my_pts
        lines.append(f"\n  Gap al líder *{leader['name']}*: -{gap_to_leader} pts")

    return lines


def main() -> None:
    logger.info("📋 Weekly report starting…")

    client = BiwengerClient()
    client.login()

    squad_raw = client.get_squad()
    players: list[Player] = [Player(**p) for p in squad_raw.get("all_players", [])]
    balance: int = squad_raw.get("balance", 0)
    total_pts: int = squad_raw.get("points", 0)
    xi_ids: set[int] = set(squad_raw.get("lineup_player_ids", []))

    fixture_map: dict = {}
    try:
        fixture_map = refresh_fixtures(ssl_verify=settings.ssl_verify)
    except Exception:
        pass

    jornada = _get_current_jornada_number(client)
    actual_scores = _get_last_jornada_scores(client, players)

    logger.info(f"Jornada: {jornada} | Players with scores: {len(actual_scores)}")

    if not actual_scores:
        send_message(
            "📋 *Informe semanal*\n\n"
            "⚠️ No se encontraron puntuaciones de la última jornada. "
            "Puede que los resultados no estén publicados aún."
        )
        return

    header = [
        "📋 *Informe semanal — Vampiros United*\n",
        f"💰 Balance: €{balance:,.0f} | 📊 Puntos totales: {total_pts}\n",
    ]

    perf_lines = build_squad_performance_section(
        players, actual_scores, xi_ids, jornada, fixture_map
    )
    acc_lines = build_model_accuracy_section(
        players, actual_scores, jornada, fixture_map
    )
    standings_lines = build_standings_section(client, settings.biwenger_user_id)

    full_report = "\n".join(header + perf_lines + acc_lines + standings_lines)
    send_message(full_report)
    logger.info("✅ Weekly report sent.")


if __name__ == "__main__":
    main()
