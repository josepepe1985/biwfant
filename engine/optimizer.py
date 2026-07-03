"""
PuLP MILP lineup optimizer.

Selects the best 11 players from a squad to maximise predicted points,
subject to formation constraints.  Tries every valid formation and
returns the one with the highest total predicted score.
"""

from __future__ import annotations

import pulp
from loguru import logger

from api.models import Player
from engine.scorer import score_player

# (GK, DEF, MID, FWD)
VALID_FORMATIONS: dict[str, tuple[int, int, int, int]] = {
    "3-4-3": (1, 3, 4, 3),
    "3-5-2": (1, 3, 5, 2),
    "4-3-3": (1, 4, 3, 3),
    "4-4-2": (1, 4, 4, 2),
    "4-5-1": (1, 4, 5, 1),
    "5-3-2": (1, 5, 3, 2),
    "5-4-1": (1, 5, 4, 1),
}


def optimize_lineup(
    players: list[Player],
    preferred_formation: str | None = None,
) -> tuple[list[Player], str, float]:
    """
    Returns (starting_xi, formation, predicted_total_points).

    If preferred_formation is given, only that formation is tried
    (faster for lineup-lock runs).  Otherwise all seven are tried and
    the best is returned.
    """
    if len(players) < 11:
        logger.warning(f"Only {len(players)} players — cannot build XI")
        return [], preferred_formation or "4-4-2", 0.0

    formations = (
        [preferred_formation]
        if preferred_formation and preferred_formation in VALID_FORMATIONS
        else list(VALID_FORMATIONS.keys())
    )

    best_xi: list[Player] = []
    best_formation = formations[0]
    best_score = -1.0

    for f in formations:
        xi, score = _solve(players, f)
        if xi and score > best_score:
            best_score = score
            best_xi = xi
            best_formation = f

    return best_xi, best_formation, round(best_score, 2)


def _solve(
    players: list[Player], formation: str
) -> tuple[list[Player], float]:
    """Solve ILP for a fixed formation. Returns (xi, total_score)."""
    reqs = VALID_FORMATIONS[formation]  # (GK, DEF, MID, FWD)

    prob = pulp.LpProblem(f"lineup_{formation}", pulp.LpMaximize)

    x = {p.id: pulp.LpVariable(f"x_{p.id}", cat="Binary") for p in players}
    scores = {p.id: score_player(p) for p in players}

    # Objective
    prob += pulp.lpSum(scores[p.id] * x[p.id] for p in players)

    # Exactly 11 starters
    prob += pulp.lpSum(x[p.id] for p in players) == 11

    # Per-position constraints
    for pos, req in enumerate(reqs, start=1):  # pos: 1=GK 2=DEF 3=MID 4=FWD
        pos_players = [p for p in players if p.position == pos]
        prob += pulp.lpSum(x[p.id] for p in pos_players) == req

    # Injured players cannot start
    for p in players:
        if not p.is_available:
            prob += x[p.id] == 0

    prob.solve(pulp.PULP_CBC_CMD(msg=0))

    if pulp.LpStatus[prob.status] != "Optimal":
        return [], 0.0

    selected = [p for p in players if pulp.value(x[p.id]) == 1]
    total = sum(scores[p.id] for p in selected)
    return selected, total
