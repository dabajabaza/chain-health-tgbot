from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from chain_health.db.models import Chain
from chain_health.domain.errors import InvalidOperationError, NotFoundError
from chain_health.timeutils import local_today
from tests.factories import make_group_with_chain, make_services, make_user

TODAY = date(2026, 7, 30)


async def test_first_chain_added_becomes_active_with_group_default_limit(session, settings):
    user = await make_user(session)
    garage, _ = make_services(session, settings)
    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=250)

    assert chain is not None
    assert chain.is_active is True
    assert chain.cycle_limit_km == 250


async def test_chain_limit_can_be_overridden(session, settings):
    user = await make_user(session)
    garage, _ = make_services(session, settings)
    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=300)

    await garage.set_chain_limit(chain, 200)

    assert chain.cycle_limit_km == 200


async def test_rotation_options_picks_lowest_total(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    group, _chain_a = await make_group_with_chain(garage, user.id)
    chain_b = await garage.add_chain(group, "Б")
    chain_c = await garage.add_chain(group, "В")

    await rides.add_ride(user_id=user.id, chain=chain_b, distance_km=500, ride_dt=TODAY)
    await rides.add_ride(user_id=user.id, chain=chain_c, distance_km=10, ride_dt=TODAY)

    options = await garage.rotation_options(group)

    assert options.recommended is not None
    assert options.recommended.id == chain_c.id
    assert {c.id for c in options.candidates} == {chain_b.id, chain_c.id}
    assert options.totals[chain_b.id] == 500
    assert options.totals[chain_c.id] == 10


async def test_rotation_options_excludes_active_and_retired_chains(session, settings):
    user = await make_user(session)
    garage, _ = make_services(session, settings)
    group, chain_a = await make_group_with_chain(garage, user.id)
    chain_b = await garage.add_chain(group, "Б")
    await garage.retire_chain(chain_b)

    options = await garage.rotation_options(group)

    assert options.candidates == []
    assert options.recommended is None
    assert chain_a.is_active is True


async def test_rotation_options_recommends_none_when_no_candidates(session, settings):
    user = await make_user(session)
    garage, _ = make_services(session, settings)
    group, _chain = await make_group_with_chain(garage, user.id)

    options = await garage.rotation_options(group)

    assert options.candidates == []
    assert options.recommended is None


async def test_rotation_deactivates_previous_chain(session, settings):
    user = await make_user(session)
    garage, _ = make_services(session, settings)
    group, chain_a = await make_group_with_chain(garage, user.id)
    chain_b = await garage.add_chain(group, "Б")

    await garage.rotate(chain_b)

    active = await garage.active_chain(group)
    assert active is not None
    assert active.id == chain_b.id
    assert chain_a.is_active is False


async def test_rotating_an_already_active_chain_raises_instead_of_resetting_its_cycle(
    session, settings
):
    """A rotation-candidates keyboard stays valid forever in chat history — a
    stale/duplicate confirm click for a chain that's already active must not
    silently insert a fresh Rotation and zero the cycle it's mid-way through.
    """
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    _group, chain = await make_group_with_chain(garage, user.id)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=100, ride_dt=today)

    with pytest.raises(InvalidOperationError):
        await garage.rotate(chain)

    assert await rides.cycle_km(chain) == 100


async def test_retired_chain_is_excluded_from_listing_and_rotation_raises(session, settings):
    user = await make_user(session)
    garage, _ = make_services(session, settings)
    group, chain_a = await make_group_with_chain(garage, user.id)
    chain_b = await garage.add_chain(group, "Б")

    await garage.retire_chain(chain_b)

    chains = await garage.list_chains(group)
    assert [c.id for c in chains] == [chain_a.id]

    with pytest.raises(InvalidOperationError):
        await garage.rotate(chain_b)


async def test_partial_unique_index_prevents_two_active_chains(session, settings):
    user = await make_user(session)
    garage, _ = make_services(session, settings)
    group, _chain = await make_group_with_chain(garage, user.id)

    rogue = Chain(group_id=group.id, name="Rogue", cycle_limit_km=300, is_active=True)
    session.add(rogue)

    with pytest.raises(IntegrityError):
        await session.flush()


# --- Ownership (R1) ---


async def test_require_group_rejects_a_group_belonging_to_another_user(session, settings):
    owner = await make_user(session, user_id=1)
    await make_user(session, user_id=2)
    garage, _ = make_services(session, settings)
    group, _chain = await make_group_with_chain(garage, owner.id)

    with pytest.raises(NotFoundError):
        await garage.require_group(2, group.id)


async def test_require_group_rejects_an_id_that_does_not_exist(session, settings):
    await make_user(session, user_id=1)
    garage, _ = make_services(session, settings)

    with pytest.raises(NotFoundError):
        await garage.require_group(1, 999)


async def test_require_group_returns_the_group_for_its_owner(session, settings):
    owner = await make_user(session, user_id=1)
    garage, _ = make_services(session, settings)
    group, _chain = await make_group_with_chain(garage, owner.id)

    found = await garage.require_group(owner.id, group.id)

    assert found.id == group.id


async def test_require_chain_rejects_a_chain_from_another_users_group(session, settings):
    owner = await make_user(session, user_id=1)
    await make_user(session, user_id=2)
    garage, _ = make_services(session, settings)
    _group, chain = await make_group_with_chain(garage, owner.id)

    with pytest.raises(NotFoundError):
        await garage.require_chain(2, chain.id)


async def test_require_chain_finds_a_retired_chain_for_its_owner(session, settings):
    owner = await make_user(session, user_id=1)
    garage, _ = make_services(session, settings)
    _group, chain = await make_group_with_chain(garage, owner.id)
    await garage.retire_chain(chain)

    found = await garage.require_chain(owner.id, chain.id)

    assert found.id == chain.id
    assert found.is_retired is True


async def test_current_group_ignores_a_poisoned_current_group_id(session, settings):
    """If current_group_id ever pointed at another user's group (e.g. a bug in
    a caller that skipped require_group before set_current_group), current_group
    must still refuse to return it — this is the second line of defense.
    """
    owner = await make_user(session, user_id=1)
    attacker = await make_user(session, user_id=2)
    garage, _ = make_services(session, settings)
    group, _chain = await make_group_with_chain(garage, owner.id)

    attacker.current_group_id = group.id
    await session.flush()

    assert await garage.current_group(attacker.id) is None
    assert await garage.current_active_chain(attacker.id) is None
