from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from chain_health.db.models import User
from chain_health.services.access import AccessService
from chain_health.timeutils import utcnow


async def test_stranger_gets_no_reply(harness):
    await harness.send("hello", user_id=999)
    assert harness.session.calls == []


async def test_group_chat_message_from_an_allowed_user_is_ignored(harness):
    """D8: private-chats-only is enforced before the auth check even runs."""
    await harness.send("/status", user_id=1, chat_type="group")
    assert harness.session.calls == []


async def test_group_chat_callback_gets_no_response_either(harness):
    await harness.click("menu:root", user_id=1, chat_type="group")
    assert harness.session.calls == []


async def test_invite_deep_link_grants_access(harness, container):
    async with container() as rc:
        access = await rc.get(AccessService)
        invite = await access.create_invite(created_by=1)

    await harness.send(f"/start {invite.code}", user_id=2)

    assert harness.session.sent_texts() != []

    harness.session.clear()
    await harness.send("/status", user_id=2)
    assert harness.session.sent_texts() != []


async def test_invite_can_only_be_used_once(harness, container):
    async with container() as rc:
        access = await rc.get(AccessService)
        invite = await access.create_invite(created_by=1)

    await harness.send(f"/start {invite.code}", user_id=2)
    harness.session.clear()

    await harness.send(f"/start {invite.code}", user_id=3)

    assert harness.session.calls == []


async def test_expired_invite_stays_silent(harness, container):
    async with container() as rc:
        access = await rc.get(AccessService)
        invite = await access.create_invite(created_by=1)
        session = await rc.get(AsyncSession)
        db_invite = await session.get(type(invite), invite.code)
        db_invite.expires_at = utcnow() - timedelta(hours=1)

    await harness.send(f"/start {invite.code}", user_id=4)

    assert harness.session.calls == []


async def test_admin_without_a_users_row_is_registered_on_first_contact(harness, container):
    await harness.send("/status", user_id=1)

    async with container() as rc:
        session = await rc.get(AsyncSession)
        assert await session.get(User, 1) is not None


async def test_allow_rejects_superscript_digits_with_usage_text_not_a_crash(harness):
    """str.isdigit() is True for characters like the superscript "³" that
    int() then rejects with ValueError — must use isdecimal() instead.
    """
    await harness.send("/allow 12³", user_id=1)

    sent_texts = harness.session.sent_texts()
    assert sent_texts != []
    assert "Использование" in sent_texts[-1]


async def test_unknown_callback_gets_a_toast_not_silence(harness):
    await harness.click("totally:garbage:payload", user_id=1)
    answers = harness.session.calls_of("AnswerCallbackQuery")
    assert len(answers) == 1
