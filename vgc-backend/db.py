"""SQLite schema and connection helpers.

One file, one database. Timestamps are integer epoch milliseconds so they line up
with the frontend's `Date.now()` without any conversion at the boundary.
"""

import os
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("VGC_DB", Path(__file__).parent / "vgc.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    created_at    INTEGER NOT NULL
);

-- Refresh tokens only. Access tokens are stateless JWTs and are never stored.
-- Only the SHA-256 of a refresh token is kept, so a leaked database does not
-- hand an attacker usable sessions.
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT    PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refresh_account ON refresh_tokens(account_id);

CREATE TABLE IF NOT EXISTS pokemon (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    species     TEXT    NOT NULL,
    nickname    TEXT    NOT NULL DEFAULT '',
    shiny       INTEGER NOT NULL DEFAULT 0,
    mark        TEXT    NOT NULL DEFAULT '',
    ribbon      TEXT    NOT NULL DEFAULT '',
    origin_game TEXT    NOT NULL DEFAULT '',
    tera_type   TEXT    NOT NULL DEFAULT '',
    ball        TEXT    NOT NULL DEFAULT '',
    level       INTEGER,
    nature      TEXT    NOT NULL DEFAULT '',
    ability     TEXT    NOT NULL DEFAULT '',
    ot          TEXT    NOT NULL DEFAULT '',
    language    TEXT    NOT NULL DEFAULT '',
    notes       TEXT    NOT NULL DEFAULT '',
    dex_id      INTEGER,
    art         TEXT,
    art_shiny   TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pokemon_account ON pokemon(account_id);

CREATE TABLE IF NOT EXISTS binders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    tradeable   INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_binders_account ON binders(account_id);

-- Deleting either side removes the filing, so a deleted Pokémon can't linger
-- as a dangling reference inside a binder.
CREATE TABLE IF NOT EXISTS binder_pokemon (
    binder_id  INTEGER NOT NULL REFERENCES binders(id) ON DELETE CASCADE,
    pokemon_id INTEGER NOT NULL REFERENCES pokemon(id) ON DELETE CASCADE,
    PRIMARY KEY (binder_id, pokemon_id)
);
CREATE INDEX IF NOT EXISTS idx_binder_pokemon_pk ON binder_pokemon(pokemon_id);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id    INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    recipient_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    body         TEXT    NOT NULL DEFAULT '',
    -- Opaque JSON blob from the client (a binder reference). The server never
    -- needs to understand binder internals for messaging to work.
    attachment   TEXT,
    created_at   INTEGER NOT NULL,
    read_at      INTEGER,
    CHECK (sender_id <> recipient_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender_id, recipient_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_inbox ON messages(recipient_id, read_at);
"""


def now_ms() -> int:
    """Current time as epoch milliseconds."""
    return int(time.time() * 1000)


def connect() -> sqlite3.Connection:
    """Open a connection with foreign keys enforced and dict-like rows."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # Off by default in SQLite; without it the REFERENCES clauses above do nothing.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# Paired releases used to be separate options. Rows written back then still hold a
# single version, which no longer matches anything the client offers — so they'd be
# unfilterable and would silently change on the next edit. Folding them onto the
# combined label fixes that in place.
ORIGIN_GAME_MERGES = {
    "Scarlet": "Scarlet / Violet",
    "Violet": "Scarlet / Violet",
    "Sword": "Sword / Shield",
    "Shield": "Sword / Shield",
    "Brilliant Diamond": "Brilliant Diamond / Shining Pearl",
    "Shining Pearl": "Brilliant Diamond / Shining Pearl",
    "Sun": "Sun / Moon",
    "Moon": "Sun / Moon",
    "Ultra Sun": "Ultra Sun / Ultra Moon",
    "Ultra Moon": "Ultra Sun / Ultra Moon",
    "Let's Go Pikachu": "Let's Go Pikachu / Eevee",
    "Let's Go Eevee": "Let's Go Pikachu / Eevee",
    # renamed so " / " means "release pair" everywhere and nothing else
    "HOME / transferred": "HOME (transferred)",
}


def merge_paired_origin_games(conn: sqlite3.Connection) -> int:
    """Fold single-version origins onto their release pair. Idempotent.

    Only touches exact legacy labels, so anything already combined — or any value
    we don't recognise — is left alone rather than coerced.
    """
    changed = 0
    for legacy, combined in ORIGIN_GAME_MERGES.items():
        cur = conn.execute(
            "UPDATE pokemon SET origin_game = ? WHERE origin_game = ?", (combined, legacy)
        )
        changed += cur.rowcount
    return changed


def init_db() -> None:
    """Create tables if they don't exist, then run data migrations.

    Safe to call on every startup; each step is idempotent.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        merge_paired_origin_games(conn)
