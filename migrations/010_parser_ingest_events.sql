-- Parser v2: ingestion audit/events table

CREATE TABLE IF NOT EXISTS parser_ingest_events (
    id SERIAL PRIMARY KEY,
    stream_entry_id VARCHAR(64) NOT NULL UNIQUE,
    chat_id VARCHAR(64) NOT NULL,
    message_id BIGINT NOT NULL,
    source VARCHAR(64) NOT NULL DEFAULT 'tg-parser-bot',
    from_city VARCHAR(120),
    to_city VARCHAR(120),
    body_type VARCHAR(64),
    phone VARCHAR(32),
    inn VARCHAR(12),
    rate_rub INTEGER,
    weight_t FLOAT,
    trust_score INTEGER,
    trust_verdict VARCHAR(16),
    trust_comment TEXT,
    provider VARCHAR(32),
    is_spam BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(32) NOT NULL DEFAULT 'parsed',
    error VARCHAR(255),
    raw_text TEXT NOT NULL,
    details_json TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_parser_ingest_events_stream_entry_id ON parser_ingest_events(stream_entry_id);
CREATE INDEX IF NOT EXISTS ix_parser_ingest_events_chat_id ON parser_ingest_events(chat_id);
CREATE INDEX IF NOT EXISTS ix_parser_ingest_events_message_id ON parser_ingest_events(message_id);
CREATE INDEX IF NOT EXISTS ix_parser_events_route ON parser_ingest_events(from_city, to_city);
CREATE INDEX IF NOT EXISTS ix_parser_events_score ON parser_ingest_events(trust_score);
CREATE INDEX IF NOT EXISTS ix_parser_events_status ON parser_ingest_events(status);
