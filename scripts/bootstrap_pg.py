#!/usr/bin/env python3
"""Создать все таблицы через Base.metadata.create_all (только если нет миграций).

Обычно используй: docker compose exec api sh -lc 'cd /app && alembic upgrade head'

Запуск из контейнера (если нужно создать таблицы без alembic):
  docker compose exec api sh -lc 'cd /app && python scripts/bootstrap_pg.py'
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import engine
from app.models.models import Base

if __name__ == "__main__":
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("Done.")
