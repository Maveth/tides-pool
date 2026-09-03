-- Ops-only coinbase finds (Prime/GW fallback): keep as pool finds; ops pays miners manually.
ALTER TABLE blocks ADD COLUMN IF NOT EXISTS payout_mode TEXT NOT NULL DEFAULT 'onchain_split';
ALTER TABLE blocks ADD COLUMN IF NOT EXISTS manual_payout_done BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE blocks ADD COLUMN IF NOT EXISTS manual_payout_note TEXT;
ALTER TABLE blocks ADD COLUMN IF NOT EXISTS intended_payout_json TEXT;
