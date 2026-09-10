-- Per-miner settlement ledger (actual coinbase / sendmany amounts).
-- Miner Payment history + Total earned read from here — not share-window replay.

CREATE TABLE IF NOT EXISTS miner_payouts (
    id           BIGSERIAL PRIMARY KEY,
    height       INT NOT NULL REFERENCES blocks(height) ON DELETE CASCADE,
    address      TEXT NOT NULL,
    sats         BIGINT NOT NULL CHECK (sats >= 0),
    kind         TEXT NOT NULL,
    source       TEXT NOT NULL,
    status       TEXT NOT NULL,
    txid         TEXT,
    paid_at      TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (height, address, source)
);

CREATE INDEX IF NOT EXISTS miner_payouts_addr_height_idx
    ON miner_payouts (address, height DESC);

CREATE INDEX IF NOT EXISTS miner_payouts_height_idx
    ON miner_payouts (height);
