from datetime import timedelta

import pytest

from chain_health.domain.enums import RideSource
from chain_health.domain.errors import NoActiveChainError
from chain_health.timeutils import local_today
from tests.helpers.factories import (
    make_group_with_chain,
    make_mileage_service,
    make_services,
    make_status_service,
    make_user,
)


async def _mileage_service(session, settings):
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    return garage, rides, make_mileage_service(garage, rides, status_service, settings)


async def test_record_ride_uses_the_local_today_and_the_active_chain(session, settings):
    user = await make_user(session)
    garage, rides, mileage = await _mileage_service(session, settings)
    _, chain = await make_group_with_chain(garage, user.id)

    recorded = await mileage.record_ride(user_id=user.id, distance_km=42)

    assert recorded.ride.chain_id == chain.id
    assert recorded.ride.ride_dt == local_today(settings.timezone)
    assert recorded.ride.source == RideSource.MANUAL


async def test_record_ride_without_a_current_group_raises(session, settings):
    user = await make_user(session)
    _garage, _rides, mileage = await _mileage_service(session, settings)

    with pytest.raises(NoActiveChainError):
        await mileage.record_ride(user_id=user.id, distance_km=10)


async def test_record_ride_when_the_only_chain_was_retired_raises(session, settings):
    user = await make_user(session)
    garage, _rides, mileage = await _mileage_service(session, settings)
    _, chain = await make_group_with_chain(garage, user.id)
    await garage.retire_chain(chain)

    with pytest.raises(NoActiveChainError):
        await mileage.record_ride(user_id=user.id, distance_km=10)


async def test_record_ride_returns_a_status_that_includes_the_new_ride(session, settings):
    user = await make_user(session)
    garage, _rides, mileage = await _mileage_service(session, settings)
    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=100)

    recorded = await mileage.record_ride(user_id=user.id, distance_km=42)

    assert recorded.status.chain.id == chain.id
    assert recorded.status.cycle_km == 42
    assert recorded.status.total_km == 42
    assert recorded.status.over_limit is False


async def test_record_ride_lands_on_the_current_group_when_the_user_has_two(session, settings):
    user = await make_user(session)
    garage, _rides, mileage = await _mileage_service(session, settings)
    _group1, _chain1 = await make_group_with_chain(garage, user.id, group_name="A")
    group2, chain2 = await make_group_with_chain(garage, user.id, group_name="B")
    await garage.set_current_group(user.id, group2)

    recorded = await mileage.record_ride(user_id=user.id, distance_km=5)

    assert recorded.group.id == group2.id
    assert recorded.ride.chain_id == chain2.id


async def test_record_ride_passes_source_and_external_id_through(session, settings):
    user = await make_user(session)
    garage, _rides, mileage = await _mileage_service(session, settings)
    await make_group_with_chain(garage, user.id)

    recorded = await mileage.record_ride(
        user_id=user.id, distance_km=15, source="strava", external_id="abc123"
    )

    assert recorded.ride.source == "strava"
    assert recorded.ride.external_id == "abc123"


async def test_record_ride_accepts_an_explicit_ride_dt(session, settings):
    user = await make_user(session)
    garage, _rides, mileage = await _mileage_service(session, settings)
    await make_group_with_chain(garage, user.id)
    explicit_date = local_today(settings.timezone) - timedelta(days=3)

    recorded = await mileage.record_ride(user_id=user.id, distance_km=15, ride_dt=explicit_date)

    assert recorded.ride.ride_dt == explicit_date
