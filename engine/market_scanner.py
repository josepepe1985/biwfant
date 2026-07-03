"""
Market scanner — finds undervalued players and sell candidates.
"""

from __future__ import annotations

from loguru import logger

from api.client import BiwengerClient
from api.models import Player
from engine.scorer import score_player, value_efficiency


def scan_market(
    client: BiwengerClient, my_squad_ids: set[int]
) -> list[dict]:
    """
    Fetch all active market sales, enrich each player with full stats,
    and return a list of opportunities sorted by value_efficiency desc.

    Only players NOT already in our squad are returned.
    """
    market = client.get_market()
    sales = market.get("sales", [])

    opportunities: list[dict] = []

    for sale in sales:
        player_id = (sale.get("player") or {}).get("id")
        if not player_id or player_id in my_squad_ids:
            continue

        try:
            raw = client.get_player(player_id)
            player = Player(**raw)
        except Exception as exc:
            logger.warning(f"Could not fetch player {player_id}: {exc}")
            continue

        market_price = sale.get("price", player.price)
        # Score using market price (the actual cost if we buy)
        priced = player.model_copy(update={"price": market_price})
        eff = value_efficiency(priced)
        predicted = score_player(priced)

        seller = sale.get("user") or {}
        seller_user_id = seller.get("id") if seller else None

        opportunities.append(
            {
                "player": player,
                "market_price": market_price,
                "seller_user_id": seller_user_id,
                "is_free_pool": seller_user_id is None,
                "value_efficiency": eff,
                "predicted_points": predicted,
                "price_trend": player.price_trend,
                "until": sale.get("until"),
            }
        )

    return sorted(opportunities, key=lambda o: o["value_efficiency"], reverse=True)


def find_sell_candidates(
    players: list[Player], buy_opportunities: list[dict]
) -> list[dict]:
    """
    Identify our squad players worth listing for sale.

    Criteria (≥2 triggers):
    - Price falling
    - Low value efficiency (< 1.5 pts/M) after ≥5 games
    - Poor recent form (fitness_avg < 2.0) after ≥5 games

    Only recommends selling if a clearly better alternative is available
    on the market (1.3× our player's efficiency).
    """
    if not buy_opportunities:
        return []

    best_market_eff = buy_opportunities[0]["value_efficiency"]
    candidates: list[dict] = []

    for player in players:
        eff = value_efficiency(player)
        triggers: list[str] = []

        if player.price_trend == "falling":
            triggers.append("precio bajando")
        if player.games_played >= 5 and eff < 1.5:
            triggers.append(f"bajo valor ({eff:.2f} pts/M)")
        if player.games_played >= 5 and player.fitness_avg < 2.0:
            triggers.append(f"mala forma reciente ({player.fitness_avg:.1f} pts/j)")

        if len(triggers) >= 2 and best_market_eff > eff * 1.3:
            # List at 5 % premium over current market price
            ask_price = max(player.price, int(player.price * 1.05))
            candidates.append(
                {
                    "player": player,
                    "value_efficiency": eff,
                    "triggers": triggers,
                    "ask_price": ask_price,
                }
            )

    return sorted(candidates, key=lambda c: c["value_efficiency"])
