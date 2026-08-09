from datetime import timedelta

import pytest

from chain_health.domain.errors import NotFoundError
from chain_health.timeutils import local_today
from tests.helpers.factories import make_group_with_chain, make_services, make_user


async def test_total_km_sums_all_rides(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    _, chain = await make_group_with_chain(garage, user.id)
    today = local_today(settings.timezone)

    await rides.add_ride(user_id=user.id, chain=chain, distance_km=40, ride_dt=today)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=25.5, ride_dt=today)

    assert await rides.total_km(chain) == 65.5
    assert await rides.cycle_km(chain) == 65.5


async def test_cycle_resets_only_for_newly_activated_chain(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    group, chain_a = await make_group_with_chain(garage, user.id)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain_a, distance_km=100, ride_dt=today)

    chain_b = await garage.add_chain(group, "Цепь Б")
    await garage.rotate(chain_b)

    assert await rides.cycle_km(chain_a) == 100
    assert await rides.cycle_km(chain_b) == 0
    assert await rides.total_km(chain_a) == 100
    assert await rides.total_km(chain_b) == 0

    await rides.add_ride(user_id=user.id, chain=chain_b, distance_km=30, ride_dt=today)
    assert await rides.cycle_km(chain_b) == 30
    assert await rides.cycle_km(chain_a) == 100


async def test_edit_ride_updates_totals(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    _, chain = await make_group_with_chain(garage, user.id)
    today = local_today(settings.timezone)
    ride = await rides.add_ride(user_id=user.id, chain=chain, distance_km=40, ride_dt=today)

    await rides.edit_ride(ride, distance_km=55)

    assert await rides.total_km(chain) == 55


async def test_delete_ride_updates_totals(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    _, chain = await make_group_with_chain(garage, user.id)
    today = local_today(settings.timezone)
    ride1 = await rides.add_ride(user_id=user.id, chain=chain, distance_km=40, ride_dt=today)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=10, ride_dt=today)

    await rides.delete_ride(ride1)

    assert await rides.total_km(chain) == 10


async def test_editing_a_ride_does_not_move_it_between_cycles(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    group, chain_a = await make_group_with_chain(garage, user.id)
    today = local_today(settings.timezone)
    ride = await rides.add_ride(user_id=user.id, chain=chain_a, distance_km=40, ride_dt=today)

    chain_b = await garage.add_chain(group, "Б")
    await garage.rotate(chain_b)
    await garage.rotate(chain_a)  # chain_a's cycle boundary moves to "now"
    await rides.edit_ride(ride, distance_km=999)

    assert await rides.cycle_km(chain_a) == 0  # edited ride stays before the new boundary
    assert await rides.total_km(chain_a) == 999


# --- R6: composite cycle boundary (docs/ARCHITECTURE.md D9) ---


async def test_backdated_ride_before_activation_date_excluded_from_cycle_but_in_total(
    session, settings
):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    _, chain = await make_group_with_chain(garage, user.id)  # activated "today"
    yesterday = local_today(settings.timezone) - timedelta(days=1)

    await rides.add_ride(user_id=user.id, chain=chain, distance_km=50, ride_dt=yesterday)

    assert await rides.total_km(chain) == 50
    assert await rides.cycle_km(chain) == 0


async def test_backdated_ride_after_activation_date_is_in_cycle(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    _, chain = await make_group_with_chain(garage, user.id)  # activated "today"
    tomorrow = local_today(settings.timezone) + timedelta(days=1)

    # A ride dated tomorrow (e.g. entered from a different local timezone) is
    # still "after" the activation date, so it counts.
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=30, ride_dt=tomorrow)

    assert await rides.cycle_km(chain) == 30


async def test_ride_on_activation_day_recorded_after_activation_is_in_cycle(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    _, chain = await make_group_with_chain(garage, user.id)
    today = local_today(settings.timezone)

    # created_at defaults to "now", which is necessarily after the chain's
    # own activation instant (activated moments earlier in this same test).
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=15, ride_dt=today)

    assert await rides.cycle_km(chain) == 15


async def test_same_day_rotation_back_to_a_previous_chain_restarts_its_cycle(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    group, chain_a = await make_group_with_chain(garage, user.id)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain_a, distance_km=100, ride_dt=today)

    chain_b = await garage.add_chain(group, "Б")
    await garage.rotate(chain_b)
    await garage.rotate(chain_a)  # A -> B -> A, all today

    assert await rides.cycle_km(chain_a) == 0  # cycle restarted by the second activation
    assert await rides.total_km(chain_a) == 100  # lifetime total unaffected


async def test_chain_without_any_rotation_counts_every_ride(session, settings):
    """A second chain added to a group that already has an active one is never
    activated itself, so it has no Rotation row at all — cycle_km must fall
    back to the full lifetime total rather than erroring.
    """
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    group, _chain_a = await make_group_with_chain(garage, user.id)
    spare = await garage.add_chain(group, "Запасная")
    assert spare.is_active is False
    yesterday = local_today(settings.timezone) - timedelta(days=1)

    await rides.add_ride(user_id=user.id, chain=spare, distance_km=77, ride_dt=yesterday)

    assert await rides.total_km(spare) == 77
    assert await rides.cycle_km(spare) == 77


# --- Ownership (R1) ---


async def test_require_ride_rejects_a_ride_belonging_to_another_user(session, settings):
    owner = await make_user(session, user_id=1)
    await make_user(session, user_id=2)
    garage, rides = make_services(session, settings)
    _, chain = await make_group_with_chain(garage, owner.id)
    today = local_today(settings.timezone)
    ride = await rides.add_ride(user_id=owner.id, chain=chain, distance_km=10, ride_dt=today)

    with pytest.raises(NotFoundError):
        await rides.require_ride(2, ride.id)


async def test_require_ride_rejects_an_id_that_does_not_exist(session, settings):
    await make_user(session, user_id=1)
    _garage, rides = make_services(session, settings)

    with pytest.raises(NotFoundError):
        await rides.require_ride(1, 999)


async def test_require_ride_returns_the_ride_for_its_owner(session, settings):
    owner = await make_user(session, user_id=1)
    garage, rides = make_services(session, settings)
    _, chain = await make_group_with_chain(garage, owner.id)
    today = local_today(settings.timezone)
    ride = await rides.add_ride(user_id=owner.id, chain=chain, distance_km=10, ride_dt=today)

    found = await rides.require_ride(owner.id, ride.id)

    assert found.id == ride.id
