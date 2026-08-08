"""A delivery promise outlives a failed send.

Sending after the commit lifted the throughput ceiling but broke the link
between "the change was applied" and "the user knows it". That matters here:
not seeing a confirmation, the user re-enters the ride, and it gets logged
twice — the very duplicate ProcessedUpdate exists to prevent.

The queue closes the gap. The promise is written in the same transaction as the
change; a successful send deletes it; a failed one leaves it for the background
sender. At-least-once: a duplicated reply is harmless, a lost one is not.
"""

import asyncio
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select

from chain_health import outbox
from chain_health.bot import texts as bot_texts
from chain_health.bot.callbacks import MenuAction, MenuCB
from chain_health.db.models import OutboxMessage, Ride
from chain_health.services.mileage import MileageService
from chain_health.timeutils import utcnow
from tests.bot_harness import make_update_message
from tests.factories import onboard


async def _queued(session_factory) -> list[OutboxMessage]:
    async with session_factory() as s:
        return list(await s.scalars(select(OutboxMessage).order_by(OutboxMessage.id)))


async def _make_due(session_factory) -> None:
    """Bring the retry time forward: pretend the normal send window has passed.

    A row is held back for OUTBOX_GRACE (see ui.persist) so the background
    sender cannot duplicate a reply the normal path is delivering right now.
    Tests about the sender itself have to step over that, not wait it out.
    """
    async with session_factory() as s:
        for row in await s.scalars(select(OutboxMessage)):
            row.next_attempt_at = utcnow() - timedelta(seconds=1)
        await s.commit()


async def _rides(session_factory) -> list[float]:
    async with session_factory() as s:
        return [r.distance_km for r in await s.scalars(select(Ride).order_by(Ride.id))]


async def test_a_delivered_reply_leaves_no_trace(harness, session_factory):
    """The normal path: the reply went out, the queue is empty again. Otherwise
    the table would grow on every update and the sender would send duplicates."""
    await onboard(harness)
    await harness.send("42", user_id=1)

    assert await _rides(session_factory) == [42.0]
    assert await _queued(session_factory) == []


async def test_a_failed_send_leaves_a_promise_that_gets_retried(harness, session_factory):
    """The main scenario. The ride is recorded, the reply did not go out — the
    promise stays queued, and the next pass of the sender fulfils it."""
    await onboard(harness)
    harness.session.fail_on["SendMessage"] = RuntimeError("network down")
    await harness.send("42", user_id=1)
    del harness.session.fail_on["SendMessage"]

    assert await _rides(session_factory) == [42.0], "the ride must have been recorded"
    queued = await _queued(session_factory)
    assert [row.method for row in queued] == ["SendMessage"]

    harness.session.clear()
    await _make_due(session_factory)
    sent = await outbox.deliver_batch(harness.bot, session_factory, asyncio.Lock())

    assert sent == 1
    assert harness.session.calls_of("SendMessage")
    assert await _queued(session_factory) == [], "a delivered promise must disappear"


async def test_a_retried_reply_does_not_repeat_the_ride(harness, session_factory):
    """What gets retried is the REPLY, not the action. Otherwise the queue would
    become a second, silent way to log a ride."""
    await onboard(harness)
    harness.session.fail_on["SendMessage"] = RuntimeError("network down")
    await harness.send("42", user_id=1)
    del harness.session.fail_on["SendMessage"]

    await _make_due(session_factory)
    await outbox.deliver_batch(harness.bot, session_factory, asyncio.Lock())
    assert await _rides(session_factory) == [42.0], "the ride must not be logged twice"


async def test_a_failed_retry_backs_off(harness, session_factory):
    """The network is down on the retry too: the row survives but its next
    attempt is pushed out, or the sender would hammer it every few seconds."""
    await onboard(harness)
    harness.session.fail_on["SendMessage"] = RuntimeError("network down")
    await harness.send("42", user_id=1)

    await _make_due(session_factory)
    sent = await outbox.deliver_batch(harness.bot, session_factory, asyncio.Lock())

    assert sent == 0
    queued = await _queued(session_factory)
    assert len(queued) == 1
    assert queued[0].attempts == 1
    assert queued[0].next_attempt_at > queued[0].created_at, "the retry must be deferred"


async def test_a_rolled_back_update_leaves_no_promise(harness, session_factory):
    """The promise lives in the same transaction as the change: no change, no
    promise to report one. Otherwise a failed update would still owe the user a
    reply about something that never happened."""
    await onboard(harness)
    harness.session.clear()

    with patch.object(MileageService, "record_ride", side_effect=RuntimeError("disk gone")):
        await harness.dp.feed_update(
            harness.bot, make_update_message("42", user_id=1, update_id=501)
        )

    assert await _rides(session_factory) == []
    assert await _queued(session_factory) == []


async def test_an_expired_promise_is_dropped(harness, session_factory):
    """A day-old reply is of no use, and the table must not grow forever."""
    await onboard(harness)
    harness.session.fail_on["SendMessage"] = RuntimeError("network down")
    await harness.send("42", user_id=1)
    del harness.session.fail_on["SendMessage"]

    async with session_factory() as s:
        row = (await s.scalars(select(OutboxMessage))).one()
        row.created_at = row.created_at - outbox.TTL * 2
        await s.commit()

    await outbox.purge_expired(session_factory, asyncio.Lock())
    assert await _queued(session_factory) == []


async def test_an_unreadable_promise_does_not_block_the_queue(harness, session_factory):
    """A row nothing can send (its payload drifted from the code) is dropped
    rather than jamming the queue forever."""
    await onboard(harness)
    harness.session.fail_on["SendMessage"] = RuntimeError("network down")
    await harness.send("42", user_id=1)
    del harness.session.fail_on["SendMessage"]

    async with session_factory() as s:
        row = (await s.scalars(select(OutboxMessage))).one()
        row.payload = "{not json"
        await s.commit()

    await _make_due(session_factory)
    await outbox.deliver_batch(harness.bot, session_factory, asyncio.Lock())
    assert await _queued(session_factory) == []


async def test_a_fresh_row_is_invisible_to_the_sender(harness, session_factory):
    """The sender must not touch what the normal path is delivering right now:
    otherwise an ordinary reply arrives twice, which is exactly the ambiguous
    signal the whole design avoids."""
    await onboard(harness)
    harness.session.fail_on["SendMessage"] = RuntimeError("network down")
    await harness.send("42", user_id=1)
    del harness.session.fail_on["SendMessage"]

    assert len(await _queued(session_factory)) == 1, "the promise must be recorded"
    assert await outbox.deliver_batch(harness.bot, session_factory, asyncio.Lock()) == 0
    assert len(await _queued(session_factory)) == 1, "the row must wait its turn"


async def test_a_screen_edit_is_not_queued(harness, session_factory):
    """The queue carries messages, not screens.

    A deferred edit would return the user to a screen they already left: the
    ride list edit fails, they navigate elsewhere, and a minute later the stale
    card lands on top of whatever they are looking at.
    """
    await onboard(harness)
    await harness.send("42", user_id=1)
    harness.session.clear()

    harness.session.fail_on["EditMessageText"] = RuntimeError("network down")
    await harness.click(MenuCB(action=MenuAction.RIDES).pack(), user_id=1)
    del harness.session.fail_on["EditMessageText"]

    assert await _queued(session_factory) == [], "an edit must not be retried later"


async def test_failed_bookkeeping_does_not_turn_success_into_an_error(
    harness, session_factory, monkeypatch
):
    """_settle is post-send housekeeping. Its failure must not tell the user
    "try again" about a ride that was recorded — they would enter it again."""
    from chain_health.bot import middlewares as mw

    async def boom(self, responder):  # noqa: ANN001, ANN202, ARG001
        raise RuntimeError("second transaction refused to open")

    monkeypatch.setattr(mw.WriteLockMiddleware, "_settle", boom)

    await onboard(harness)
    harness.session.clear()
    await harness.send("42", user_id=1)

    assert await _rides(session_factory) == [42.0], "the ride must be recorded"
    assert bot_texts.ERR_UNEXPECTED not in harness.session.sent_texts()
