"""Redelivered updates must not be applied twice.

Telegram confirms an update only on the *next* getUpdates call, so a process
killed in between receives the same update_id again — and a deploy kills the bot
on purpose. A duplicate is worse than a loss here: a dropped message the user
resends, a double ride silently corrupts mileage.

These tests drive the real dispatcher, so they cover the wiring order too: the
idempotency middleware has to sit after dishka (it needs the request-scoped
session) and before auth.
"""

from sqlalchemy import select

from chain_health.db.models import ProcessedUpdate
from chain_health.services.rides import RideService
from tests.bot_harness import make_update_message
from tests.factories import onboard


async def _rides(container, user_id: int = 1):
    async with container() as rc:
        return await (await rc.get(RideService)).recent_rides(user_id)


async def _marks(session_factory) -> list[int]:
    async with session_factory() as s:
        return sorted(await s.scalars(select(ProcessedUpdate.id)))


async def test_redelivered_update_is_applied_only_once(harness, container, session_factory):
    await onboard(harness)
    update = make_update_message("42", user_id=1, update_id=777)

    await harness.dp.feed_update(harness.bot, update)
    # Exactly what Telegram sends after the process dies before confirming.
    await harness.dp.feed_update(harness.bot, update)

    rides = await _rides(container)
    assert [r.distance_km for r in rides] == [42], "the ride must not be logged twice"
    assert 777 in await _marks(session_factory)


async def test_distinct_updates_are_both_applied(harness, container, session_factory):
    await onboard(harness)

    await harness.dp.feed_update(harness.bot, make_update_message("10", user_id=1, update_id=801))
    await harness.dp.feed_update(harness.bot, make_update_message("20", user_id=1, update_id=802))

    rides = await _rides(container)
    assert sorted(r.distance_km for r in rides) == [10, 20]
    marks = await _marks(session_factory)
    assert 801 in marks and 802 in marks


async def test_repeated_update_produces_no_second_reply(harness, container):
    """The duplicate is dropped before the handler, so the user is not answered
    twice either — a second confirmation would read as a second ride."""
    await onboard(harness)
    update = make_update_message("15", user_id=1, update_id=903)

    await harness.dp.feed_update(harness.bot, update)
    replies_after_first = len(harness.session.sent_texts())

    await harness.dp.feed_update(harness.bot, update)

    assert len(harness.session.sent_texts()) == replies_after_first


async def test_even_a_denied_update_gets_marked(harness, session_factory):
    """Every update that finishes without raising is marked, including one that
    auth drops.

    This is a consequence of the unit of work, not an oversight: one request
    scope is one transaction, and RequestProvider commits it whenever no
    exception occurred — returning ``None`` from a middleware is not an
    exception. The mark is therefore written for read-only and denied updates
    too. Harmless (their redelivery had nothing to repeat) and bounded by the
    weekly prune, but worth pinning down so the behaviour is not mistaken for
    a leak later.
    """
    await harness.dp.feed_update(
        harness.bot, make_update_message("hello", user_id=999, update_id=555)
    )

    assert 555 in await _marks(session_factory)
