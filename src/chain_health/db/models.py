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
    """A Telegram user admitted to the bot (admin, invitee, or auto-registered)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=False, comment="Telegram user id"
    )
    username: Mapped[str | None] = mapped_column(
        default=None, comment="Telegram @username at last contact, if the user has one"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, comment="UTC instant the user was first registered"
    )
    # No FK constraint: groups.user_id -> users.id already closes the loop, and SQLite
    # cannot ALTER TABLE to add a constraint afterwards, so this stays app-enforced.
    current_group_id: Mapped[int | None] = mapped_column(
        default=None, comment="groups.id the user is currently working in (app-enforced FK)"
    )
    pinned_message_id: Mapped[int | None] = mapped_column(
        default=None, comment="Telegram message id of the pinned status message in the user's chat"
    )
    last_reminder_sent_dt: Mapped[date | None] = mapped_column(
        Date, default=None, comment="Local calendar date the last daily reminder was sent"
    )
    invited_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None, comment="users.id that invited this user, if any"
    )

    groups: Mapped[list[Group]] = relationship(
        back_populates="user",
        foreign_keys="Group.user_id",
        cascade="all, delete-orphan",
        doc="All groups (bikes) owned by this user",
    )


class Invite(Base):
    """A one-time, time-limited invite code redeemable via a /start deep link."""

    __tablename__ = "invites"

    code: Mapped[str] = mapped_column(
        primary_key=True, comment="URL-safe random invite code, carried in the /start deep link"
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"), comment="users.id of the admin who issued the invite"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, comment="UTC instant the invite was issued"
    )
    expires_at: Mapped[datetime] = mapped_column(
        UTCDateTime, comment="UTC instant after which the invite can no longer be redeemed"
    )
    used_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        default=None,
        comment="users.id that redeemed the invite; NULL while unused",
    )
    used_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, default=None, comment="UTC instant the invite was redeemed; NULL while unused"
    )


class Group(Base):
    """A set of chains rotated together — in practice, one bike."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True, comment="Surrogate primary key")
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, comment="Owning users.id"
    )
    name: Mapped[str] = mapped_column(comment="User-visible group name")
    # No column-level default: GarageService.create_group always passes an explicit
    # value (domain.constants.DEFAULT_CYCLE_LIMIT_KM when the caller doesn't override
    # it) — see docs/ARCHITECTURE.md D12 on why db/models.py doesn't import domain/.
    default_cycle_limit_km: Mapped[float] = mapped_column(
        Float, comment="Cycle limit (km) that newly added chains inherit"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, comment="UTC instant the group was created"
    )

    user: Mapped[User] = relationship(
        back_populates="groups", foreign_keys=[user_id], doc="Owning user"
    )
    chains: Mapped[list[Chain]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        doc="All chains in this group, retired ones included",
    )


class Chain(Base):
    """A physical bicycle chain tracked within a group."""

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

    id: Mapped[int] = mapped_column(primary_key=True, comment="Surrogate primary key")
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id"), index=True, comment="Owning groups.id"
    )
    name: Mapped[str] = mapped_column(comment="User-visible chain name")
    cycle_limit_km: Mapped[float] = mapped_column(
        Float, comment="Mileage (km) per cycle after which the chain is due for rotation"
    )
    resource_km: Mapped[float | None] = mapped_column(
        Float,
        default=None,
        comment="Total lifetime mileage (km) after which the chain is considered worn out",
    )
    is_active: Mapped[bool] = mapped_column(
        default=False, comment="Whether this is the group's currently mounted chain"
    )
    is_retired: Mapped[bool] = mapped_column(
        default=False, comment="Whether the chain was permanently taken out of rotation"
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, default=None, comment="UTC instant the chain was retired; NULL while in use"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, comment="UTC instant the chain was added"
    )

    group: Mapped[Group] = relationship(back_populates="chains", doc="Owning group")
    rotations: Mapped[list[Rotation]] = relationship(
        back_populates="chain",
        cascade="all, delete-orphan",
        doc="Every activation of this chain, newest defining the current cycle",
    )
    rides: Mapped[list[Ride]] = relationship(
        back_populates="chain",
        cascade="all, delete-orphan",
        doc="All rides logged against this chain",
    )


class Rotation(Base):
    """Records when a chain became active; the cycle boundary for that chain.

    ``activated_dt`` is the local calendar date (same clock as ``Ride.ride_dt``);
    ``activated_at`` is the UTC instant (same clock as ``Ride.created_at``). The
    cycle predicate in RideService compares like with like, never mixing the two.
    """

    __tablename__ = "rotations"
    __table_args__ = (Index("ix_rotations_chain_activated", "chain_id", "activated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, comment="Surrogate primary key")
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id"), index=True, comment="groups.id the rotation happened in"
    )
    chain_id: Mapped[int] = mapped_column(
        ForeignKey("chains.id"), index=True, comment="chains.id that became active"
    )
    # No column-level default on either half: GarageService._activate always sets
    # both explicitly from one utcnow() call. A lone default here would invite a
    # future caller to omit activated_at and get it from a second, separate
    # "now" — exactly the midnight-skew mismatch D9's composite boundary guards
    # against.
    activated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, comment="UTC instant of activation (same clock as rides.created_at)"
    )
    activated_dt: Mapped[date] = mapped_column(
        Date, comment="Local calendar date of activation (same clock as rides.ride_dt)"
    )

    chain: Mapped[Chain] = relationship(back_populates="rotations", doc="Activated chain")


class Ride(Base):
    """A single logged ride: a distance on a date, attributed to one chain."""

    __tablename__ = "rides"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_rides_source_external_id"),
        Index("ix_rides_chain_created", "chain_id", "created_at"),
        Index("ix_rides_user_created", "user_id", "created_at"),
        Index("ix_rides_chain_ride_dt", "chain_id", "ride_dt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="Surrogate primary key")
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, comment="users.id who logged the ride"
    )
    chain_id: Mapped[int] = mapped_column(
        ForeignKey("chains.id"), index=True, comment="chains.id the ride's mileage counts toward"
    )
    ride_dt: Mapped[date] = mapped_column(Date, comment="Local calendar date the ride happened on")
    distance_km: Mapped[float] = mapped_column(Float, comment="Ride distance in kilometers")
    # No column-level default: RideService.add_ride always passes an explicit value
    # (domain.enums.RideSource.MANUAL by default) — see D12 above.
    source: Mapped[str] = mapped_column(
        comment="Origin of the ride record (domain.enums.RideSource value)"
    )
    external_id: Mapped[str | None] = mapped_column(
        default=None,
        comment="Ride id in the external source; unique per source, NULL for manual entries",
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, comment="UTC instant the record was created"
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=utcnow,
        onupdate=utcnow,
        comment="UTC instant of the last edit",
    )

    chain: Mapped[Chain] = relationship(back_populates="rides", doc="Chain the ride counts toward")


class ProcessedUpdate(Base):
    """Marks a Telegram update as applied, so a redelivery cannot repeat it.

    Telegram treats an update as delivered only once the offset is confirmed,
    and that confirmation rides on the *next* getUpdates call. A process killed
    in between — which is exactly what a deploy does — receives the same
    update_id again, and the ride would be logged twice. A duplicate is worse
    than a loss here: a dropped message the user simply resends, whereas a
    double entry silently corrupts mileage.

    The row is written inside the same transaction as the business change (one
    request scope is one transaction, see RequestProvider), so no state exists
    where the change landed but the mark did not.
    """

    __tablename__ = "processed_updates"

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=False, comment="Telegram update id"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, comment="UTC instant the update was applied"
    )
