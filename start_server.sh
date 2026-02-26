#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"
VENV_DIR="${VENV_DIR:-$ROOT/venv}"
PYTHON_BIN=""
PIP_CMD=()

if [ -x "$VENV_DIR/bin/python" ] && [ -x "$VENV_DIR/bin/pip" ]; then
  PYTHON_BIN="$VENV_DIR/bin/python"
  PIP_CMD=("$VENV_DIR/bin/pip")
  echo "🐍 Использую существующее venv: $VENV_DIR"
else
  echo "🐍 Виртуальное окружение не найдено. Пытаюсь создать: $VENV_DIR"
  if python3 -m venv "$VENV_DIR" 2>/dev/null && [ -x "$VENV_DIR/bin/python" ] && [ -x "$VENV_DIR/bin/pip" ]; then
    PYTHON_BIN="$VENV_DIR/bin/python"
    PIP_CMD=("$VENV_DIR/bin/pip")
    echo "✅ venv создано успешно"
  else
    PYTHON_BIN="$(command -v python3 || true)"
    if [ -z "$PYTHON_BIN" ]; then
      echo "❌ python3 не найден. Установите Python 3."
      exit 1
    fi
    PIP_CMD=("$PYTHON_BIN" "-m" "pip")
    echo "⚠️ Не удалось создать venv (возможно, не установлен python3-venv)."
    echo "➡️ Использую системный Python: $PYTHON_BIN"
  fi
fi

if ! "$PYTHON_BIN" -c "import uvicorn" >/dev/null 2>&1; then
  if [ ! -f requirements.txt ]; then
    echo "❌ requirements.txt не найден в $ROOT"
    exit 1
  fi
  echo "📦 Устанавливаю зависимости из requirements.txt..."
  "${PIP_CMD[@]}" install -r requirements.txt
fi

pkill -f "uvicorn.*${PORT}" 2>/dev/null || true
sleep 1

echo "🚀 Запуск сервера на http://localhost:${PORT}"
exec "$PYTHON_BIN" -m uvicorn app.api.main:app --host "$HOST" --port "$PORT" --reload
