from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Date, Float, Text, Enum, Boolean, BigInteger, UniqueConstraint
from sqlalchemy.types import JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.database import Base
import enum


# Роли пользователей
class UserRole(str, enum.Enum):
    shipper = "shipper"
    carrier = "carrier"
    admin = "admin"


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
    
    phone = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.shipper)
    
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
    vehicles = relationship("Vehicle", back_populates="carrier")


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

    id = Column(Integer, primary_key=True, index=True)
    carrier_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    body_type = Column(String(32), nullable=False, index=True)  # тент / реф / площадка / коники
    capacity_tons = Column(Float, nullable=False)
    volume_m3 = Column(Float, nullable=False)
    location_city = Column(String(120), nullable=False, index=True)
    location_region = Column(String(120), nullable=True, index=True)
    available_from = Column(Date, nullable=False, index=True)
    rate_per_km = Column(Float, nullable=True)
    status = Column(String(20), default="active", index=True)  # active / archived

    # AI-слой: сохраняем результат оценки на момент публикации
    ai_risk_level = Column(String(20), default="low", index=True)  # low / medium / high
    ai_score = Column(Integer, default=0)
    ai_warnings = Column(JSON, nullable=True)
    ai_market_rate = Column(Float, nullable=True)  # среднерыночная ставка для сравнения
    ai_idle_ratio = Column(Float, nullable=True)  # доля простаивающего парка перевозчика

    created_at = Column(DateTime, default=datetime.utcnow)

    carrier = relationship("User", back_populates="vehicles")


# --------------------
#  LOADS (заявки на груз)
# --------------------
class Load(Base):
    __tablename__ = "loads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    from_city = Column(String, nullable=False)
    to_city = Column(String, nullable=False)
    weight = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    price = Column(Float, nullable=False)
    status = Column(String, default="open")  # open / covered / closed
    created_at = Column(DateTime, default=datetime.utcnow)

    creator = relationship("User", back_populates="loads")
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
    doc_type = Column(String(50), nullable=False)  # contract, ttn, upd
    status = Column(String(50), default="draft")  # draft, signed
    pdf_path = Column(String(500), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    signed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    deal = relationship("Deal", backref="documents")


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
