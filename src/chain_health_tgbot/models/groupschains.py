from sqlalchemy import Column, Integer, Boolean, UniqueConstraint, ForeignKeyConstraint

from chain_health_tgbot.models.base import Base


class GroupsChains(Base):
    __tablename__ = "groupschains"
    __table_args__ = (
        ForeignKeyConstraint(["group_id"], ["groups.id"]),
        ForeignKeyConstraint(["chain_id"], ["chains.id"]),
        UniqueConstraint("group_id", "chain_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, nullable=False)
    chain_id = Column(Integer, nullable=False)
    mileage_cycle_current = Column(Integer, nullable=False, default=0,
                                   doc="Счётчик сбрасывается в ноль, начиная новый цикл, "
                                       "только вручную, когда пользователь делает активной следующую цепь."
                                       "Обнуляется при этом только новая активная цепь")
    mileage_cycle_limit = Column(Integer, nullable=False, default=300)
    is_active = Column(Boolean, nullable=False, default=False)
