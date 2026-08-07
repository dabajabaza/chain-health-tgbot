"""The seam between handlers and the Telegram API.

Handlers never call the Bot API directly. They *record* what should be sent,
and the delivery happens later — after the request's transaction has committed
and the write lock has been released (see middlewares.WriteLockMiddleware and
the registration order in __main__.build_dispatcher).

That ordering is the whole point. While sending sat inside the transaction,
SQLite's single write lock stayed held for the entire round trip to Telegram —
a few hundred milliseconds per update — and the bot's ceiling was a handful of
updates per second no matter how many arrived. Recording is pure bookkeeping,
so the lock is now held only for the database work itself.

Recording methods that need to read the database (the status view, the pinned
message id) are coroutines; the rest are plain functions. Anything a recording
method reads is read *inside* the transaction, which is what keeps the
recorded intent consistent with the data that was committed.
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardMarkup,
    MaybeInaccessibleMessageUnion,
    Message,
)

from chain_health.bot import keyboards, texts
from chain_health.bot.parsing import parse_positive_float
from chain_health.domain.errors import StaleMessageError
from chain_health.services.status import StatusService
from chain_health.services.users import UserService

logger = logging.getLogger(__name__)

_BENIGN_EDIT_ERRORS = ("message is not modified",)


def is_benign_edit_error(exc: TelegramBadRequest) -> bool:
    message = str(exc).lower()
    return any(pattern in message for pattern in _BENIGN_EDIT_ERRORS)


def require_editable(message: MaybeInaccessibleMessageUnion | None) -> Message:
    """The message behind an inline keyboard, if it can still be edited.

    Checked when the intent is recorded, not when it is sent: a callback whose
    origin message is gone or older than 48h is a bad request we can reject
    before touching any data, and the dp.errors handler turns StaleMessageError
    into one friendly toast instead of a traceback with a hung spinner.
    """
    if message is None or isinstance(message, InaccessibleMessage):
        raise StaleMessageError("Message is no longer accessible")
    return message


async def start_edit_dialog(
    callback: CallbackQuery,
    state: FSMContext,
    next_state: State,
    prompt: str,
    data: dict[str, int],
    responder: "Responder",
) -> None:
    """Shared shape of every "edit this value" entry point: stash the
    authorized entity's id in FSM state, prompt for the new value, and answer
    the callback so its spinner stops.
    """
    await state.update_data(data)
    await state.set_state(next_state)
    responder.edit(callback.message, prompt)
    responder.answer_callback(callback)


def ask_number_or_retry(
    message: Message, responder: "Responder", *, max_value: float
) -> float | None:
    """Parses the numeric FSM reply, or asks again and returns None.

    max_value has no default and is required, on purpose: a bare
    parse_positive_float(message.text) call once let a ride-edit/limit-edit
    dialog accept an unbounded number, permanently corrupting cycle_km/total_km
    or silencing over-limit warnings forever. Every caller must pick a sane
    bound rather than being able to forget one.
    """
    value = parse_positive_float(message.text, max_value=max_value)
    if value is None:
        responder.reply_raw(message.chat.id, texts.ASK_NUMBER_RETRY)
    return value


# raise    — a failure is a real failure and reaches dp.errors;
# not_modified — an edit reporting "not modified" succeeded: the user pressed
#            the same button twice and the screen already shows what it should;
# quiet    — cosmetic (dropping a spent keyboard); failing changes nothing.
OnError = Literal["raise", "not_modified", "quiet"]


@dataclass(slots=True)
class _Call:
    """One Bot API call, plus how much its failure matters."""

    method: TelegramMethod[Any]
    on_error: OnError = "raise"


@dataclass(slots=True)
class _PinnedSync:
    """Bring the user's pinned status message up to date.

    Compound on purpose: whether this edits or recreates depends on what
    Telegram answers, so it cannot be flattened into a single method at
    recording time. Everything it needs from the database (the text, the known
    message id) is read while recording, inside the transaction.
    """

    chat_id: int
    user_id: int
    text: str
    known_message_id: int | None


class Responder:
    """Recorded intent for one request scope, delivered after the commit.

    Also the single place that keeps the reply-keyboard placeholder and the
    pinned status message in sync with the database, so handlers never do it
    by hand.
    """

    def __init__(self, bot: Bot, users: UserService, status_service: StatusService) -> None:
        self._bot = bot
        self._users = users
        self._status_service = status_service
        self._queue: list[_Call | _PinnedSync] = []
        # Filled by flush, drained by the middleware in a second short
        # transaction: a recreated pinned message only has an id once sent.
        self.pinned_updates: list[tuple[int, int]] = []

    # ---------- recording ----------

    async def reply(
        self, chat_id: int, user_id: int, text: str, *, data_changed: bool = False
    ) -> None:
        """Send a message with a fresh reply-keyboard placeholder; sync the pin if data changed."""
        view = await self._status_service.build(user_id)
        keyboard = keyboards.main_reply_keyboard(texts.placeholder_text(view))
        self._queue.append(_Call(SendMessage(chat_id=chat_id, text=text, reply_markup=keyboard)))
        if data_changed:
            await self.sync_pinned(chat_id, user_id)

    async def reply_with_inline_keyboard(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup,
        *,
        data_changed: bool = False,
    ) -> None:
        """Send a message with inline buttons; sync the pinned status if data changed."""
        # Telegram allows only one keyboard type per message, so the reply-keyboard
        # placeholder is not refreshed here — it catches up on the next plain reply().
        self._queue.append(
            _Call(SendMessage(chat_id=chat_id, text=text, reply_markup=reply_markup))
        )
        if data_changed:
            await self.sync_pinned(chat_id, user_id)

    async def sync_pinned(self, chat_id: int, user_id: int) -> None:
        """Refresh the pinned status message only, without sending a new chat message.

        Used by menu actions where the edited inline message already gives the
        user feedback — the placeholder catches up lazily on the next reply().
        """
        view = await self._status_service.build(user_id)
        self._queue.append(
            _PinnedSync(
                chat_id=chat_id,
                user_id=user_id,
                text=texts.pinned_status_text(view),
                known_message_id=await self._users.pinned_message_id(user_id),
            )
        )

    def reply_raw(
        self, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> None:
        """A plain message, with no status view attached.

        For prompts, retries and menus, where rebuilding the whole status view
        would be pure cost — nothing about it has changed.
        """
        self._queue.append(
            _Call(SendMessage(chat_id=chat_id, text=text, reply_markup=reply_markup))
        )

    def edit(
        self,
        message: MaybeInaccessibleMessageUnion | None,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Edit an inline message, treating "not modified" as success."""
        editable = require_editable(message)
        self._queue.append(
            _Call(
                EditMessageText(
                    chat_id=editable.chat.id,
                    message_id=editable.message_id,
                    text=text,
                    reply_markup=reply_markup,
                ),
                on_error="not_modified",
            )
        )

    def clear_keyboard(self, message: MaybeInaccessibleMessageUnion | None) -> None:
        """Take the buttons off a message that has served its purpose.

        Purely cosmetic, so an inaccessible or already-cleared message is not
        worth a word: nothing about the user's data depends on it.
        """
        if message is None or isinstance(message, InaccessibleMessage):
            return
        self._queue.append(
            _Call(
                EditMessageReplyMarkup(
                    chat_id=message.chat.id, message_id=message.message_id, reply_markup=None
                ),
                on_error="quiet",
            )
        )

    def answer_callback(self, callback: CallbackQuery, text: str | None = None) -> None:
        """Stop the button's spinner, optionally with a toast."""
        self._queue.append(_Call(AnswerCallbackQuery(callback_query_id=callback.id, text=text)))

    # ---------- delivery ----------

    def pending(self) -> int:
        """How many intents are waiting — for tests and diagnostics."""
        return len(self._queue)

    def discard(self) -> None:
        """Drop what was recorded without sending it.

        Called when the transaction rolls back: the update did not happen, so
        there is nothing to tell the user about.
        """
        self._queue.clear()

    async def flush(self) -> None:
        """Deliver the recorded intents, in order.

        The queue is emptied *before* sending, so a second flush (from an error
        path, say) cannot deliver the same message twice.
        """
        queued, self._queue = self._queue, []
        for item in queued:
            if isinstance(item, _PinnedSync):
                await self._sync_pinned(item)
            else:
                await self._call(item)

    async def _call(self, item: _Call) -> None:
        try:
            await self._bot(item.method)
        except TelegramBadRequest as exc:
            if item.on_error == "not_modified" and is_benign_edit_error(exc):
                return
            if item.on_error == "quiet":
                return
            raise StaleMessageError(str(exc)) from exc
        except TelegramAPIError:
            if item.on_error == "quiet":
                return
            raise

    async def _sync_pinned(self, item: _PinnedSync) -> None:
        """Best-effort per docs/ARCHITECTURE.md D10: any Telegram-API failure
        anywhere in this sync (edit, recreate, or pin) is logged and swallowed.
        It used to matter because a raise would have rolled back a transaction
        that already recorded a real ride; now the transaction is long gone,
        and it still matters — a pin we could not refresh is no reason to show
        the user an error for a ride that was saved.
        """
        try:
            await self._do_sync_pinned(item)
        except TelegramAPIError:
            logger.warning("Could not sync the pinned status message for user %s", item.user_id)

    async def _do_sync_pinned(self, item: _PinnedSync) -> None:
        if item.known_message_id is not None:
            try:
                await self._bot.edit_message_text(
                    item.text, chat_id=item.chat_id, message_id=item.known_message_id
                )
                return
            except TelegramBadRequest as exc:
                if is_benign_edit_error(exc):
                    return
                # Deleted or otherwise inaccessible — recreate it below.

        message = await self._bot.send_message(item.chat_id, item.text)
        # Record the id BEFORE pinning: a pin failure must not orphan the
        # message and make every later sync create yet another one. The write
        # itself lands in a follow-up transaction (the middleware drains this),
        # because the id does not exist until the send has happened.
        self.pinned_updates.append((item.user_id, message.message_id))
        await self._bot.pin_chat_message(
            item.chat_id, message.message_id, disable_notification=True
        )
