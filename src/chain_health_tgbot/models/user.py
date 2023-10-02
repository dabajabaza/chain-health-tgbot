from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime

from chain_health_tgbot.models.base import Base


class Users(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, doc="Telegram user_id")
    username = Column(String(length=255), doc="Telegram username")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow())
