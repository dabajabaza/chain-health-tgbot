from aiogram.types import CallbackQuery, Message

# aiogram types `Message.from_user` as Optional because the Bot API allows a
# message with no sender in principle (channel posts, etc.) — but every
# update reaching our handlers has already passed PrivateChatOnlyMiddleware
# and AuthMiddleware, both of which require a real private-chat user. This
# assertion documents and enforces that invariant for the type checker,
# rather than threading `Message.from_user | None` through every handler.


def message_user_id(message: Message) -> int:
    assert message.from_user is not None
    return message.from_user.id


def callback_chat_id(callback: CallbackQuery) -> int:
    """The chat a callback's origin message lives in.

    ``callback.message`` is ``Message | InaccessibleMessage | None`` in
    aiogram's typing, but ``.chat`` is present on both non-None variants, so
    only the None case (no origin message at all) needs ruling out.
    """
    assert callback.message is not None
    return callback.message.chat.id
