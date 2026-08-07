import asyncio
import sqlite3

from chain_health.bot import texts as bot_texts
from chain_health.db import engine as engine_module
from chain_health.services.access import AccessService
from tests.bot_harness import make_update_message


async def test_two_concurrent_invite_redemptions_only_one_succeeds(harness, container):
    """Reproduces the review's redeem_invite race: two /start <code> updates
    for the same one-time invite, fed through the real dispatcher
    concurrently (aiogram processes updates as concurrent tasks — each gets
    its own dishka request scope and its own transaction). Before the fix
    (non-atomic read-then-write on invites.used_by), both could observe
    used_by IS NULL before either committed and both would be admitted.
    """
    async with container() as rc:
        access = await rc.get(AccessService)
        await access.ensure_registered(1, "admin")
        invite = await access.create_invite(created_by=1)
        code = invite.code

    update_a = make_update_message(f"/start {code}", user_id=7, update_id=101)
    update_b = make_update_message(f"/start {code}", user_id=8, update_id=102)

    await asyncio.gather(
        harness.dp.feed_update(harness.bot, update_a),
        harness.dp.feed_update(harness.bot, update_b),
    )

    async with container() as rc:
        access = await rc.get(AccessService)
        allowed = [await access.is_allowed(7), await access.is_allowed(8)]
    assert sum(allowed) == 1


async def test_two_concurrent_first_contacts_from_the_same_new_user_both_succeed(
    harness, container
):
    """Reproduces the review's ensure_registered race: two updates from the
    same brand-new user_id (here, the default admin id=1), fed concurrently.
    Before the fix (non-atomic check-then-insert), the second's flush raised
    IntegrityError (UNIQUE constraint on users.id) and that whole update was
    swallowed by the generic error handler instead of getting its real reply.
    """
    update_a = make_update_message("/menu", user_id=1, update_id=201)
    update_b = make_update_message("/status", user_id=1, update_id=202)

    await asyncio.gather(
        harness.dp.feed_update(harness.bot, update_a),
        harness.dp.feed_update(harness.bot, update_b),
    )

    sent_texts = harness.session.sent_texts()
    assert bot_texts.ERR_UNEXPECTED not in sent_texts
    assert bot_texts.MENU_ROOT in sent_texts
    assert any("Добавь группу" in t for t in sent_texts)


async def test_concurrent_updates_do_not_rely_on_busy_timeout(harness, monkeypatch):
    """With the busy handler disabled entirely, concurrent updates still work.

    This pins *what* serializes writers. `busy_timeout` (db/engine.py) makes a
    second writer wait rather than fail, and that wait is long because a handler
    holds its transaction across Telegram round-trips — so the timeout papers
    over a missing lock right up until traffic outgrows it, then starts dropping
    updates with "database is locked". WriteLockMiddleware makes the contention
    not happen at all; setting the timeout to zero proves the difference,
    because with no lock the second BEGIN IMMEDIATE would fail on the spot.

    Patched on the module, not on the engine: the pragma statement reads the
    global when a connection is opened, and the container's engine has not
    opened one yet.
    """
    monkeypatch.setattr(engine_module, "_BUSY_TIMEOUT_MS", 0)

    await asyncio.gather(
        *(
            harness.dp.feed_update(
                harness.bot, make_update_message("/menu", user_id=1, update_id=300 + i)
            )
            for i in range(8)
        )
    )

    sent_texts = harness.session.sent_texts()
    assert bot_texts.ERR_UNEXPECTED not in sent_texts
    assert sent_texts.count(bot_texts.MENU_ROOT) == 8, "every update must get its reply"


def _write_lock_free(db_path) -> bool:
    """Whether the database can be written to right now.

    Raw sqlite3 on its own connection, with no wait and outside SQLAlchemy's
    pool, so the answer is about the file rather than about what the engine has
    cached. Under WAL, BEGIN IMMEDIATE does not block on readers and fails only
    on somebody else's write lock — exactly what needs measuring.
    """
    con = sqlite3.connect(db_path, timeout=0)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


async def test_the_database_is_free_while_talking_to_telegram(harness, db_path):
    """The property the whole send-after-commit ordering exists for.

    While sending sat inside the transaction, SQLite's single write lock stayed
    held for the entire round trip to Telegram, and the bot's ceiling was a
    handful of updates per second regardless of load. Handlers now only record
    intent (bot/ui.py) and delivery happens after the commit.

    Asked literally: on every call to Telegram, ask the database whether it is
    writable. A single busy answer means the network has crept back inside the
    transaction.
    """
    free: list[bool] = []
    original = harness.session.make_request

    async def probing(*args, **kwargs):
        free.append(_write_lock_free(db_path))
        return await original(*args, **kwargs)

    harness.session.make_request = probing

    await harness.dp.feed_update(harness.bot, make_update_message("125", user_id=1, update_id=401))

    assert free, "the update should have talked to Telegram"
    assert all(free), "the update's transaction must be closed before any Telegram call"
