-- Composite covering index for the main feed query path:
-- WHERE is_spam=false AND status='synced' ORDER BY id DESC
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_parser_events_feed
    ON parser_ingest_events (id DESC)
    WHERE is_spam = false AND status = 'synced';

-- Verdict filter (used in every feed request)
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_parser_events_verdict
    ON parser_ingest_events (trust_verdict)
    WHERE is_spam = false AND status = 'synced';

-- Date filter for load_date queries and export
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_parser_events_load_date
    ON parser_ingest_events (load_date)
    WHERE load_date IS NOT NULL;

-- created_at for notification cutoff and watchdog freshness check
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_parser_events_created
    ON parser_ingest_events (created_at DESC);

-- Phone index for blacklist lookup within events
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_parser_events_phone
    ON parser_ingest_events (phone)
    WHERE phone IS NOT NULL;

-- Body type filter
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_parser_events_body_type
    ON parser_ingest_events (body_type)
    WHERE body_type IS NOT NULL;

-- Composite index for blacklist check: list_type + phone
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_counterparty_blacklist_phone
    ON counterparty_lists (phone)
    WHERE list_type = 'black';
