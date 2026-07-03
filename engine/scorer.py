"""
Heuristic player scorer.

Uses fitness (last 5 jornada points) + season average + price trend.
No training data needed — works on day 1.

XGBoost upgrade path: once 10+ jornadas of data are collected,
train `engine/xgb_trainer.py` and drop in the trained model here.
"""

from api.models import Player


def score_player(player: Player) -> float:
    """
    Predicted points for next jornada.

    Formula:
      base = 0.6 * fitness_avg + 0.4 * season_avg
      score = base * trend_multiplier

    Rationale: recent form (fitness) outweighs season average 60/40.
    Trend multiplier rewards in-form players (+10%) and penalises
    declining ones (-10%).
    """
    if not player.is_available or player.games_played == 0:
        return 0.0

    fitness = player.fitness_avg        # avg last-5 jornada pts
    season_avg = player.points_per_game # full-season avg

    base = 0.6 * fitness + 0.4 * season_avg

    trend_mult = {"rising": 1.10, "stable": 1.00, "falling": 0.90}.get(
        player.price_trend, 1.0
    )

    return round(base * trend_mult, 3)


def value_efficiency(player: Player) -> float:
    """
    Value metric: predicted points per million euros.

    Higher = better bang for buck. Used by market_scanner to rank
    players and by transfer recommendations.
    """
    if player.price == 0:
        return 0.0
    return round(score_player(player) / player.price_millions, 4)
