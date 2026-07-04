"""
SQLite persistence layer.

Stores per-jornada snapshots of players, market data, and bot decisions.
This time-series data is used for ML training and price trend analysis.

DB location: data/biwfant.db (relative to repo root, committed to git).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path(__file__).parent / "biwfant.db"


def _conn() -> sqlite3.Connection:
    """Open (or create) the DB with WAL mode for safe concurrent access."""
    db = sqlite3.connect(_DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db() -> None:
    """Create tables if they don't exist. Safe to call repeatedly."""
    with _conn() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS player_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id       INTEGER NOT NULL,
                name            TEXT    NOT NULL,
                position        INTEGER NOT NULL,
                team_name       TEXT,
                price           INTEGER NOT NULL,
                price_increment INTEGER NOT NULL DEFAULT 0,
                points          INTEGER NOT NULL DEFAULT 0,
                games_played    INTEGER NOT NULL DEFAULT 0,
                fitness_json    TEXT,           -- JSON list of last-5 scores
                status          TEXT,
                jornada         INTEGER,
                captured_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id       INTEGER NOT NULL,
                name            TEXT,
                market_price    INTEGER NOT NULL,
                seller_type     TEXT,           -- 'free_pool' | 'user'
                value_efficiency REAL,
                predicted_pts   REAL,
                jornada         INTEGER,
                captured_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bot_decisions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type     TEXT    NOT NULL, -- 'lineup'|'sell'|'buy'|'skip'
                player_id       INTEGER,
                player_name     TEXT,
                reasoning       TEXT,            -- LLM summary or heuristic reason
                confidence      REAL,
                confirmed       INTEGER,         -- 1=yes 0=no NULL=not asked (dry-run)
                executed        INTEGER DEFAULT 0,
                jornada         INTEGER,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fixture_cache (
                team_slug       TEXT PRIMARY KEY,
                team_name       TEXT,
                difficulty      REAL NOT NULL DEFAULT 1.0,
                opponent        TEXT,
                is_home         INTEGER,
                jornada         INTEGER,
                cached_at       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jornada_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id       INTEGER NOT NULL,
                name            TEXT    NOT NULL,
                position        INTEGER NOT NULL,
                jornada         INTEGER NOT NULL,
                predicted_pts   REAL,
                actual_pts      REAL,
                delta           REAL,   -- actual - predicted
                was_in_xi       INTEGER DEFAULT 0,
                captured_at     TEXT NOT NULL,
                UNIQUE(player_id, jornada)
            );

            CREATE TABLE IF NOT EXISTS watchlist (
                player_id           INTEGER PRIMARY KEY,
                player_name         TEXT    NOT NULL,
                position            INTEGER,
                team_name           TEXT,
                price_at_add        INTEGER,
                added_at            TEXT NOT NULL,
                reason              TEXT,
                alert_on_market     INTEGER DEFAULT 1,
                alert_on_price_drop INTEGER DEFAULT 1,
                alert_on_score      INTEGER DEFAULT 1,
                score_threshold     REAL    DEFAULT 7.0
            );

            CREATE TABLE IF NOT EXISTS standings_cache (
                user_id         INTEGER PRIMARY KEY,
                user_name       TEXT    NOT NULL,
                position        INTEGER NOT NULL,
                points          INTEGER NOT NULL DEFAULT 0,
                team_value      INTEGER NOT NULL DEFAULT 0,
                cached_at       TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_jr_player_jornada
                ON jornada_results(player_id, jornada);

            CREATE INDEX IF NOT EXISTS idx_ps_player_jornada
                ON player_snapshots(player_id, jornada);
            CREATE INDEX IF NOT EXISTS idx_ms_player_jornada
                ON market_snapshots(player_id, jornada);
        """)


def save_player_snapshot(player_data: dict, jornada: int | None = None) -> None:
    """Persist one player's current stats."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        db.execute(
            """
            INSERT INTO player_snapshots
                (player_id, name, position, team_name, price, price_increment,
                 points, games_played, fitness_json, status, jornada, captured_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                player_data["id"],
                player_data["name"],
                player_data["position"],
                player_data.get("team_name"),
                player_data["price"],
                player_data.get("price_increment", 0),
                player_data["points"],
                player_data["games_played"],
                json.dumps(player_data.get("fitness", [])),
                player_data.get("status", "ok"),
                jornada,
                now,
            ),
        )


def save_market_snapshot(opportunity: dict, jornada: int | None = None) -> None:
    """Persist one market opportunity."""
    now = datetime.now(timezone.utc).isoformat()
    p = opportunity["player"]
    with _conn() as db:
        db.execute(
            """
            INSERT INTO market_snapshots
                (player_id, name, market_price, seller_type, value_efficiency,
                 predicted_pts, jornada, captured_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                p.id,
                p.name,
                opportunity["market_price"],
                "free_pool" if opportunity["is_free_pool"] else "user",
                opportunity["value_efficiency"],
                opportunity["predicted_points"],
                jornada,
                now,
            ),
        )


def save_decision(
    action_type: str,
    player_id: int | None,
    player_name: str | None,
    reasoning: str,
    confidence: float | None = None,
    confirmed: bool | None = None,
    executed: bool = False,
    jornada: int | None = None,
) -> None:
    """Record a bot decision for audit + ML feedback loop."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        db.execute(
            """
            INSERT INTO bot_decisions
                (action_type, player_id, player_name, reasoning, confidence,
                 confirmed, executed, jornada, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                action_type,
                player_id,
                player_name,
                reasoning,
                confidence,
                None if confirmed is None else int(confirmed),
                int(executed),
                jornada,
                now,
            ),
        )


def save_fixture(
    team_slug: str,
    team_name: str,
    difficulty: float,
    opponent: str | None,
    is_home: bool,
    jornada: int | None = None,
) -> None:
    """Upsert fixture difficulty for a team."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        db.execute(
            """
            INSERT INTO fixture_cache
                (team_slug, team_name, difficulty, opponent, is_home, jornada, cached_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(team_slug) DO UPDATE SET
                difficulty=excluded.difficulty,
                opponent=excluded.opponent,
                is_home=excluded.is_home,
                jornada=excluded.jornada,
                cached_at=excluded.cached_at
            """,
            (team_slug, team_name, difficulty, opponent, int(is_home), jornada, now),
        )


def get_fixture(team_slug: str, max_age_hours: int = 24) -> float:
    """Return cached fixture difficulty or 1.0 if stale/missing."""
    with _conn() as db:
        row = db.execute(
            "SELECT difficulty, cached_at FROM fixture_cache WHERE team_slug=?",
            (team_slug,),
        ).fetchone()
    if not row:
        return 1.0
    cached = datetime.fromisoformat(row["cached_at"])
    age_h = (datetime.now(timezone.utc) - cached).total_seconds() / 3600
    return row["difficulty"] if age_h < max_age_hours else 1.0


def get_price_7d_ago(player_id: int) -> int | None:
    """Return the player's price from ~7 days ago for trend calculation."""
    with _conn() as db:
        row = db.execute(
            """
            SELECT price FROM player_snapshots
            WHERE player_id=?
              AND captured_at < datetime('now','-6 days')
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (player_id,),
        ).fetchone()
    return row["price"] if row else None


def get_recent_decisions(limit: int = 20) -> list[dict]:
    """Return the most recent bot decisions for LLM context."""
    with _conn() as db:
        rows = db.execute(
            """
            SELECT action_type, player_name, reasoning, confirmed, executed, created_at
            FROM bot_decisions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------- jornada results

def save_jornada_result(
    player_id: int,
    name: str,
    position: int,
    jornada: int,
    actual_pts: float,
    predicted_pts: float | None = None,
    was_in_xi: bool = False,
) -> None:
    """Save actual vs predicted points for a completed jornada."""
    now = datetime.now(timezone.utc).isoformat()
    delta = (actual_pts - predicted_pts) if predicted_pts is not None else None
    with _conn() as db:
        db.execute(
            """
            INSERT INTO jornada_results
                (player_id, name, position, jornada, predicted_pts, actual_pts,
                 delta, was_in_xi, captured_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(player_id, jornada) DO UPDATE SET
                actual_pts=excluded.actual_pts,
                delta=excluded.delta,
                captured_at=excluded.captured_at
            """,
            (player_id, name, position, jornada, predicted_pts, actual_pts,
             delta, int(was_in_xi), now),
        )


def get_model_accuracy(last_n_jornadas: int = 5) -> dict:
    """
    Return model accuracy metrics over the last N jornadas.
    Only includes rows where both predicted and actual are non-null.
    """
    with _conn() as db:
        rows = db.execute(
            """
            SELECT predicted_pts, actual_pts, delta, was_in_xi
            FROM jornada_results
            WHERE predicted_pts IS NOT NULL
              AND actual_pts IS NOT NULL
              AND jornada >= (SELECT MAX(jornada) - ? FROM jornada_results)
            """,
            (last_n_jornadas,),
        ).fetchall()

    if not rows:
        return {"mae": None, "direction_accuracy": None, "n_samples": 0}

    import statistics
    deltas = [abs(r["delta"]) for r in rows]
    mae = statistics.mean(deltas)

    # Direction: predicted above/below avg matches actual above/below avg
    actuals = [r["actual_pts"] for r in rows]
    avg_actual = statistics.mean(actuals) if actuals else 0
    correct = sum(
        1 for r in rows
        if (r["predicted_pts"] >= avg_actual) == (r["actual_pts"] >= avg_actual)
    )
    direction_acc = correct / len(rows) if rows else 0

    return {
        "mae": round(mae, 2),
        "direction_accuracy": round(direction_acc, 3),
        "n_samples": len(rows),
    }


def get_latest_price(player_id: int) -> int | None:
    """Return most recent snapshotted price for a player."""
    with _conn() as db:
        row = db.execute(
            """
            SELECT price FROM player_snapshots
            WHERE player_id=?
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (player_id,),
        ).fetchone()
    return row["price"] if row else None


# ------------------------------------------------------------------- watchlist

def add_to_watchlist(
    player_id: int,
    player_name: str,
    position: int | None = None,
    team_name: str | None = None,
    price_at_add: int | None = None,
    reason: str = "",
    score_threshold: float = 7.0,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        db.execute(
            """
            INSERT INTO watchlist
                (player_id, player_name, position, team_name, price_at_add,
                 added_at, reason, score_threshold)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(player_id) DO UPDATE SET
                reason=excluded.reason,
                price_at_add=excluded.price_at_add,
                added_at=excluded.added_at
            """,
            (player_id, player_name, position, team_name, price_at_add,
             now, reason, score_threshold),
        )


def remove_from_watchlist(player_id: int) -> int:
    """Returns number of rows deleted (0 if not found)."""
    with _conn() as db:
        cur = db.execute("DELETE FROM watchlist WHERE player_id=?", (player_id,))
    return cur.rowcount


def get_watchlist() -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            """
            SELECT player_id, player_name, position, team_name, price_at_add,
                   added_at, reason, alert_on_market, alert_on_price_drop,
                   alert_on_score, score_threshold
            FROM watchlist ORDER BY added_at DESC
            """,
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ standings

def save_standings(users: list[dict]) -> None:
    """Upsert current league standings."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        for u in users:
            db.execute(
                """
                INSERT INTO standings_cache
                    (user_id, user_name, position, points, team_value, cached_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    user_name=excluded.user_name,
                    position=excluded.position,
                    points=excluded.points,
                    team_value=excluded.team_value,
                    cached_at=excluded.cached_at
                """,
                (
                    u.get("id") or u.get("user_id"),
                    u.get("name") or u.get("user_name", "?"),
                    u.get("position", 0),
                    u.get("points", 0),
                    u.get("teamValue") or u.get("team_value", 0),
                    now,
                ),
            )


def get_standings() -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM standings_cache ORDER BY position ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def lineup_confirmed_today() -> bool:
    """True if a lineup action was confirmed (executed=1) today."""
    with _conn() as db:
        row = db.execute(
            """
            SELECT id FROM bot_decisions
            WHERE action_type='lineup'
              AND executed=1
              AND created_at >= date('now')
            LIMIT 1
            """
        ).fetchone()
    return row is not None


# Initialise on import
init_db()
