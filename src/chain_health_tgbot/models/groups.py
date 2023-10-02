from sqlalchemy import Column, Integer, String, ForeignKeyConstraint

from chain_health_tgbot.models.base import Base


class Groups(Base):
    __tablename__ = "groups"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(length=255))
    user_id = Column(Integer, nullable=False)
