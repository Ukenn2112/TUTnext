-- Cross-isolate / cross-Worker leases (tutnext.core.d1lease).
-- One row per held lock; a row whose expires_at has passed counts as free.
CREATE TABLE IF NOT EXISTS locks (
    name       TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    expires_at REAL NOT NULL      -- unix seconds
);
