"""
Heuristic player scorer — upgraded with fixture difficulty + minutes played.

Score formula:
  base  = 0.5 × fitness_avg + 0.3 × season_avg + 0.2 × minutes_pct/10
  score = base × fixture_difficulty × injury_penalty

fixture_difficulty: 0.65 (tough away) … 1.30 (easy home), default 1.0
minutes_pct: estimated % of minutes played this season (proxy for rotation risk)
injury_penalty: 0.0 if confirmed injured, 0.7 if doubt, 1.0 otherwise

XGBoost upgrade path: once 10+ jornadas of data are collected,
train `engine/xgb_trainer.py` and drop in the trained model here.
"""

from api.models import Player


def score_player(player: Player, fixture_difficulty: float = 1.0) -> float:
    """
    Predicted points for next jornada.

    Args:
        player: Player model instance.
        fixture_difficulty: Multiplier from engine.fixtures (0.65–1.30).
                            Defaults to 1.0 (neutral) when fixture data unavailable.
    """
    if player.games_played == 0:
        return 0.0

    injury_penalty = {
        "ok": 1.0,
        "doubt": 0.7,
        "injured": 0.0,
        "suspended": 0.0,
    }.get(player.status, 0.5)

    if injury_penalty == 0.0:
        return 0.0

    fitness_avg = player.fitness_avg
    season_avg = player.points_per_game

    # Minutes played proxy: (games_played / max expected games) capped at 100
    # La Liga has ~38 jornadas; a fully selected player averages ~30 by mid-season
    max_games = max(player.games_played, 10)
    minutes_pct = min(100.0, (player.games_played / max_games) * 100)

    base = (
        0.50 * fitness_avg
        + 0.30 * season_avg
        + 0.20 * (minutes_pct / 10)  # normalise to ~same scale as avg pts
    )

    return round(base * fixture_difficulty * injury_penalty, 3)


def value_efficiency(player: Player, fixture_difficulty: float = 1.0) -> float:
    """
    Value metric: predicted points per million euros.

    Higher = better bang for buck. Used by market_scanner to rank
    players and by transfer recommendations.
    """
    if player.price == 0:
        return 0.0
    return round(score_player(player, fixture_difficulty) / player.price_millions, 4)

