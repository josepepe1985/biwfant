"""Transfer recommendation message formatting."""

from __future__ import annotations

from api.models import Player


def build_transfers_message(
    sell_candidates: list[dict],
    buy_opportunities: list[dict],
    balance: int,
) -> str:
    """Format a Telegram-ready markdown message for market recommendations."""
    lines = [
        "💸 *Recomendaciones de mercado*\n",
        f"💰 Balance disponible: *€{balance:,.0f}*\n",
    ]

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
            lines.append(
                f"  {i}. *{p.name}* ({p.position_name}) {p.trend_emoji} {source}"
            )
            lines.append(
                f"     ↳ €{opp['market_price']:,.0f} | "
                f"{opp['predicted_points']:.1f} pts | "
                f"{opp['value_efficiency']:.2f} pts/M"
            )
        lines.append("")

    if not sell_candidates and not buy_opportunities:
        lines.append("✅ No hay recomendaciones de mercado en este momento.")

    return "\n".join(lines)
