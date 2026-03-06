# GruzPotok / tg-bot Architecture

## Runtime Topology

- `parser-bot` (`Telethon`) listens to configured Telegram chats and writes raw events to Redis Streams.
- `parser-worker` consumes the stream, runs extraction/enrichment/filtering, and persists normalized events.
- `PostgreSQL` stores parser events, user vehicles, favorites, bot data, and admin moderation state.
- `Redis` is transient infrastructure only: queue + FSM + short-lived dedupe/cache.
- `bot` serves three roles in one process:
  - Telegram bot polling (`aiogram`)
  - FastAPI API (`/api/*`)
  - Mini App (`/webapp`)

## Ingest Pipeline

1. `parser-bot` receives a Telegram message.
2. It writes a compact event to `settings.parser_stream_name` (default `logistics_stream`).
3. `parser-worker` reads via consumer group `settings.parser_stream_group`.
4. Worker stages:
   - anti-flood / anti-duplicate
   - regex extraction
   - optional LLM re-parse
   - phone blacklist / invalid geo token checks
   - INN scoring / enrichment
   - geo lookup when route is valid
   - hot-deal / suggested response derivation
5. Result is stored in `parser_ingest_events`.
6. Depending on validation outcome, event status becomes one of:
   - `synced`
   - `manual_review`
   - `ignored`
   - `spam_filtered`
   - `sync_failed`
   - `error`

## Client Surfaces

- Telegram bot:
  - conversational handlers
  - deep links
  - premium / payments
- React TWA:
  - served from `/webapp`
  - built from `frontend/twa`
  - static assets mounted at `/webapp/assets`
- Admin panel:
  - `/admin`
  - manual moderation queue at `/admin/manual-review`

## Operational Metrics

Health output now exposes parser metrics under:

- `GET /health`
- `GET /health/detailed`

Current parser metrics:

- `queue_depth`
- `pending`
- `lag`
- `consumers`
- `manual_review`
- `synced_24h`
- `ignored_24h`
- `last_event_age_min`

These same runtime metrics are surfaced on the admin dashboard.

## Deployment Notes

- The React TWA is built inside the normal Docker image build.
- The public Mini App URL should point to the bot service domain and open `/webapp`.
- Nginx only needs to reverse-proxy to `:8000`; no separate manual static hosting is required.

## Current Boundaries

- `tg-bot` is the ingestion + Mini App + bot runtime.
- `gruzpotok-api` is still a separate service for the larger platform.
- Cross-service sync remains explicit; this repository is not the source of truth for the `goutruckme-api` frontend.
