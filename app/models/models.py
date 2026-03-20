from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Date, Float, Text, Enum, Boolean, BigInteger, UniqueConstraint
from sqlalchemy.types import JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.database import Base
import enum


# Роли пользователей
class UserRole(str, enum.Enum):
    # Основные роли MVP аналитики
    client = "client"
    forwarder = "forwarder"
    carrier = "carrier"
    admin = "admin"
    # Legacy-алиасы для обратной совместимости
    shipper = "shipper"
    expeditor = "expeditor"


class CargoStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    closed = "closed"
    cancelled = "cancelled"


class VehicleKind(str, enum.Enum):
    EUROFURA_TENT_20T = "EUROFURA_TENT_20T"
    JUMBO = "JUMBO"
    REFRIGERATOR = "REFRIGERATOR"
    ISOTHERM = "ISOTHERM"
    DUMP_TRUCK = "DUMP_TRUCK"
    TANKER = "TANKER"
    CONTAINER_CARRIER = "CONTAINER_CARRIER"
    FLATBED = "FLATBED"
    LOWBOY_TRAL = "LOWBOY_TRAL"
    VAN_UP_TO_3_5T = "VAN_UP_TO_3_5T"
    CAR_CARRIER = "CAR_CARRIER"
    TIMBER_TRUCK = "TIMBER_TRUCK"
    MANIPULATOR = "MANIPULATOR"


# --------------------
#  USERS
# --------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    # Старые поля (для обратной совместимости)
    fullname = Column(String, nullable=True)  # Теперь опционально
    company = Column(String, nullable=True)
    
    # Новые поля для регистрации по ИНН
    organization_type = Column(String, nullable=True)  # "ИП" или "ООО"
    inn = Column(String, unique=True, nullable=True, index=True)  # ИНН (10 или 12 цифр)
    organization_name = Column(String, nullable=True)  # Название ИП или ООО
    email = Column(String, nullable=True)
    ogrn = Column(String, nullable=True, index=True)
    city = Column(String, nullable=True)
    contact_person = Column(String, nullable=True)
    website = Column(String, nullable=True)
    edo_enabled = Column(Boolean, default=False)
    requisites_verified = Column(Boolean, default=False)
    documents_verified = Column(Boolean, default=False)
    
    phone = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.forwarder)
    
    # Банковские реквизиты для подтверждения
    bank_name = Column(String, nullable=True)
    bank_account = Column(String, nullable=True)  # Расчетный счет
    bank_bik = Column(String, nullable=True)  # БИК
    bank_ks = Column(String, nullable=True)  # Корреспондентский счет
    payment_confirmed = Column(Boolean, default=False)  # Подтверждена ли оплата
    payment_date = Column(DateTime, nullable=True)  # Дата подтверждения оплаты
    
    # Система баллов и рейтинга (как в АТИ)
    rating = Column(Float, default=5.0)  # Рейтинг от 0 до 5
    points = Column(Integer, default=100)  # Баллы (как АТИ-баллы)
    successful_deals = Column(Integer, default=0)  # Успешные сделки
    complaints = Column(Integer, default=0)  # Жалобы
    disputes = Column(Integer, default=0)  # Споры
    verified = Column(Boolean, default=False)  # Верифицирован ли аккаунт
    trust_level = Column(String, default="new")  # new / trusted / verified / premium
    
    created_at = Column(DateTime, default=datetime.utcnow)
    last_activity = Column(DateTime, default=datetime.utcnow)

    # Telegram интеграция
    telegram_id = Column(BigInteger, unique=True, nullable=True, index=True)
    telegram_username = Column(String(255), nullable=True)
    telegram_linked_at = Column(DateTime, nullable=True)
    # Referral & Pro
    referral_code  = Column(String(20), nullable=True, unique=True, index=True)
    referred_by    = Column(Integer, ForeignKey("users.id"), nullable=True)
    referral_count = Column(Integer, default=0, nullable=False)
    pro_until      = Column(DateTime, nullable=True)
    billing_plan   = Column(String(20), nullable=True, default="free")

    trucks = relationship("Truck", back_populates="owner")
    loads = relationship("Load", back_populates="creator")
    bids = relationship("Bid", back_populates="carrier")
    messages = relationship("Message", back_populates="sender")
    rating_history = relationship("RatingHistory", back_populates="user")
    complaints_sent = relationship(
        "Complaint",
        foreign_keys="Complaint.complainant_id",
        back_populates="complainant",
    )
    complaints_received = relationship(
        "Complaint",
        foreign_keys="Complaint.defendant_id",
        back_populates="defendant",
    )
    forum_posts = relationship(
        "ForumPost",
        foreign_keys="ForumPost.author_id",
        back_populates="author",
    )
    forum_comments = relationship(
        "ForumComment",
        foreign_keys="ForumComment.author_id",
        back_populates="author",
    )
    vehicles = relationship("Vehicle", back_populates="carrier", foreign_keys="Vehicle.carrier_id")
    trust_stats = relationship("CompanyTrustStats", back_populates="company", uselist=False)


# --------------------
#  COMPANY TRUST STATS (агрегаты доверия компании)
# --------------------
class CompanyTrustStats(Base):
    __tablename__ = "company_trust_stats"

    company_id = Column(Integer, ForeignKey("users.id"), primary_key=True, index=True)
    trust_score = Column(Integer, default=50, nullable=False)
    stars = Column(Integer, default=3, nullable=False)
    success_rate = Column(Float, nullable=True)
    deals_total = Column(Integer, default=0, nullable=False)
    deals_success = Column(Integer, default=0, nullable=False)
    disputes_total = Column(Integer, default=0, nullable=False)
    disputes_confirmed = Column(Integer, default=0, nullable=False)
    flags_total = Column(Integer, default=0, nullable=False)
    flags_high = Column(Integer, default=0, nullable=False)
    profile_completeness = Column(Float, default=0.0, nullable=False)
    response_time_avg_min = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company = relationship("User", back_populates="trust_stats")


# --------------------
#  TRUCKS (машины)
# --------------------
class Truck(Base):
    __tablename__ = "trucks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    type = Column(String, nullable=False)  # газель, 5т, 10т, фура
    capacity = Column(Float, nullable=True)  # грузоподъемность
    region = Column(String, nullable=True)
    status = Column(String, default="free")  # free / busy

    owner = relationship("User", back_populates="trucks")


# --------------------
#  VEHICLES (парк перевозчика для витрины/матчинга)
# --------------------
class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "plate_number", name="uq_vehicle_owner_plate"),
    )

    id = Column(Integer, primary_key=True, index=True)
    carrier_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    name = Column(String(120), nullable=True)
    vehicle_kind = Column(String(40), nullable=True, index=True)
    body_type = Column(String(32), nullable=False, index=True)  # тент / реф / площадка / коники
    brand = Column(String(64), nullable=True)
    model = Column(String(64), nullable=True)
    plate_number = Column(String(24), nullable=True)
    vin = Column(String(64), nullable=True)
    pts_number = Column(String(64), nullable=True)
    payload_tons = Column(Float, nullable=True)
    capacity_tons = Column(Float, nullable=False)
    volume_m3 = Column(Float, nullable=False)
    max_weight_t = Column(Float, nullable=True)
    max_volume_m3 = Column(Float, nullable=True)
    length_m = Column(Float, nullable=True)
    width_m = Column(Float, nullable=True)
    height_m = Column(Float, nullable=True)
    loading_types = Column(JSON, nullable=True)
    options = Column(JSON, nullable=True)
    adr_classes = Column(JSON, nullable=True)
    crew_size = Column(Integer, nullable=False, default=1)
    temp_min = Column(Float, nullable=True)
    temp_max = Column(Float, nullable=True)
    city_id = Column(Integer, ForeignKey("cities.id"), nullable=True, index=True)
    start_lat = Column(Float, nullable=True)
    start_lon = Column(Float, nullable=True)
    location_city = Column(String(120), nullable=False, index=True)
    location_region = Column(String(120), nullable=True, index=True)
    radius_km = Column(Integer, nullable=False, default=50)
    available_from = Column(Date, nullable=False, index=True)
    available_to = Column(Date, nullable=True, index=True)
    rate_per_km = Column(Float, nullable=True)
    status = Column(String(20), default="active", index=True)  # active / archived
    is_priority = Column(Boolean, default=False, nullable=False, index=True)

    # AI-слой: сохраняем результат оценки на момент публикации
    ai_risk_level = Column(String(20), default="low", index=True)  # low / medium / high
    ai_score = Column(Integer, default=0)
    ai_warnings = Column(JSON, nullable=True)
    ai_market_rate = Column(Float, nullable=True)  # среднерыночная ставка для сравнения
    ai_idle_ratio = Column(Float, nullable=True)  # доля простаивающего парка перевозчика

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    carrier = relationship("User", back_populates="vehicles", foreign_keys=[carrier_id])
    city = relationship("City", foreign_keys=[city_id])


# --------------------
#  CITIES (справочник городов для автокомплита)
# --------------------
class City(Base):
    __tablename__ = "cities"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    name_norm = Column(String(120), nullable=False, index=True)
    region = Column(String(120), nullable=True)
    country = Column(String(8), nullable=False, default="RU")
    population = Column(Integer, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)


# --------------------
#  LOADS (заявки на груз)
# --------------------
class Load(Base):
    __tablename__ = "loads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    from_city_id = Column(Integer, ForeignKey("cities.id"), nullable=True, index=True)
    to_city_id = Column(Integer, ForeignKey("cities.id"), nullable=True, index=True)
    from_city = Column(String, nullable=False)
    to_city = Column(String, nullable=False)
    from_city_text = Column(String(255), nullable=True)
    to_city_text = Column(String(255), nullable=True)
    weight = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    weight_t = Column(Float, nullable=True)
    volume_m3 = Column(Float, nullable=True)
    pickup_lat = Column(Float, nullable=True)
    pickup_lon = Column(Float, nullable=True)
    delivery_lat = Column(Float, nullable=True)
    delivery_lon = Column(Float, nullable=True)
    required_body_type = Column(String(32), nullable=True, index=True)
    cargo_kind = Column(String(32), nullable=True, index=True)
    required_vehicle_kinds = Column(JSON, nullable=True)
    required_options = Column(JSON, nullable=True)
    adr_class = Column(String(32), nullable=True)
    adr_classes = Column(JSON, nullable=True)
    crew_required = Column(Boolean, default=False, nullable=True)
    container_size = Column(String(8), nullable=True)
    needs_crane = Column(Boolean, default=False, nullable=True)
    needs_dump = Column(Boolean, default=False, nullable=True)
    temp_required = Column(Boolean, default=False, nullable=True)
    temp_min = Column(Float, nullable=True)
    temp_max = Column(Float, nullable=True)
    loading_type = Column(String(32), nullable=True)  # side | top | rear
    price = Column(Float, nullable=False)
    total_price = Column(Float, nullable=True)
    distance_km = Column(Float, nullable=True)
    rate_per_km = Column(Float, nullable=True)
    loading_date = Column(Date, nullable=True, index=True)
    loading_time = Column(String(5), nullable=True)
    cargo_description = Column(String(255), nullable=True)
    payment_terms = Column(String(120), nullable=True)
    is_direct_customer = Column(Boolean, nullable=True)
    dimensions = Column(String(64), nullable=True)
    is_hot_deal = Column(Boolean, default=False, nullable=True)
    phone = Column(String(32), nullable=True, index=True)
    inn = Column(String(12), nullable=True, index=True)
    suggested_response = Column(Text, nullable=True)
    source = Column(String(64), nullable=True)
    status = Column(String(20), default=CargoStatus.active.value, nullable=False, index=True)  # active / expired / closed / cancelled
    is_priority = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    creator = relationship("User", back_populates="loads")
    from_city_ref = relationship("City", foreign_keys=[from_city_id])
    to_city_ref = relationship("City", foreign_keys=[to_city_id])
    bids = relationship("Bid", back_populates="load")
    messages = relationship("Message", back_populates="load")


# --------------------
#  BIDS (ставки)
# --------------------
class Bid(Base):
    __tablename__ = "bids"

    id = Column(Integer, primary_key=True, index=True)
    load_id = Column(Integer, ForeignKey("loads.id"))
    carrier_id = Column(Integer, ForeignKey("users.id"))
    price = Column(Float, nullable=False)
    comment = Column(Text, nullable=True)
    status = Column(String, default="waiting")  # waiting / accepted / rejected

    load = relationship("Load", back_populates="bids")
    carrier = relationship("User", back_populates="bids")


# --------------------
#  MESSAGES (чаты внутри заявки)
# --------------------
class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    load_id = Column(Integer, ForeignKey("loads.id"))
    sender_id = Column(Integer, ForeignKey("users.id"))
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    load = relationship("Load", back_populates="messages")
    sender = relationship("User", back_populates="messages")


# --------------------
#  RATING HISTORY (история изменений рейтинга)
# --------------------
class RatingHistory(Base):
    __tablename__ = "rating_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    points_change = Column(Integer, default=0)  # Изменение баллов (+/-)
    rating_before = Column(Float)
    rating_after = Column(Float)
    reason = Column(String, nullable=False)  # Причина изменения
    deal_id = Column(Integer, nullable=True)  # ID связанной сделки
    load_id = Column(Integer, nullable=True)  # ID заявки
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="rating_history")


# --------------------
#  COMPLAINTS (претензии/жалобы)
# --------------------
class Complaint(Base):
    __tablename__ = "complaints"

    id = Column(Integer, primary_key=True, index=True)
    complainant_id = Column(Integer, ForeignKey("users.id"))  # Кто жалуется
    defendant_id = Column(Integer, ForeignKey("users.id"))  # На кого жалуются
    load_id = Column(Integer, ForeignKey("loads.id"), nullable=True)  # Связанная заявка
    title = Column(String, nullable=False)  # Заголовок претензии
    description = Column(Text, nullable=False)  # Описание проблемы
    complaint_type = Column(String, default="general")  # general / fraud / delay / damage / payment
    status = Column(String, default="pending")  # pending / reviewed / resolved / rejected
    evidence = Column(Text, nullable=True)  # Доказательства (ссылки на фото, документы)
    admin_response = Column(Text, nullable=True)  # Ответ администратора
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    complainant = relationship(
        "User",
        foreign_keys=[complainant_id],
        back_populates="complaints_sent",
    )
    defendant = relationship(
        "User",
        foreign_keys=[defendant_id],
        back_populates="complaints_received",
    )
    load = relationship("Load")


# --------------------
#  FORUM POSTS (посты на форуме)
# --------------------
class ForumPost(Base):
    __tablename__ = "forum_posts"

    id = Column(Integer, primary_key=True, index=True)
    author_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    post_type = Column(String, default="warning")  # warning / review / discussion / complaint
    target_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # О ком пост
    target_company = Column(String, nullable=True)  # О какой компании
    target_phone = Column(String, nullable=True)  # О каком телефоне
    is_verified = Column(Boolean, default=False)  # Проверен ли модератором
    is_pinned = Column(Boolean, default=False)  # Закреплён ли пост
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    dislikes = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    author = relationship(
        "User",
        foreign_keys=[author_id],
        back_populates="forum_posts",
    )
    target_user = relationship("User", foreign_keys=[target_user_id])
    comments = relationship("ForumComment", back_populates="post", cascade="all, delete-orphan")


# --------------------
#  FORUM COMMENTS (комментарии к постам)
# --------------------
class ForumComment(Base):
    __tablename__ = "forum_comments"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("forum_posts.id"))
    author_id = Column(Integer, ForeignKey("users.id"))
    content = Column(Text, nullable=False)
    is_verified = Column(Boolean, default=False)
    likes = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    post = relationship("ForumPost", back_populates="comments")
    author = relationship(
        "User",
        foreign_keys=[author_id],
        back_populates="forum_comments",
    )


# --------------------
#  TELEGRAM LINK CODES (коды привязки Telegram)
# --------------------
class TelegramLinkCode(Base):
    """Одноразовые коды для привязки Telegram."""
    __tablename__ = "telegram_link_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(32), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", backref="telegram_link_codes")


# --------------------
#  DEALS (сделки по грузу)
# --------------------
class Deal(Base):
    __tablename__ = "deals"

    id = Column(Integer, primary_key=True, index=True)
    cargo_id = Column(Integer, ForeignKey("loads.id"), nullable=False)
    shipper_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    carrier_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(50), default="IN_PROGRESS")  # IN_PROGRESS, CONFIRMED, CONTRACTED
    carrier_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    load = relationship("Load", backref="deals")
    shipper = relationship("User", foreign_keys=[shipper_id])
    carrier = relationship("User", foreign_keys=[carrier_id])


# --------------------
#  DOCUMENTS (документы по сделке)
# --------------------
class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    deal_id = Column(Integer, ForeignKey("deals.id"), nullable=False)
    company_id_from = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    company_id_to = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    doc_type = Column(String(50), nullable=False)  # contract, ttn, upd
    status = Column(String(50), default="draft")  # draft, sent, signed, expired
    payload_json = Column(JSON, nullable=True)
    pdf_path = Column(String(500), nullable=True)
    pdf_draft_path = Column(String(500), nullable=True)
    pdf_signed_path = Column(String(500), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    signed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    deal = relationship("Deal", backref="documents")
    company_from = relationship("User", foreign_keys=[company_id_from])
    company_to = relationship("User", foreign_keys=[company_id_to])
    sign_sessions = relationship("DocumentSignSession", back_populates="document", cascade="all, delete-orphan")


class DocumentSignSession(Base):
    __tablename__ = "document_sign_sessions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    phone = Column(String(32), nullable=True)
    otp_hash = Column(String(64), nullable=True)
    otp_sent_at = Column(DateTime, nullable=True)
    otp_attempts = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    ip_first = Column(String(64), nullable=True)
    user_agent_first = Column(String(500), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    signature_png_path = Column(String(500), nullable=True)
    signature_meta_json = Column(JSON, nullable=True)
    sms_verified = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    document = relationship("Document", back_populates="sign_sessions")


# --------------------
#  DEAL SYNC (сделки фронта для синхронизации с бэкендом)
# --------------------
class DealSync(Base):
    """Хранение сделок из фронта (localStorage) для бэкапа и синхронизации."""
    __tablename__ = "deal_sync"

    id = Column(Integer, primary_key=True, index=True)
    local_id = Column(String(120), unique=True, nullable=False, index=True)  # deal_xxx с фронта
    payload = Column(JSON, nullable=False)  # весь объект Deal (cargoId, status, carrier, cargoSnapshot, ...)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --------------------
#  MODERATION REVIEW (AI-модерация сделок и документов)
# --------------------
class ModerationReview(Base):
    """Результат модерации: deal_sync или document_sync."""
    __tablename__ = "moderation_review"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", name="uq_moderation_entity"),)

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String(20), nullable=False, index=True)  # 'deal' | 'document'
    entity_id = Column(Integer, nullable=False, index=True)  # server_id (deal_sync) or document_id
    status = Column(String(20), default="pending", nullable=False)  # pending | done | error
    risk_level = Column(String(20), nullable=True, index=True)  # low | medium | high
    flags = Column(JSON, nullable=True)  # array of strings
    comment = Column(Text, nullable=True)
    recommended_action = Column(Text, nullable=True)
    model_used = Column(String(80), nullable=True)  # 'rules', 'gpt-4o-mini', etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --------------------
#  ROUTE RATE PROFILES (типичные ставки по направлениям)
# --------------------
class RouteRateProfile(Base):
    __tablename__ = "route_rate_profiles"
    __table_args__ = (UniqueConstraint("from_city_norm", "to_city_norm", name="uq_route_rate_pair"),)

    id = Column(Integer, primary_key=True, index=True)
    from_city_norm = Column(String(255), nullable=False, index=True)
    to_city_norm = Column(String(255), nullable=False, index=True)
    min_rate_per_km = Column(Integer, nullable=False)
    max_rate_per_km = Column(Integer, nullable=False)
    median_rate_per_km = Column(Integer, nullable=True)
    samples_count = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --------------------
#  COUNTERPARTY LISTS (whitelist / blacklist)
# --------------------
class CounterpartyList(Base):
    __tablename__ = "counterparty_lists"

    id = Column(Integer, primary_key=True, index=True)
    list_type = Column(String(10), nullable=False, index=True)  # white | black
    inn = Column(String(20), nullable=True, index=True)
    phone = Column(String(32), nullable=True, index=True)
    name = Column(String(255), nullable=True, index=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# --------------------
#  DEAL DOC REQUESTS (автозапрос документов)
# --------------------
class DealDocRequest(Base):
    __tablename__ = "deal_doc_requests"
    __table_args__ = (UniqueConstraint("deal_id", name="uq_deal_doc_requests_deal_id"),)

    id = Column(Integer, primary_key=True, index=True)
    deal_id = Column(Integer, nullable=False, index=True)
    status = Column(String(20), default="requested", nullable=False, index=True)  # requested | received | skipped
    required_docs = Column(JSON, nullable=False)
    reason_codes = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --------------------
#  CLOSED DEAL STATS (обучающие данные по закрытым сделкам)
# --------------------
class ClosedDealStat(Base):
    __tablename__ = "closed_deal_stats"

    id = Column(Integer, primary_key=True, index=True)
    from_city_norm = Column(String(255), nullable=False, index=True)
    to_city_norm = Column(String(255), nullable=False, index=True)
    distance_km = Column(Float, nullable=False)
    rate_per_km = Column(Float, nullable=False)
    total_rub = Column(Float, nullable=True)
    closed_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# --------------------
#  COUNTERPARTY RISK HISTORY (история рисков контрагента)
# --------------------
class CounterpartyRiskHistory(Base):
    __tablename__ = "counterparty_risk_history"

    id = Column(Integer, primary_key=True, index=True)
    counterparty_inn = Column(String(20), nullable=False, index=True)
    deal_id = Column(Integer, nullable=False, index=True)
    risk_level = Column(String(20), nullable=False, index=True)
    score_total = Column(Integer, nullable=False, default=0)
    reason_codes = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# --------------------
#  ROUTE RATE STATS (агрегированная статистика по маршруту)
# --------------------
class RouteRateStats(Base):
    __tablename__ = "route_rate_stats"
    __table_args__ = (UniqueConstraint("from_city_norm", "to_city_norm", name="uq_route_rate_stats_pair"),)

    id = Column(Integer, primary_key=True, index=True)
    from_city_norm = Column(String(255), nullable=False, index=True)
    to_city_norm = Column(String(255), nullable=False, index=True)
    mean_rate = Column(Float, nullable=True)
    median_rate = Column(Float, nullable=True)
    std_dev = Column(Float, nullable=True)
    p25 = Column(Float, nullable=True)
    p75 = Column(Float, nullable=True)
    sample_size = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --------------------
#  FRAUD GRAPH ENTITIES
# --------------------
class FraudEntity(Base):
    __tablename__ = "fraud_entities"
    __table_args__ = (UniqueConstraint("entity_type", "entity_value", name="uq_fraud_entity_type_value"),)

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String(32), nullable=False, index=True)  # inn|phone|email|card|bank_account|ip|device|name|deal
    entity_value = Column(String(512), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FraudEdge(Base):
    __tablename__ = "fraud_edges"

    id = Column(Integer, primary_key=True, index=True)
    src_entity_id = Column(Integer, ForeignKey("fraud_entities.id"), nullable=False, index=True)
    dst_entity_id = Column(Integer, ForeignKey("fraud_entities.id"), nullable=False, index=True)
    edge_type = Column(String(32), nullable=False, index=True)  # deal_link|shared_contact|payment_link|login_link|doc_link
    weight = Column(Integer, nullable=False, default=1)
    evidence = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FraudComponent(Base):
    __tablename__ = "fraud_components"
    __table_args__ = (UniqueConstraint("component_key", name="uq_fraud_component_key"),)

    id = Column(Integer, primary_key=True, index=True)
    component_key = Column(String(128), nullable=False, index=True)
    size = Column(Integer, nullable=False, default=0)
    risk_score = Column(Integer, nullable=False, default=0)
    high_risk_nodes = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FraudEntityComponent(Base):
    __tablename__ = "fraud_entity_components"
    __table_args__ = (UniqueConstraint("entity_id", name="uq_fraud_entity_component_entity"),)

    id = Column(Integer, primary_key=True, index=True)
    entity_id = Column(Integer, ForeignKey("fraud_entities.id"), nullable=False, index=True)
    component_id = Column(Integer, ForeignKey("fraud_components.id"), nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FraudSignal(Base):
    __tablename__ = "fraud_signals"

    id = Column(Integer, primary_key=True, index=True)
    signal_type = Column(String(40), nullable=False, index=True)  # complaint|chargeback|doc_forgery|...
    entity_id = Column(Integer, ForeignKey("fraud_entities.id"), nullable=True, index=True)
    deal_id = Column(Integer, nullable=True, index=True)
    severity = Column(Integer, nullable=False, default=1)  # 1..5
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class EnforcementDecision(Base):
    __tablename__ = "enforcement_decisions"

    id = Column(Integer, primary_key=True, index=True)
    scope = Column(String(32), nullable=False, index=True)  # deal|counterparty|entity|component
    scope_id = Column(String(128), nullable=False, index=True)
    decision = Column(String(24), nullable=False, index=True)  # allow|soft_block|hard_block|manual_review
    reason_codes = Column(JSON, nullable=False)
    confidence = Column(Integer, nullable=False, default=0)  # 0..100
    created_by = Column(String(64), nullable=False, default="system")
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AntifraudModel(Base):
    __tablename__ = "antifraud_models"

    id = Column(Integer, primary_key=True, index=True)
    model_type = Column(String(24), nullable=False, default="logreg")
    version = Column(Integer, nullable=False, default=1, index=True)
    weights = Column(JSON, nullable=False)
    metrics = Column(JSON, nullable=True)
    trained_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False, index=True)


# --------------------
#  DOCUMENT SYNC (документы по сделкам для хранения на сервере)
# --------------------
class DocumentSync(Base):
    """Метаданные и путь к PDF документа (договор, ТТН, УПД) по deal_sync."""
    __tablename__ = "document_sync"

    id = Column(Integer, primary_key=True, index=True)
    deal_server_id = Column(Integer, nullable=False, index=True)  # deal_sync.id
    doc_type = Column(String(20), nullable=False)  # CONTRACT, TTN, UPD
    status = Column(String(20), default="draft")  # draft, final
    file_path = Column(String(500), nullable=True)  # путь к PDF на диске
    file_hash = Column(String(64), nullable=True)  # опционально: sha256 для версии
    created_at = Column(DateTime, default=datetime.utcnow)


# --------------------
#  AUDIT EVENTS (история действий по entity)
# --------------------
class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String(50), nullable=False, index=True)  # application, cargo, deal
    entity_id = Column(Integer, nullable=False, index=True)
    action = Column(String(80), nullable=False)
    actor_role = Column(String(50), nullable=True, index=True)
    actor_user_id = Column(Integer, nullable=True, index=True)
    meta_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# --------------------
#  SHIPMENTS REGISTRY (учёт перевозок)
# --------------------
class Shipment(Base):
    __tablename__ = "shipments"

    id = Column(Integer, primary_key=True, index=True)
    owner_company_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    ship_date = Column(Date, nullable=False, index=True)

    client_name = Column(String(255), nullable=False)
    client_inn = Column(String(20), nullable=True, index=True)

    from_city = Column(String(120), nullable=False, index=True)
    to_city = Column(String(120), nullable=False, index=True)
    cargo_brief = Column(Text, nullable=False)

    carrier_name = Column(String(255), nullable=False)
    carrier_inn = Column(String(20), nullable=True, index=True)

    client_amount = Column(Float, nullable=False, default=0.0)
    carrier_amount = Column(Float, nullable=False, default=0.0)

    status = Column(String(20), nullable=False, default="draft", index=True)  # draft|in_progress|done|closed
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner_company = relationship("User")
    payments = relationship("Payment", back_populates="shipment", cascade="all, delete-orphan")
    attachments = relationship("Attachment", back_populates="shipment", cascade="all, delete-orphan")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=False, index=True)
    direction = Column(String(10), nullable=False, index=True)  # in | out
    planned_date = Column(Date, nullable=False, index=True)
    planned_amount = Column(Float, nullable=False)
    actual_date = Column(Date, nullable=True, index=True)
    actual_amount = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="planned", index=True)  # planned | paid | overdue
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    shipment = relationship("Shipment", back_populates="payments")


class Attachment(Base):
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=False, index=True)
    file_path = Column(String(500), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_type = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    shipment = relationship("Shipment", back_populates="attachments")


# --------------------
#  CONSOLIDATION PLANS (сборные рейсы)
# --------------------
class ConsolidationPlan(Base):
    __tablename__ = "consolidation_plans"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="draft", index=True)  # draft | confirmed
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    total_weight = Column(Float, nullable=False, default=0.0)
    total_volume = Column(Float, nullable=False, default=0.0)
    score = Column(Float, nullable=False, default=0.0)
    detour_km = Column(Float, nullable=False, default=0.0)
    explain_json = Column(JSON, nullable=True)

    vehicle = relationship("Vehicle")
    creator = relationship("User")
    items = relationship("ConsolidationPlanItem", back_populates="plan", cascade="all, delete-orphan")


class ConsolidationPlanItem(Base):
    __tablename__ = "consolidation_plan_items"
    __table_args__ = (
        UniqueConstraint("plan_id", "seq", name="uq_consolidation_plan_items_plan_seq"),
    )

    plan_id = Column(Integer, ForeignKey("consolidation_plans.id", ondelete="CASCADE"), primary_key=True)
    cargo_id = Column(Integer, ForeignKey("loads.id", ondelete="CASCADE"), primary_key=True, index=True)
    seq = Column(Integer, nullable=False, default=1)

    plan = relationship("ConsolidationPlan", back_populates="items")
    cargo = relationship("Load")

