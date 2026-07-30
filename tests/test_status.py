from chain_health.timeutils import local_today
from tests.factories import make_group_with_chain, make_services, make_status_service, make_user


async def test_status_view_flags_resource_warning(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)

    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=100)
    await garage.set_chain_resource(chain, 50)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=60, ride_dt=today)

    view = await status_service.build(user.id)

    assert view.active is not None
    assert view.active.chain.id == chain.id
    assert view.active.cycle_km == 60
    assert view.active.over_limit is False
    assert view.active.resource_warning is True


async def test_status_view_flags_over_limit(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)

    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=50)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=60, ride_dt=today)

    view = await status_service.build(user.id)

    assert view.active is not None
    assert view.active.over_limit is True


async def test_status_view_empty_when_no_current_group(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)

    view = await status_service.build(user.id)

    assert view.group is None
    assert view.active is None
    assert view.all_chains == []


async def test_chain_status_is_public_and_matches_build_for_group(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)

    group, chain = await make_group_with_chain(garage, user.id)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=12, ride_dt=today)

    direct = await status_service.chain_status(chain)
    [via_group] = await status_service.build_for_group(group)

    assert direct.cycle_km == via_group.cycle_km == 12
    assert direct.chain.id == via_group.chain.id == chain.id
