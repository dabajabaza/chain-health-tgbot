from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    Float,
    ForeignKey,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chain_health.db.base import Base
from chain_health.db.types import UTCDateTime
from chain_health.timeutils import utcnow

# Column naming convention: a `_at` suffix means second-or-finer precision
# (stored via UTCDateTime, always timezone-aware in Python); a `_dt` suffix
# means a calendar date with no time component (stored via Date, no timezone
# concept applies).


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False, doc="Telegram user id")
    username: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # No FK constraint: groups.user_id -> users.id already closes the loop, and SQLite
    # cannot ALTER TABLE to add a constraint afterwards, so this stays app-enforced.
    current_group_id: Mapped[int | None] = mapped_column(default=None)
    pinned_message_id: Mapped[int | None] = mapped_column(default=None)
    last_reminder_sent_dt: Mapped[date | None] = mapped_column(Date, default=None)
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)

    groups: Mapped[list[Group]] = relationship(
        back_populates="user", foreign_keys="Group.user_id", cascade="all, delete-orphan"
    )


class Invite(Base):
    __tablename__ = "invites"

    code: Mapped[str] = mapped_column(primary_key=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str]
    # No column-level default: GarageService.create_group always passes an explicit
    # value (domain.constants.DEFAULT_CYCLE_LIMIT_KM when the caller doesn't override
    # it) — see docs/ARCHITECTURE.md D12 on why db/models.py doesn't import domain/.
    default_cycle_limit_km: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="groups", foreign_keys=[user_id])
    chains: Mapped[list[Chain]] = relationship(back_populates="group", cascade="all, delete-orphan")


class Chain(Base):
    __tablename__ = "chains"
    __table_args__ = (
        # Partial unique index: at most one active chain per group.
        Index(
            "ix_chains_one_active_per_group",
            "group_id",
            unique=True,
            sqlite_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    name: Mapped[str]
    cycle_limit_km: Mapped[float] = mapped_column(Float)
    resource_km: Mapped[float | None] = mapped_column(Float, default=None)
    is_active: Mapped[bool] = mapped_column(default=False)
    is_retired: Mapped[bool] = mapped_column(default=False)
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    group: Mapped[Group] = relationship(back_populates="chains")
    rotations: Mapped[list[Rotation]] = relationship(
        back_populates="chain", cascade="all, delete-orphan"
    )
    rides: Mapped[list[Ride]] = relationship(back_populates="chain", cascade="all, delete-orphan")


class Rotation(Base):
    """Records when a chain became active; the cycle boundary for that chain.

    ``activated_dt`` is the local calendar date (same clock as ``Ride.ride_dt``);
    ``activated_at`` is the UTC instant (same clock as ``Ride.created_at``). The
    cycle predicate in RideService compares like with like, never mixing the two.
    """

    __tablename__ = "rotations"
    __table_args__ = (Index("ix_rotations_chain_activated", "chain_id", "activated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"), index=True)
    # No column-level default on either half: GarageService._activate always sets
    # both explicitly from one utcnow() call. A lone default here would invite a
    # future caller to omit activated_at and get it from a second, separate
    # "now" — exactly the midnight-skew mismatch D9's composite boundary guards
    # against.
    activated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    activated_dt: Mapped[date] = mapped_column(Date)

    chain: Mapped[Chain] = relationship(back_populates="rotations")


class Ride(Base):
    __tablename__ = "rides"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_rides_source_external_id"),
        Index("ix_rides_chain_created", "chain_id", "created_at"),
        Index("ix_rides_user_created", "user_id", "created_at"),
        Index("ix_rides_chain_ride_dt", "chain_id", "ride_dt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"), index=True)
    ride_dt: Mapped[date] = mapped_column(Date)
    distance_km: Mapped[float] = mapped_column(Float)
    # No column-level default: RideService.add_ride always passes an explicit value
    # (domain.enums.RideSource.MANUAL by default) — see D12 above.
    source: Mapped[str] = mapped_column()
    external_id: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    chain: Mapped[Chain] = relationship(back_populates="rides")
