from datetime import timedelta

from chain_health.services.access import AccessService
from chain_health.timeutils import utcnow


async def test_admin_is_always_allowed(session, settings):
    access = AccessService(session, settings)
    assert access.is_admin(1)
    assert await access.is_allowed(1) is True


async def test_unknown_user_is_not_allowed(session, settings):
    access = AccessService(session, settings)
    assert await access.is_allowed(42) is False


async def test_allow_user_registers_them(session, settings):
    access = AccessService(session, settings)
    await access.allow_user(42, "friend")
    assert await access.is_allowed(42) is True


async def test_invite_can_be_redeemed_once(session, settings):
    access = AccessService(session, settings)
    invite = await access.create_invite(created_by=1)

    assert await access.redeem_invite(invite.code, 42, "friend") is True
    assert await access.is_allowed(42) is True

    assert await access.redeem_invite(invite.code, 43, "other") is False
    assert await access.is_allowed(43) is False


async def test_expired_invite_is_rejected(session, settings):
    access = AccessService(session, settings)
    invite = await access.create_invite(created_by=1)
    invite.expires_at = utcnow() - timedelta(hours=1)
    await session.flush()

    assert await access.redeem_invite(invite.code, 42, "friend") is False
    assert await access.is_allowed(42) is False


async def test_unknown_invite_code_is_rejected(session, settings):
    access = AccessService(session, settings)
    assert await access.redeem_invite("does-not-exist", 42, "friend") is False
