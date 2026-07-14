"""Transfer recommendation message formatting."""

from __future__ import annotations

from api.models import Player

_POS_ORDER = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def _fitness_str(player: "Player | None") -> str:
    """Return a compact last-5-games string, e.g. '3·7·0·2·5'."""
    if player is None or not player.fitness:
        return ""
    last5 = player.fitness[-5:]
    parts = [str(f) if isinstance(f, int) else "-" for f in last5]
    return "〔" + "·".join(parts) + "〕"


def _evaluate_offer(amount: int, player: "Player | None") -> tuple[str, str]:
    """
    Return (verdict_emoji, reasoning) for a sent offer.

    Verdicts: 🟢 Buena oferta / 🟡 Aceptable / 🔴 Mala oferta
    """
    if player is None:
        return "❓", "Sin datos del jugador"

    market_val = player.price
    if market_val <= 0:
        return "❓", "Precio de mercado desconocido"

    ratio = amount / market_val  # 1.0 = at market price

    reasons: list[str] = []

    # Price vs market value
    if ratio <= 0.90:
        price_verdict = "🟢"
        reasons.append(f"oferta {(1-ratio)*100:.0f}% por debajo del valor de mercado")
    elif ratio <= 1.05:
        price_verdict = "🟡"
        reasons.append(f"oferta ajustada al valor de mercado (€{market_val:,.0f})")
    else:
        price_verdict = "🔴"
        reasons.append(f"oferta {(ratio-1)*100:.0f}% por encima del valor de mercado")

    # Price trend
    if player.price_trend == "falling":
        reasons.append("precio bajando 📉 — esperar puede ser mejor")
        if price_verdict == "🟢":
            price_verdict = "🟡"
    elif player.price_trend == "rising":
        reasons.append("precio subiendo 📈 — comprar ahora tiene sentido")
        if price_verdict == "🟡":
            price_verdict = "🟢"

    # Recent form
    if player.games_played >= 3:
        avg = player.fitness_avg
        if avg >= 6:
            reasons.append(f"buena forma reciente ({avg:.1f} pts/j)")
        elif avg <= 2:
            reasons.append(f"mala forma reciente ({avg:.1f} pts/j)")
            if price_verdict == "🟢":
                price_verdict = "🟡"

    # Value efficiency
    if player.games_played >= 5 and player.price > 0:
        eff = player.points_per_game / (player.price / 1_000_000)
        if eff >= 3.0:
            reasons.append(f"alto rendimiento ({eff:.1f} pts/M)")
        elif eff < 1.5:
            reasons.append(f"bajo rendimiento ({eff:.1f} pts/M)")
            if price_verdict != "🔴":
                price_verdict = "🟡"

    return price_verdict, " · ".join(reasons) if reasons else "Sin información suficiente"


def build_market_overview_message(
    market_raw: dict,
    offers_raw: dict,
    my_squad_ids: set[int],
    player_map: "dict[int, Player] | None" = None,
) -> str:
    """
    Format a full market overview for Telegram:
      - All active listings grouped by position (with last-5 points)
      - Offers I have sent (pending bids)
      - Offers received on players I'm selling

    player_map: optional dict[player_id → full Player object] used to show
                the last 5 jornada scores next to each listing.
    """
    player_map = player_map or {}
    sales: list[dict] = market_raw.get("sales", [])
    sent: list[dict] = offers_raw.get("sent", [])
    received: list[dict] = offers_raw.get("received", [])

    lines = ["🏪 *Mercado completo*\n"]

    # ── All active listings ──────────────────────────────────────────────────
    if sales:
        # Group by position — use player_map for position since raw market only has player ID
        by_pos: dict[int, list[dict]] = {1: [], 2: [], 3: [], 4: []}
        other: list[dict] = []
        for s in sales:
            p_raw = s.get("player") or {}
            pid = p_raw.get("id")
            full_player = player_map.get(pid) if pid else None
            pos = (full_player.position if full_player else None) or p_raw.get("position", 0)
            if pos in by_pos:
                by_pos[pos].append(s)
            else:
                other.append(s)

        lines.append(f"📋 *Jugadores en el mercado ({len(sales)}):*")
        for pos, label in _POS_ORDER.items():
            group = by_pos.get(pos, [])
            if not group:
                continue
            lines.append(f"\n_{label}_")
            for s in group:
                p_raw = s.get("player") or {}
                pid = p_raw.get("id")
                full_player = player_map.get(pid) if pid else None
                seller = s.get("user") or {}
                seller_name = seller.get("name", "Pool libre") if seller else "Pool libre"
                # prefer full player name from player_map, fall back to raw
                name = (full_player.name if full_player else None) or p_raw.get("name", "?")
                price = s.get("price", 0)
                in_squad = "👤 " if pid in my_squad_ids else ""
                fitness = _fitness_str(full_player)
                lines.append(
                    f"  {in_squad}*{name}* — €{price:,.0f} · {seller_name}"
                    + (f"\n     ↳ últimos 5: {fitness}" if fitness else "")
                )
        if other:
            lines.append("\n_Otros_")
            for s in other:
                p_raw = s.get("player") or {}
                pid = p_raw.get("id")
                full_player = player_map.get(pid) if pid else None
                name = (full_player.name if full_player else None) or p_raw.get("name", "?")
                price = s.get("price", 0)
                fitness = _fitness_str(full_player)
                lines.append(
                    f"  *{name}* — €{price:,.0f}"
                    + (f"\n     ↳ últimos 5: {fitness}" if fitness else "")
                )
    else:
        lines.append("📋 No hay jugadores en el mercado ahora mismo.")

    lines.append("")

    # ── Offers I sent ────────────────────────────────────────────────────────
    if sent:
        lines.append(f"📤 *Mis ofertas enviadas ({len(sent)}):*")
        for o in sent:
            players_req = o.get("requestedPlayers") or o.get("players") or []
            pid = (players_req[0].get("id") if players_req and isinstance(players_req[0], dict) else players_req[0] if players_req and isinstance(players_req[0], int) else None)
            full_player = player_map.get(pid) if pid else None
            p_name = _offer_player_name(o, player_map)
            amount = o.get("amount", 0)
            to_user = (o.get("to") or {}).get("name", "Pool libre") if isinstance(o.get("to"), dict) else "Pool libre"
            status = o.get("status", "")
            status_icon = {"waiting": "⏳", "pending": "⏳", "accepted": "✅", "rejected": "❌"}.get(status, "🔄")
            verdict, reasoning = _evaluate_offer(amount, full_player)
            fitness = _fitness_str(full_player)
            lines.append(f"  {status_icon} *{p_name}* — €{amount:,.0f} → {to_user}")
            lines.append(f"     ↳ {verdict} {reasoning}")
            if fitness:
                lines.append(f"     ↳ últimos 5: {fitness}")
    else:
        lines.append("📤 Sin ofertas enviadas.")

    lines.append("")

    # ── Offers received ──────────────────────────────────────────────────────
    if received:
        lines.append(f"📥 *Ofertas recibidas ({len(received)}):*")
        for o in received:
            p_name = _offer_player_name(o, player_map)
            amount = o.get("amount", 0)
            from_user = (o.get("from") or {}).get("name", "?") if isinstance(o.get("from"), dict) else "?"
            status = o.get("status", "")
            status_icon = {"waiting": "⏳", "pending": "⏳", "accepted": "✅", "rejected": "❌"}.get(status, "🔄")
            lines.append(f"  {status_icon} *{p_name}* — €{amount:,.0f} ← {from_user}")
    else:
        lines.append("📥 Sin ofertas recibidas.")

    return "\n".join(lines)



def _offer_player_name(offer: dict, player_map: "dict") -> str:
    """Resolve player name from an offer's requestedPlayers list."""
    players_req = offer.get("requestedPlayers") or offer.get("players") or []
    if not players_req:
        return "?"
    first = players_req[0]
    if isinstance(first, dict):
        pid = first.get("id")
        name = first.get("name")
        if name:
            return name
        if pid and pid in player_map:
            return player_map[pid].name
        return f"ID {pid}" if pid else "?"
    if isinstance(first, int):
        return player_map[first].name if first in player_map else f"ID {first}"
    return "?"


def build_transfers_message(
    sell_candidates: list[dict],
    buy_opportunities: list[dict],
    balance: int,
    llm_advice: dict | None = None,
    market_summary: str | None = None,
) -> str:
    """Format a Telegram-ready markdown message for market recommendations."""
    lines = [
        "💸 *Recomendaciones de mercado*\n",
        f"💰 Balance disponible: *€{balance:,.0f}*\n",
    ]

    # LLM advice block (if available)
    if llm_advice:
        rec = llm_advice.get("recommendation", "HOLD")
        conf = llm_advice.get("confidence", 0.0)
        summary = llm_advice.get("summary_es", "")
        rec_emoji = {"SELL_AND_BUY": "🔄", "BUY_ONLY": "🟢", "SELL_ONLY": "🔴", "HOLD": "⏸"}.get(rec, "🤖")
        lines.append(f"{rec_emoji} *IA recomienda: {rec}* (confianza {conf:.0%})")
        if summary:
            lines.append(f"_{summary}_\n")

    if sell_candidates:
        lines.append("🔴 *Candidatos a vender:*")
        for i, c in enumerate(sell_candidates[:3], 1):
            p: Player = c["player"]
            reasons = ", ".join(c["triggers"])
            lines.append(f"  {i}. *{p.name}* ({p.position_name}) {p.trend_emoji}")
            lines.append(f"     ↳ {reasons}")
            lines.append(f"     ↳ Precio sugerido: €{c['ask_price']:,.0f}")
        lines.append("")

    if buy_opportunities:
        lines.append("🟢 *Oportunidades de compra:*")
        for i, opp in enumerate(buy_opportunities[:3], 1):
            p = opp["player"]
            source = "🏪 Pool libre" if opp["is_free_pool"] else "👤 Rival"
            diff = opp.get("fixture_difficulty", 1.0)
            diff_emoji = "🟢" if diff >= 1.1 else ("🔴" if diff <= 0.8 else "🟡")
            lines.append(
                f"  {i}. *{p.name}* ({p.position_name}) {p.trend_emoji} {source} {diff_emoji}"
            )
            lines.append(
                f"     ↳ €{opp['market_price']:,.0f} | "
                f"{opp['predicted_points']:.1f} pts | "
                f"{opp['value_efficiency']:.2f} pts/M"
            )
        lines.append("")

    if market_summary:
        lines.append(f"🤖 _{market_summary}_\n")

    if not sell_candidates and not buy_opportunities:
        lines.append("✅ No hay recomendaciones de mercado en este momento.")

    return "\n".join(lines)

