"""Health check: DB ping, alembic revision, build info."""
import os
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.database import engine

router = APIRouter()


def get_build_info() -> dict:
    return {
        "sha": os.getenv("BUILD_SHA", "local"),
        "time": os.getenv("BUILD_TIME", ""),
    }


def get_alembic_info() -> dict:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[3]  # .../app/api/routes -> .../ (корень проекта)
    alembic_ini = root / "alembic.ini"

    cfg = Config(str(alembic_ini))
    script = ScriptDirectory.from_config(cfg)
    heads = list(script.get_heads())

    current = None
    try:
        with engine.connect() as conn:
            current = conn.execute(text("select version_num from alembic_version")).scalar()
    except Exception:
        current = None

    ok = (current in heads) if (current and heads) else False
    return {"current": current, "heads": heads, "ok": ok}


@router.get("/health")
def health():
    db_url = os.getenv("DATABASE_URL", "")
    db_url_present = bool(db_url)

    db = {"ok": False, "error": None, "url_present": db_url_present}

    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        db["ok"] = True
    except SQLAlchemyError as e:
        db["error"] = str(e)

    migrations = get_alembic_info()
    build = get_build_info()

    status = "ok" if (db["ok"] and migrations["ok"]) else "degraded"

    return {
        "status": status,
        "message": "ok" if status == "ok" else "degraded",
    }
