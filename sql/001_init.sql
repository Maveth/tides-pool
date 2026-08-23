-- tides-pool schema (Phase 0)
-- Share log is append-only; hot window queried by walking seq DESC.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    address      TEXT PRIMARY KEY,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workers (
    address      TEXT NOT NULL REFERENCES users(address),
    worker       TEXT NOT NULL,
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (address, worker)
);

-- Distinct shares (TIDES resolution). work = difficulty-1 units.
CREATE TABLE IF NOT EXISTS shares (
    seq            BIGSERIAL PRIMARY KEY,
    address        TEXT NOT NULL REFERENCES users(address),
    worker         TEXT,
    work           BIGINT NOT NULL CHECK (work >= 1),
    fee_bps        INT NOT NULL DEFAULT 1000,
    job_head_seq   BIGINT,          -- share-log head at job issue (anti-cheat)
    pow_hash       BYTEA,
    accepted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS shares_address_seq_idx ON shares (address, seq DESC);
CREATE INDEX IF NOT EXISTS shares_seq_desc_idx ON shares (seq DESC);

CREATE TABLE IF NOT EXISTS blocks (
    height           INT PRIMARY KEY,
    block_hash       TEXT NOT NULL UNIQUE,
    difficulty       NUMERIC NOT NULL,
    reward_sats      BIGINT NOT NULL,
    finder_address   TEXT,
    share_head_seq   BIGINT,        -- head used for window (job-issue)
    accounted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    coinbase_txid    TEXT
);

CREATE TABLE IF NOT EXISTS finder_credits (
    id               BIGSERIAL PRIMARY KEY,
    from_height      INT NOT NULL REFERENCES blocks(height),
    address          TEXT NOT NULL,
    credit_sats      BIGINT NOT NULL CHECK (credit_sats >= 0),
    paid_in_height   INT REFERENCES blocks(height),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS finder_credits_open_idx
    ON finder_credits (address)
    WHERE paid_in_height IS NULL;

CREATE TABLE IF NOT EXISTS coinbaser_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    share_head_seq   BIGINT,
    reward_estimate  BIGINT NOT NULL,
    payload_json     JSONB NOT NULL
);
