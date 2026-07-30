import contextlib
from datetime import timedelta

from chain_health.domain.values import ChainStatus
from chain_health.timeutils import local_today
from tests.factories import (
    make_group_with_chain,
    make_reminder_service,
    make_services,
    make_status_service,
    make_user,
)


async def test_over_limit_chain_is_due(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    reminders, _notifier = make_reminder_service(session, status_service)

    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=50)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=60, ride_dt=today)

    due = await reminders.due_reminders(today)

    assert [d.user_id for d in due] == [user.id]


async def test_under_limit_chain_is_not_due(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    reminders, _notifier = make_reminder_service(session, status_service)

    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=300)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=60, ride_dt=today)

    due = await reminders.due_reminders(today)

    assert due == []


async def test_resource_warning_is_due_even_under_cycle_limit(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    reminders, _notifier = make_reminder_service(session, status_service)

    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=300)
    await garage.set_chain_resource(chain, 50)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=60, ride_dt=today)

    due = await reminders.due_reminders(today)

    assert [d.user_id for d in due] == [user.id]


async def test_already_reminded_today_is_not_due_again(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    reminders, _notifier = make_reminder_service(session, status_service)

    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=50)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=60, ride_dt=today)

    await reminders.mark_reminded(user.id, today)

    assert await reminders.due_reminders(today) == []


async def test_reminded_yesterday_is_due_again_today(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    reminders, _notifier = make_reminder_service(session, status_service)

    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=50)
    today = local_today(settings.timezone)
    yesterday = today - timedelta(days=1)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=60, ride_dt=today)

    await reminders.mark_reminded(user.id, yesterday)

    assert [d.user_id for d in await reminders.due_reminders(today)] == [user.id]


async def test_user_without_current_group_is_never_due(session, settings):
    await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    reminders, _notifier = make_reminder_service(session, status_service)

    assert await reminders.due_reminders(local_today(settings.timezone)) == []


async def test_due_reminders_can_be_narrowed_to_one_user(session, settings):
    user1 = await make_user(session, user_id=1)
    user2 = await make_user(session, user_id=2)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    reminders, _notifier = make_reminder_service(session, status_service)
    today = local_today(settings.timezone)

    for user in (user1, user2):
        _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=10)
        await rides.add_ride(user_id=user.id, chain=chain, distance_km=20, ride_dt=today)

    due_all = await reminders.due_reminders(today)
    due_one = await reminders.due_reminders(today, user_id=user2.id)

    assert {d.user_id for d in due_all} == {1, 2}
    assert [d.user_id for d in due_one] == [2]


async def test_due_user_ids_matches_due_reminders(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    reminders, _notifier = make_reminder_service(session, status_service)

    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=10)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=20, ride_dt=today)

    assert await reminders.due_user_ids(today) == [user.id]


async def test_send_due_reminder_notifies_and_marks(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    reminders, notifier = make_reminder_service(session, status_service)

    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=10)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=20, ride_dt=today)

    sent = await reminders.send_due_reminder(user.id, today)

    assert sent is True
    assert len(notifier.sent) == 1
    notified_user_id, status = notifier.sent[0]
    assert notified_user_id == user.id
    assert isinstance(status, ChainStatus)
    assert status.chain.id == chain.id
    assert (await reminders.due_reminders(today)) == []  # marked, no longer due


async def test_send_due_reminder_returns_false_when_no_longer_due(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    reminders, notifier = make_reminder_service(session, status_service)
    today = local_today(settings.timezone)

    await make_group_with_chain(garage, user.id, default_cycle_limit_km=300)
    # under limit -> never due

    sent = await reminders.send_due_reminder(user.id, today)

    assert sent is False
    assert notifier.sent == []


async def test_send_due_reminder_does_not_mark_when_notifier_raises(session, settings):
    user = await make_user(session)
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    reminders, notifier = make_reminder_service(session, status_service)
    notifier.raises = RuntimeError("blocked the bot")

    _, chain = await make_group_with_chain(garage, user.id, default_cycle_limit_km=10)
    today = local_today(settings.timezone)
    await rides.add_ride(user_id=user.id, chain=chain, distance_km=20, ride_dt=today)

    with contextlib.suppress(RuntimeError):
        await reminders.send_due_reminder(user.id, today)

    # mark_reminded must not have run — still due on a retry.
    assert [d.user_id for d in await reminders.due_reminders(today)] == [user.id]
