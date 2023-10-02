from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKeyConstraint

from chain_health_tgbot.models.base import Base


class Chains(Base):
    __tablename__ = "chains"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(length=255), nullable=False)
    description = Column(String(length=1024))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow())
    is_deleted = Column(Boolean, nullable=False, default=False)
    expired_at = Column(DateTime)
    mileage_total = Column(Integer, nullable=False, default=0)
    user_id = Column(Integer, nullable=False)
