from tests.helpers.factories import make_user, make_user_service


async def test_pinned_message_id_is_none_for_a_fresh_user(session):
    user = await make_user(session)
    users = make_user_service(session)

    assert await users.pinned_message_id(user.id) is None


async def test_set_then_read_pinned_message_id(session):
    user = await make_user(session)
    users = make_user_service(session)

    ok = await users.set_pinned_message_id(user.id, 4242)

    assert ok is True
    assert await users.pinned_message_id(user.id) == 4242


async def test_set_pinned_message_id_returns_false_for_an_unknown_user(session):
    users = make_user_service(session)

    assert await users.set_pinned_message_id(999, 1) is False
