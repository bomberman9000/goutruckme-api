from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# SQLite для разработки, PostgreSQL для продакшена
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./goutruckme.db")

# Для SQLite нужен check_same_thread=False
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency для получения сессии БД."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Создание таблиц в БД."""
    from app.models.models import (  # noqa: F401
        User, Truck, Vehicle, Load, Bid, Message, DealSync, DocumentSync, ModerationReview,
    )
    Base.metadata.create_all(bind=engine)
