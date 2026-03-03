from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, Boolean, Text, Integer, Float, Enum, Index, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.core.database import Base
import enum


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [str(member.value) for member in enum_cls]


def _string_enum(enum_cls: type[enum.Enum]) -> Enum:
    return Enum(
        enum_cls,
        values_callable=_enum_values,
        native_enum=False,
        validate_strings=True,
    )

class User(Base):
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_carrier: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    trust_score: Mapped[int] = mapped_column(Integer, default=50)
    warnings_count: Mapped[int] = mapped_column(Integer, default=0)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    premium_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)



class UserRole(enum.Enum):
    CUSTOMER = "customer"
    CARRIER = "carrier"
    FORWARDER = "forwarder"

class VerificationStatus(enum.Enum):
    BASIC = "basic"
    CONFIRMED = "confirmed"
    VERIFIED = "verified"

class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.CUSTOMER)
    inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    ogrn: Mapped[str | None] = mapped_column(String(15), nullable=True)
    director_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus), default=VerificationStatus.BASIC
    )
    verification_doc_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CargoStatus(enum.Enum):
    NEW = "new"
    ACTIVE = "active"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class CargoPaymentStatus(enum.Enum):
    UNSECURED = "unsecured"
    PAYMENT_PENDING = "payment_pending"
    FUNDED = "funded"
    DELIVERY_MARKED = "delivery_marked"
    RELEASED = "released"
    DISPUTED = "disputed"
    CANCELLED = "cancelled"

class Cargo(Base):
    __tablename__ = "cargos"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    carrier_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    client_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    forwarder_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    from_city: Mapped[str] = mapped_column(String(100))
    to_city: Mapped[str] = mapped_column(String(100))
    
    cargo_type: Mapped[str] = mapped_column(String(100))
    weight: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    price: Mapped[int] = mapped_column(Integer)
    actual_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    load_date: Mapped[datetime] = mapped_column(DateTime)
    load_time: Mapped[str | None] = mapped_column(String(10), nullable=True)  # формат "HH:MM"
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    source_platform: Mapped[str] = mapped_column(String(64), default="manual", index=True)

    status: Mapped[CargoStatus] = mapped_column(Enum(CargoStatus), default=CargoStatus.NEW)
    tracking_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payment_status: Mapped[CargoPaymentStatus] = mapped_column(
        _string_enum(CargoPaymentStatus),
        default=CargoPaymentStatus.UNSECURED,
    )
    payment_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(Integer, ForeignKey("cargos.id"))
    type: Mapped[str] = mapped_column(String(1))  # "A" или "B"
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft, sent, signed, cancelled

    created_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    selected_carrier_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Подписи
    signed_by_client_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    signed_by_forwarder_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    signed_by_carrier_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Рендер
    rendered_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # PDF
    pdf_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pdf_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ApplicationPartySnapshot(Base):
    __tablename__ = "application_party_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("applications.id"))
    role: Mapped[str] = mapped_column(String(20))  # client, forwarder, carrier
    payload_json: Mapped[str] = mapped_column(Text)  # JSON с реквизитами
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CompanyDetails(Base):
    __tablename__ = "company_details"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), unique=True)

    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    kpp: Mapped[str | None] = mapped_column(String(9), nullable=True)
    ogrn: Mapped[str | None] = mapped_column(String(15), nullable=True)
    legal_address: Mapped[str | None] = mapped_column(String(500), nullable=True)

    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_bik: Mapped[str | None] = mapped_column(String(9), nullable=True)
    bank_account: Mapped[str | None] = mapped_column(String(20), nullable=True)
    bank_corr_account: Mapped[str | None] = mapped_column(String(20), nullable=True)

    contact_person: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(100), nullable=True)

    driver_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    driver_passport: Mapped[str | None] = mapped_column(String(100), nullable=True)
    driver_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    vehicle_info: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Рейтинг (10-балльная система)
    rating_registration: Mapped[int] = mapped_column(Integer, default=2)  # ИП/ООО = 2, физлицо = 0
    rating_subscription: Mapped[int] = mapped_column(Integer, default=0)  # подписка = +1
    rating_experience: Mapped[int] = mapped_column(Integer, default=0)    # >1 года = +1
    rating_verified: Mapped[int] = mapped_column(Integer, default=0)      # верификация = +1
    rating_deals_completed: Mapped[int] = mapped_column(Integer, default=0)  # 10+ сделок = +1, 50+ = +2
    rating_no_claims: Mapped[int] = mapped_column(Integer, default=1)     # нет претензий = +1, есть = -1
    rating_response_time: Mapped[int] = mapped_column(Integer, default=0)  # быстрый ответ = +1
    rating_documents: Mapped[int] = mapped_column(Integer, default=0)      # документы в порядке = +1

    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def total_rating(self) -> int:
        """Вычисляемый общий рейтинг (сумма, макс 10)."""
        total = (
            self.rating_registration
            + self.rating_subscription
            + self.rating_experience
            + self.rating_verified
            + self.rating_deals_completed
            + self.rating_no_claims
            + self.rating_response_time
            + self.rating_documents
        )
        return min(10, max(0, total))


class ClaimStatus(enum.Enum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Кто подаёт
    from_user_id: Mapped[int] = mapped_column(BigInteger)
    from_company_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("company_details.id"), nullable=True)

    # На кого подаёт
    to_user_id: Mapped[int] = mapped_column(BigInteger)
    to_company_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("company_details.id"), nullable=True)

    # Связь со сделкой (опционально)
    cargo_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("cargos.id"), nullable=True)

    # Данные претензии
    claim_type: Mapped[str] = mapped_column(String(50))  # payment, damage, delay, fraud, other
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)  # сумма претензии в рублях

    # Статус
    status: Mapped[ClaimStatus] = mapped_column(Enum(ClaimStatus), default=ClaimStatus.OPEN)

    # Ответ компании
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Решение (админ)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(30))
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CargoResponse(Base):
    __tablename__ = "cargo_responses"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    cargo_id: Mapped[int] = mapped_column(Integer)
    carrier_id: Mapped[int] = mapped_column(BigInteger)
    price_offer: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class MarketPrice(Base):
    __tablename__ = "market_prices"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_city: Mapped[str] = mapped_column(String(100))
    to_city: Mapped[str] = mapped_column(String(100))
    price: Mapped[int] = mapped_column()  # цена за 20 тонн без НДС
    cargo_type: Mapped[str] = mapped_column(String(50), default="тент")  # тент, реф, изотерм
    weight: Mapped[float] = mapped_column(default=20.0)  # базовый вес
    source: Mapped[str] = mapped_column(String(100), default="umnayalogistika")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_market_route", "from_city", "to_city", "cargo_type", unique=True),
    )

class Feedback(Base):
    __tablename__ = "feedback"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Reminder(Base):
    __tablename__ = "reminders"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    remind_at: Mapped[datetime] = mapped_column(DateTime)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class RouteSubscription(Base):
    __tablename__ = "route_subscriptions"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    from_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    to_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    body_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    min_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Rating(Base):
    __tablename__ = "ratings"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    cargo_id: Mapped[int] = mapped_column(Integer)
    from_user_id: Mapped[int] = mapped_column(BigInteger)
    to_user_id: Mapped[int] = mapped_column(BigInteger)
    score: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    cargo_id: Mapped[int] = mapped_column(Integer)
    from_user_id: Mapped[int] = mapped_column(BigInteger)
    to_user_id: Mapped[int] = mapped_column(BigInteger)
    message: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ReportType(enum.Enum):
    FRAUD = "fraud"
    SPAM = "spam"
    FAKE_CARGO = "fake_cargo"
    NO_PAYMENT = "no_payment"
    OTHER = "other"

class Report(Base):
    __tablename__ = "reports"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    from_user_id: Mapped[int] = mapped_column(BigInteger)
    to_user_id: Mapped[int] = mapped_column(BigInteger)
    cargo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    report_type: Mapped[ReportType] = mapped_column(Enum(ReportType))
    description: Mapped[str] = mapped_column(Text)
    is_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class CargoLocation(Base):
    __tablename__ = "cargo_locations"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    cargo_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(BigInteger)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ModerationReview(Base):
    __tablename__ = "moderation_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="done")
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    flags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_moderation_entity"),
        Index("ix_moderation_entity", "entity_type", "entity_id"),
    )


class RouteRateProfile(Base):
    __tablename__ = "route_rate_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_city_norm: Mapped[str] = mapped_column(String(120))
    to_city_norm: Mapped[str] = mapped_column(String(120))
    min_rate_per_km: Mapped[int] = mapped_column(Integer)
    max_rate_per_km: Mapped[int] = mapped_column(Integer)
    median_rate_per_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    samples_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("from_city_norm", "to_city_norm", name="uq_route_rate_profile"),
        Index("ix_route_rate_pair", "from_city_norm", "to_city_norm"),
    )


class CounterpartyList(Base):
    __tablename__ = "counterparty_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    list_type: Mapped[str] = mapped_column(String(10))  # white | black
    inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_counterparty_list_type", "list_type"),
        Index("ix_counterparty_inn", "inn"),
        Index("ix_counterparty_phone", "phone"),
        Index("ix_counterparty_name", "name"),
    )


class DealDocRequest(Base):
    __tablename__ = "deal_doc_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    deal_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="requested")
    required_docs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_codes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("deal_id", name="uq_deal_doc_request"),
        Index("ix_deal_doc_request_deal", "deal_id"),
    )


class ClosedDealStat(Base):
    __tablename__ = "closed_deal_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_city_norm: Mapped[str] = mapped_column(String(120))
    to_city_norm: Mapped[str] = mapped_column(String(120))
    distance_km: Mapped[float] = mapped_column(Float)
    rate_per_km: Mapped[float] = mapped_column(Float)
    total_rub: Mapped[float | None] = mapped_column(Float, nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_closed_deal_route", "from_city_norm", "to_city_norm"),
    )


class CounterpartyRiskHistory(Base):
    __tablename__ = "counterparty_risk_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    counterparty_inn: Mapped[str] = mapped_column(String(12))
    deal_id: Mapped[int] = mapped_column(Integer)
    risk_level: Mapped[str] = mapped_column(String(20))
    score_total: Mapped[int] = mapped_column(Integer, default=0)
    reason_codes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_counterparty_risk_inn", "counterparty_inn"),
    )


class RouteRateStats(Base):
    __tablename__ = "route_rate_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_city_norm: Mapped[str] = mapped_column(String(120))
    to_city_norm: Mapped[str] = mapped_column(String(120))
    mean_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    std_dev: Mapped[float | None] = mapped_column(Float, nullable=True)
    p25: Mapped[float | None] = mapped_column(Float, nullable=True)
    p75: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("from_city_norm", "to_city_norm", name="uq_route_rate_stats"),
        Index("ix_route_rate_stats_pair", "from_city_norm", "to_city_norm"),
    )


class ParserIngestEvent(Base):
    __tablename__ = "parser_ingest_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_entry_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    chat_id: Mapped[str] = mapped_column(String(64), index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    source: Mapped[str] = mapped_column(String(64), default="tg-parser-bot")

    from_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    to_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    body_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    rate_rub: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_t: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    load_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    cargo_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_direct_customer: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dimensions: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_hot_deal: Mapped[bool] = mapped_column(Boolean, default=False)
    suggested_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)

    from_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    from_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    to_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    to_lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    trust_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trust_verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trust_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)

    is_spam: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="parsed")  # parsed|synced|spam_filtered|sync_failed|error
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_parser_events_route", "from_city", "to_city"),
        Index("ix_parser_events_score", "trust_score"),
        Index("ix_parser_events_status", "status"),
    )


class FeedComplaint(Base):
    __tablename__ = "feed_complaints"

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(32), default="scam")
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("feed_id", "user_id", name="uq_feed_complaint_user"),
        Index("ix_feed_complaints_feed", "feed_id"),
    )


class UserVehicle(Base):
    __tablename__ = "user_vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    body_type: Mapped[str] = mapped_column(String(64))
    capacity_tons: Mapped[float] = mapped_column(Float, default=20.0)
    location_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=False)
    plate_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sts_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_user_vehicles_user", "user_id"),
        Index("ix_user_vehicles_available", "is_available", "location_city"),
    )


class TeamMember(Base):
    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_inn: Mapped[str] = mapped_column(String(12), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(20), default="carrier")
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("company_inn", "user_id", name="uq_team_member"),
        Index("ix_team_members_company", "company_inn"),
        Index("ix_team_members_user", "user_id"),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(Integer, index=True)
    carrier_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    dispatcher_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dispatcher_inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    amount_rub: Mapped[int] = mapped_column(Integer)
    payment_terms: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payment_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="delivered")
    penalty_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_transactions_carrier", "carrier_user_id"),
        Index("ix_transactions_status", "status"),
        Index("ix_transactions_deadline", "payment_deadline"),
    )


class UserWallet(Base):
    __tablename__ = "user_wallets"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    balance_rub: Mapped[int] = mapped_column(Integer, default=0)
    frozen_balance_rub: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EscrowStatus(enum.Enum):
    PAYMENT_PENDING = "payment_pending"
    FUNDED = "funded"
    DELIVERY_MARKED = "delivery_marked"
    RELEASED = "released"
    DISPUTED = "disputed"
    CANCELLED = "cancelled"


class EscrowDeal(Base):
    __tablename__ = "escrow_deals"

    id: Mapped[int] = mapped_column(primary_key=True)
    cargo_id: Mapped[int] = mapped_column(Integer, ForeignKey("cargos.id"), index=True)
    client_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    carrier_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    amount_rub: Mapped[int] = mapped_column(Integer)
    platform_fee_rub: Mapped[int] = mapped_column(Integer, default=0)
    carrier_amount_rub: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[EscrowStatus] = mapped_column(
        _string_enum(EscrowStatus),
        default=EscrowStatus.PAYMENT_PENDING,
    )
    provider: Mapped[str] = mapped_column(String(32), default="mock_tochka")
    tochka_payment_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payment_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    funded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_escrow_deals_client", "client_id", "status"),
        Index("ix_escrow_deals_carrier", "carrier_id", "status"),
    )


class EscrowEvent(Base):
    __tablename__ = "escrow_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    escrow_deal_id: Mapped[int] = mapped_column(Integer, ForeignKey("escrow_deals.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CallLog(Base):
    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    cargo_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_call_logs_user", "user_id"),
        Index("ix_call_logs_cargo", "cargo_id"),
        Index("ix_call_logs_created", "created_at"),
    )


class Favorite(Base):
    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    feed_id: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="saved")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "feed_id", name="uq_favorite_user_feed"),
        Index("ix_favorites_user", "user_id"),
        Index("ix_favorites_feed", "feed_id"),
    )


class PremiumPayment(Base):
    __tablename__ = "premium_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    plan_days: Mapped[int] = mapped_column(Integer)
    amount_stars: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(10), default="XTR")
    status: Mapped[str] = mapped_column(String(20), default="success")
    invoice_payload: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_payment_charge_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_premium_payments_user", "user_id"),
        Index("ix_premium_payments_created", "created_at"),
    )


class ReferralInvite(Base):
    __tablename__ = "referral_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    inviter_user_id: Mapped[int] = mapped_column(BigInteger)
    invited_user_id: Mapped[int] = mapped_column(BigInteger)
    source_payload: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reward_days: Mapped[int] = mapped_column(Integer, default=0)
    trigger_payment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("invited_user_id", name="uq_referral_invited_user"),
        Index("ix_referral_inviter", "inviter_user_id"),
        Index("ix_referral_invited", "invited_user_id"),
        Index("ix_referral_rewarded_at", "rewarded_at"),
    )


class ReferralReward(Base):
    __tablename__ = "referral_rewards"

    id: Mapped[int] = mapped_column(primary_key=True)
    inviter_user_id: Mapped[int] = mapped_column(BigInteger)
    invited_user_id: Mapped[int] = mapped_column(BigInteger)
    payment_id: Mapped[int] = mapped_column(Integer)
    reward_days: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_referral_reward_payment"),
        Index("ix_referral_rewards_inviter", "inviter_user_id"),
        Index("ix_referral_rewards_invited", "invited_user_id"),
        Index("ix_referral_rewards_created", "created_at"),
    )
