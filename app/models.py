from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Bot(Base):
    __tablename__ = "bots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    token = Column(String, nullable=False, unique=True)
    company_name = Column(String, nullable=True)
    base_url = Column(String, nullable=True)
    one_c_login = Column(String, nullable=True)
    one_c_password = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    users = relationship("User", back_populates="bot", cascade="all, delete-orphan")


class User(Base):
    """Таъминотчи Telegram akkaunti ↔ 1C supplier bog'lanishi.

    ``client_id`` — 1C dagi taminotchi kodi (checkNumber javobidagi ``id``).
    ``language`` — bot interfeysi tili: "uz" / "ru" (TZ 1: тил танлаш).
    """
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_bot_id_telegram_id", "bot_id", "telegram_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, nullable=False)
    phone_number = Column(String, nullable=True)
    client_id = Column(String, nullable=True)
    language = Column(String(4), nullable=False, default="uz", server_default="uz")
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    bot = relationship("Bot", back_populates="users")


class WebSession(Base):
    """Brauzer sessiyasi (/getsession havolasi) — WebApp'ga Telegram tashqarisidan kirish."""
    __tablename__ = "web_sessions"
    __table_args__ = (
        Index("ix_web_sessions_bot_id_telegram_id", "bot_id", "telegram_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String, nullable=False, unique=True, index=True)
    telegram_id = Column(BigInteger, nullable=False)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=False)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    username = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
