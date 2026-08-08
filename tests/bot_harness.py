import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import EditMessageText, SendMessage, TelegramMethod
from aiogram.methods.get_me import GetMe
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser


class RecordingSession(BaseSession):
    """aiogram session double: records every outgoing API method call and
    returns fabricated results shaped to satisfy aiogram's own response
    validation, without touching the network. Can be programmed to raise on
    a specific method name via ``fail_on``.

    Inherits BaseSession (rather than duck-typing) so ``Bot.session`` accepts
    it without a type-checker complaint, and so ``Bot.__call__`` (which
    invokes ``session(bot, method)`` -> ``BaseSession.__call__`` ->
    ``self.make_request``) works unmodified.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.fail_on: dict[str, Exception] = {}
        self._next_message_id = 5000

    def _next_message(self, chat_id: int, text: str | None) -> Message:
        self._next_message_id += 1
        return Message(
            message_id=self._next_message_id,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="private"),
            text=text,
        )

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        self.calls.append(method)
        name = type(method).__name__
        if name in self.fail_on:
            raise self.fail_on[name]

        if isinstance(method, SendMessage | EditMessageText):
            # Test fake doesn't support username-based chat ids or
            # inline_message_id-based edits — the app never uses either.
            assert isinstance(method.chat_id, int)
            return self._next_message(method.chat_id, method.text)
        if isinstance(method, GetMe):
            return TgUser(id=1, is_bot=True, first_name="Bot", username="testbot")
        return True

    async def close(self) -> None:
        pass

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b""

    def calls_of(self, name: str) -> list[TelegramMethod[Any]]:
        return [m for m in self.calls if type(m).__name__ == name]

    def sent_texts(self) -> list[str]:
        return [text for m in self.calls if (text := getattr(m, "text", None)) is not None]

    def clear(self) -> None:
        self.calls.clear()


def make_update_message(
    text: str,
    *,
    user_id: int,
    chat_id: int | None = None,
    chat_type: str = "private",
    update_id: int = 1,
) -> Update:
    chat = Chat(id=chat_id if chat_id is not None else user_id, type=chat_type)
    user = TgUser(id=user_id, is_bot=False, first_name="Test")
    message = Message(
        message_id=update_id, date=datetime.now(UTC), chat=chat, from_user=user, text=text
    )
    return Update(update_id=update_id, message=message)


def make_update_callback(
    data: str,
    *,
    user_id: int,
    chat_id: int | None = None,
    chat_type: str = "private",
    message_id: int = 9000,
    update_id: int = 1,
) -> Update:
    origin = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id if chat_id is not None else user_id, type=chat_type),
        text="origin",
    )
    user = TgUser(id=user_id, is_bot=False, first_name="Test")
    callback = CallbackQuery(
        id=str(update_id), from_user=user, chat_instance="x", data=data, message=origin
    )
    return Update(update_id=update_id, callback_query=callback)


@dataclass
class BotHarness:
    """Drives a real Dispatcher (the same one __main__.build_dispatcher wires
    for production) through fabricated Telegram updates, recording what the
    bot would have sent back.
    """

    bot: Bot
    dp: Dispatcher
    session: RecordingSession
    # The write lock the dispatcher was built with: outbox tests must use the
    # same one — a fresh Lock() would be a second writer.
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _next_update_id: int = field(default=1)

    def _update_id(self) -> int:
        self._next_update_id += 1
        return self._next_update_id

    async def send(self, text: str, *, user_id: int = 1, chat_type: str = "private") -> None:
        update = make_update_message(
            text, user_id=user_id, chat_type=chat_type, update_id=self._update_id()
        )
        await self.dp.feed_update(self.bot, update)

    async def click(
        self,
        data: str,
        *,
        user_id: int = 1,
        chat_type: str = "private",
        message_id: int = 9000,
    ) -> None:
        update = make_update_callback(
            data,
            user_id=user_id,
            chat_type=chat_type,
            message_id=message_id,
            update_id=self._update_id(),
        )
        await self.dp.feed_update(self.bot, update)
