"""SQLite persistence for the dm.luaisgame.com platform.

Everything that must survive a process restart lives here: accounts, sessions,
OAuth states, servers/channels/roles, DMs, messages, badges and vanity requests.

A single shared connection guarded by a lock is plenty for this workload and
keeps us dependency-free (no aiosqlite).
"""

import os
import sqlite3
import threading
import time

DB_FILE = None
_conn = None
_lock = threading.RLock()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL,
    email_lower   TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL DEFAULT '',
    username      TEXT NOT NULL,
    username_low  TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    avatar        TEXT NOT NULL DEFAULT '',
    bio           TEXT NOT NULL DEFAULT '',
    discord_id    TEXT UNIQUE,
    discord_name  TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    last_seen     REAL NOT NULL DEFAULT 0,
    presence      TEXT NOT NULL DEFAULT 'online',
    custom_status TEXT NOT NULL DEFAULT '',
    is_owner      INTEGER NOT NULL DEFAULT 0,
    banned        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    ip         TEXT NOT NULL DEFAULT '',
    ua         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS oauth_states (
    state      TEXT PRIMARY KEY,
    ts         REAL NOT NULL,
    redirect   TEXT NOT NULL DEFAULT '',
    next       TEXT NOT NULL DEFAULT '/',
    host       TEXT NOT NULL DEFAULT '',
    link_user  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS badges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    icon        TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#5865f2',
    created_at  REAL NOT NULL,
    created_by  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_badges (
    user_id    INTEGER NOT NULL,
    badge_id   INTEGER NOT NULL,
    granted_at REAL NOT NULL,
    granted_by INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, badge_id)
);

CREATE TABLE IF NOT EXISTS servers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER NOT NULL,
    name        TEXT NOT NULL,
    icon        TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    vanity      TEXT NOT NULL DEFAULT '',
    invite      TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_servers_vanity ON servers(vanity) WHERE vanity != '';

CREATE TABLE IF NOT EXISTS vanity_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER NOT NULL,
    requester   INTEGER NOT NULL,
    slug        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  REAL NOT NULL,
    decided_by  INTEGER NOT NULL DEFAULT 0,
    decided_at  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS server_members (
    server_id INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    nickname  TEXT NOT NULL DEFAULT '',
    joined_at REAL NOT NULL,
    PRIMARY KEY (server_id, user_id)
);

CREATE TABLE IF NOT EXISTS roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER NOT NULL,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL DEFAULT '#99aab5',
    icon        TEXT NOT NULL DEFAULT '',
    position    INTEGER NOT NULL DEFAULT 0,
    permissions INTEGER NOT NULL DEFAULT 0,
    hoist       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS member_roles (
    server_id INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    role_id   INTEGER NOT NULL,
    PRIMARY KEY (server_id, user_id, role_id)
);

CREATE TABLE IF NOT EXISTS channels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id  INTEGER,
    kind       TEXT NOT NULL DEFAULT 'text',
    name       TEXT NOT NULL DEFAULT '',
    position   INTEGER NOT NULL DEFAULT 0,
    topic      TEXT NOT NULL DEFAULT '',
    avatar     TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_channels_server ON channels(server_id);

CREATE TABLE IF NOT EXISTS channel_members (
    channel_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    joined_at  REAL NOT NULL,
    PRIMARY KEY (channel_id, user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  INTEGER NOT NULL,
    author_id   INTEGER NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]',
    created_at  REAL NOT NULL,
    edited_at   REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id, id);

CREATE TABLE IF NOT EXISTS attachments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id   INTEGER NOT NULL,
    filename   TEXT NOT NULL,
    size       INTEGER NOT NULL,
    mime       TEXT NOT NULL DEFAULT 'application/octet-stream',
    path       TEXT NOT NULL,
    message_id INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS invites (
    code      TEXT PRIMARY KEY,
    server_id INTEGER NOT NULL,
    uses      INTEGER NOT NULL DEFAULT 0
);
"""


def init(db_path: str) -> None:
    """Open (and migrate) the database. Safe to call more than once."""
    global DB_FILE, _conn
    with _lock:
        if _conn is not None:
            return
        DB_FILE = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        _conn = sqlite3.connect(db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def _db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("dm.db has not been initialised")
    return _conn


def rows(sql: str, params=()) -> list:
    with _lock:
        return [dict(r) for r in _db().execute(sql, params).fetchall()]


def row(sql: str, params=()):
    with _lock:
        r = _db().execute(sql, params).fetchone()
        return dict(r) if r else None


def one(sql: str, params=()):
    """Single scalar value or None."""
    r = row(sql, params)
    if not r:
        return None
    return next(iter(r.values()))


def run(sql: str, params=()) -> int:
    """Execute a write, return lastrowid."""
    with _lock:
        cur = _db().execute(sql, params)
        _db().commit()
        return cur.lastrowid or 0


def many(sql: str, seq) -> None:
    with _lock:
        _db().executemany(sql, seq)
        _db().commit()


def now() -> float:
    return time.time()
