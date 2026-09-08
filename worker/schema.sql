-- Life Alert Fantasy — trade ledger
-- Applied with:  wrangler d1 execute life-alert-ledger --file=./schema.sql --remote

CREATE TABLE IF NOT EXISTS trades (
  id                TEXT PRIMARY KEY,       -- client-generated uuid, makes retries idempotent
  season            INTEGER NOT NULL,
  filed_at          TEXT    NOT NULL,       -- ISO 8601, set by the worker not the client
  filed_by          TEXT,
  status            TEXT    NOT NULL,       -- settled | pending_condition | resolved
  conditional       INTEGER NOT NULL,       -- 0/1
  side_a_manager_id TEXT    NOT NULL,
  side_a_manager    TEXT    NOT NULL,
  side_b_manager_id TEXT    NOT NULL,
  side_b_manager    TEXT    NOT NULL,
  record_json       TEXT    NOT NULL,       -- the full submitted record
  resolution_json   TEXT                    -- written at end-of-season settlement
);

CREATE INDEX IF NOT EXISTS idx_trades_season ON trades (season, filed_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_pending ON trades (status) WHERE status = 'pending_condition';
