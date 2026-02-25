# AGENTS.md

## Cursor Cloud specific instructions

### Overview

GruzPotok is a logistics Telegram bot platform (FastAPI + aiogram) with a web admin panel and a React/Vite Telegram WebApp (TWA) frontend. See `README.md` for full project structure and bot commands.

### Required services

| Service | How to start | Port |
|---------|-------------|------|
| PostgreSQL 16 | `sudo docker start postgres` (or create: see below) | 5432 |
| Redis | `sudo docker start redis` (or create: see below) | 6379 |

If containers don't exist yet:
```bash
sudo docker network create backend-network 2>/dev/null || true
sudo docker run -d --name postgres --network backend-network -e POSTGRES_USER=bot -e POSTGRES_PASSWORD=botpass -e POSTGRES_DB=botdb -p 5432:5432 postgres:16-alpine
sudo docker run -d --name redis --network backend-network -p 6379:6379 redis:alpine
```

Docker daemon may need to be started first: `sudo dockerd &>/tmp/dockerd.log &`

### Environment file

Copy `.env.example` to `.env` and change hostnames from Docker service names to `localhost`:
- `DATABASE_URL=postgresql+asyncpg://bot:botpass@localhost:5432/botdb`
- `REDIS_URL=redis://localhost:6379`
- `BOT_TOKEN` — use a fake token for local dev (bot polling will fail but the API/admin work fine)

### Running the backend

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

- API root: http://localhost:8000/
- Health: http://localhost:8000/health
- Admin panel: http://localhost:8000/admin (login with `ADMIN_USERNAME`/`ADMIN_PASSWORD` from `.env`)
- The Telegram bot polling will fail with a fake `BOT_TOKEN` — this is expected; all HTTP endpoints still work.

### Running the frontend (TWA)

```bash
cd frontend/twa && npm run dev
```

Serves at http://localhost:5173/. The "not valid JSON" error is expected when running outside Telegram (it tries to parse TMA initData).

### Lint and tests

- Lint: `uv run ruff check .` (pre-existing warnings exist; CI runs with `|| true`)
- Tests: `uv run pytest -q` (34 tests; requires `pytest-asyncio` which is installed alongside pytest)

### SQL migrations

Migrations in `migrations/` are raw SQL files. They must be applied **after** the app starts (since `init_db()` calls `Base.metadata.create_all()` to create base tables first):
```bash
for f in $(ls migrations/*.sql | sort); do sudo docker exec -i postgres psql -U bot -d botdb < "$f"; done
```

### Gotchas

- `.python-version` specifies 3.14; `uv sync` auto-downloads it. The system Python may differ.
- `pytest-asyncio` is needed for async tests but is not in `pyproject.toml` — install via `uv pip install pytest-asyncio`.
- Docker runs nested (container-in-VM); requires `fuse-overlayfs` storage driver and `iptables-legacy`. This is handled in the initial setup.
