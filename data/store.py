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


# Initialise on import
init_db()
