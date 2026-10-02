-- TUTnext on Cloudflare D1
-- Mirrors the PostgreSQL schema created by tutnext.core.database.DatabaseManager
-- (lower-case column names, like unquoted identifiers in PostgreSQL) plus the
-- tables backing tutnext.core.d1redis.D1Redis.

CREATE TABLE IF NOT EXISTS users (
    username          TEXT PRIMARY KEY CHECK (username <> ''),
    encryptedpassword TEXT NOT NULL CHECK (encryptedpassword <> ''),
    devicetoken       TEXT NOT NULL CHECK (devicetoken <> '')
);
CREATE INDEX IF NOT EXISTS idx_users_devicetoken ON users (devicetoken);

CREATE TABLE IF NOT EXISTS user_tokens (
    username      TEXT PRIMARY KEY,
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Redis replacement -------------------------------------------------------
CREATE TABLE IF NOT EXISTS kv_meta (
    key        TEXT PRIMARY KEY,
    expires_at REAL            -- unix seconds, NULL = no expiry
);
CREATE INDEX IF NOT EXISTS idx_kv_meta_expires ON kv_meta (expires_at);

CREATE TABLE IF NOT EXISTS kv_string (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv_hash (
    key   TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (key, field)
);

CREATE TABLE IF NOT EXISTS kv_set (
    key    TEXT NOT NULL,
    member TEXT NOT NULL,
    PRIMARY KEY (key, member)
);

CREATE TABLE IF NOT EXISTS kv_zset (
    key    TEXT NOT NULL,
    member TEXT NOT NULL,
    score  REAL NOT NULL,
    PRIMARY KEY (key, member)
);
CREATE INDEX IF NOT EXISTS idx_kv_zset_score ON kv_zset (key, score);
