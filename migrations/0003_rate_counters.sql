-- Exact per-student request counters for tutnext-gateway (fixed 60 s windows).
-- One row per key, reset when a new window starts; tutnext-cron deletes stale rows.
CREATE TABLE IF NOT EXISTS rate_counters (
    key    TEXT PRIMARY KEY,
    window INTEGER NOT NULL,   -- unix time // 60
    count  INTEGER NOT NULL
);
