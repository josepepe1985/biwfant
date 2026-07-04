"""
Fixture difficulty engine.

Fetches the upcoming La Liga jornada fixtures and assigns each team a
difficulty multiplier (0.7 = tough away, 1.0 = neutral, 1.3 = easy home).

Data source: Biwenger's public competition API (cf.biwenger.com).
Falls back to a static tier table if the API is unreachable.
Results are cached in SQLite for 24h.
"""

from __future__ import annotations

import requests
from loguru import logger

from data.store import get_fixture, save_fixture

# Static strength tiers for La Liga 2024-25 (lower = stronger defensively)
# Used as fallback when live data unavailable.
_TIER: dict[str, int] = {
    "real-madrid": 1, "barcelona": 1, "atletico-madrid": 1,
    "athletic-bilbao": 2, "real-sociedad": 2, "villarreal": 2,
    "real-betis": 2, "osasuna": 2, "girona": 2, "mallorca": 2,
    "sevilla": 3, "celta-vigo": 3, "getafe": 3, "alaves": 3,
    "rayo-vallecano": 3, "las-palmas": 3, "leganes": 3,
    "espanyol": 4, "real-valladolid": 4, "valencia": 4,
}

# Difficulty multiplier by (opponent_tier, home_or_away)
_DIFFICULTY: dict[tuple[int, str], float] = {
    (1, "home"): 0.75, (1, "away"): 0.65,
    (2, "home"): 0.90, (2, "away"): 0.80,
    (3, "home"): 1.10, (3, "away"): 1.00,
    (4, "home"): 1.25, (4, "away"): 1.15,
}

_BIWENGER_ROUNDS_URL = (
    "https://cf.biwenger.com/api/v3/competitions/la-liga/rounds"
    "?fields=rounds(id,name,status,games(home,away,homeScore,awayScore))"
)


def _fetch_next_jornada() -> list[dict] | None:
    """
    Fetch upcoming round fixtures from Biwenger public API.
    Returns list of game dicts or None on failure.
    """
    try:
        resp = requests.get(_BIWENGER_ROUNDS_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", {}).get("rounds", [])

        # Find first round that hasn't started (no scores yet)
        for rnd in data:
            games = rnd.get("games", [])
            unplayed = [g for g in games if g.get("homeScore") is None]
            if unplayed:
                logger.info(f"Next jornada: {rnd.get('name')} ({len(unplayed)} games)")
                return unplayed, rnd.get("id")
    except Exception as exc:
        logger.warning(f"Fixture fetch failed: {exc}")
    return None, None


def _slug(team: dict) -> str:
    return team.get("slug", team.get("name", "unknown").lower().replace(" ", "-"))


def _difficulty_from_tier(opponent_slug: str, is_home: bool) -> float:
    tier = _TIER.get(opponent_slug, 3)
    venue = "home" if is_home else "away"
    return _DIFFICULTY.get((tier, venue), 1.0)


def refresh_fixtures(ssl_verify: bool = True) -> dict[str, float]:
    """
    Refresh fixture difficulty for all La Liga teams and cache in SQLite.
    Returns dict[team_slug → difficulty_multiplier].
    """
    try:
        resp = requests.get(_BIWENGER_ROUNDS_URL, timeout=10, verify=ssl_verify)
        resp.raise_for_status()
        data = resp.json().get("data", {}).get("rounds", [])
    except Exception as exc:
        logger.warning(f"Cannot fetch fixtures, using static tiers: {exc}")
        return _build_static_difficulties()

    # Find the next unplayed round
    next_games: list[dict] = []
    jornada_id: int | None = None
    for rnd in data:
        games = rnd.get("games", [])
        unplayed = [g for g in games if g.get("homeScore") is None]
        if unplayed:
            next_games = unplayed
            jornada_id = rnd.get("id")
            logger.info(f"Fixture refresh: round {rnd.get('name')}, {len(unplayed)} unplayed games")
            break

    if not next_games:
        logger.info("No upcoming fixtures found, using static tiers")
        return _build_static_difficulties()

    result: dict[str, float] = {}
    for game in next_games:
        home = game.get("home", {})
        away = game.get("away", {})
        home_slug = _slug(home)
        away_slug = _slug(away)

        # Home team faces away opponent
        diff_home = _difficulty_from_tier(away_slug, is_home=True)
        diff_away = _difficulty_from_tier(home_slug, is_home=False)

        result[home_slug] = diff_home
        result[away_slug] = diff_away

        save_fixture(home_slug, home.get("name", home_slug), diff_home,
                     away.get("name", away_slug), is_home=True, jornada=jornada_id)
        save_fixture(away_slug, away.get("name", away_slug), diff_away,
                     home.get("name", home_slug), is_home=False, jornada=jornada_id)

    logger.info(f"Fixture cache updated for {len(result)} teams")
    return result


def _build_static_difficulties() -> dict[str, float]:
    """Return static neutral difficulty for all known teams."""
    return {slug: 1.0 for slug in _TIER}


def get_team_difficulty(team_slug: str) -> float:
    """
    Return difficulty multiplier for a team's next fixture.
    Checks SQLite cache first (24h TTL), then static fallback.
    """
    cached = get_fixture(team_slug, max_age_hours=24)
    if cached != 1.0:
        return cached
    # Static fallback based on opponent avg tier (medium difficulty assumed)
    return 1.0
