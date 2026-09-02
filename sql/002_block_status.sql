-- Block confirmation / orphan tracking + find-based window support
ALTER TABLE blocks
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'confirmed',
  ADD COLUMN IF NOT EXISTS status_checked_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS orphan_reason TEXT;

-- pending | confirmed | orphaned | misattributed
CREATE INDEX IF NOT EXISTS blocks_pending_idx ON blocks (height)
  WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS blocks_confirmed_height_idx ON blocks (height DESC)
  WHERE status = 'confirmed';

-- Backfill share_head_seq from share log time so find-based windows work for history
UPDATE blocks b
SET share_head_seq = sub.seq
FROM (
  SELECT b2.height AS height,
         (
           SELECT MAX(s.seq) FROM shares s
           WHERE s.accepted_at <= b2.accounted_at
         ) AS seq
  FROM blocks b2
  WHERE b2.share_head_seq IS NULL
) sub
WHERE b.height = sub.height
  AND b.share_head_seq IS NULL
  AND sub.seq IS NOT NULL;
