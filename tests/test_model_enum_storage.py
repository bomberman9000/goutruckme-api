import os

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://bot:botpass@localhost:5432/botdb")
os.environ["DEBUG"] = "false"


def _enum_processors():
    from sqlalchemy.dialects import postgresql

    from src.core.models import Cargo, CargoPaymentStatus, EscrowDeal, EscrowStatus

    dialect = postgresql.dialect()
    cargo_type = Cargo.__table__.c.payment_status.type
    escrow_type = EscrowDeal.__table__.c.status.type
    return {
        "cargo": (
            cargo_type.bind_processor(dialect),
            cargo_type.result_processor(dialect, None),
            CargoPaymentStatus.UNSECURED,
            "unsecured",
        ),
        "escrow": (
            escrow_type.bind_processor(dialect),
            escrow_type.result_processor(dialect, None),
            EscrowStatus.PAYMENT_PENDING,
            "payment_pending",
        ),
    }


def test_cargo_payment_status_uses_lowercase_storage_values():
    bind, result, member, raw = _enum_processors()["cargo"]

    assert bind(member) == raw
    assert result(raw) is member


def test_escrow_status_uses_lowercase_storage_values():
    bind, result, member, raw = _enum_processors()["escrow"]

    assert bind(member) == raw
    assert result(raw) is member
