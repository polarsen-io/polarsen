-- Description: Track failed ingestion attempts for chat uploads

BEGIN;
ALTER TABLE general.chat_uploads
    ADD COLUMN IF NOT EXISTS error_count INT         NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS error_at    TIMESTAMPTZ;
COMMIT;
