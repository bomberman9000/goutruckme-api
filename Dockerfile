FROM node:20-slim AS twa-build

WORKDIR /twa
COPY frontend/twa/package.json frontend/twa/package-lock.json ./
RUN npm ci
COPY frontend/twa/ ./
RUN npm run build

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Чтобы exec python/uvicorn видел зависимости (не только uv run)
ENV PATH="/app/.venv/bin:$PATH"

COPY . .
COPY --from=twa-build /twa/dist ./frontend/twa/dist

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
