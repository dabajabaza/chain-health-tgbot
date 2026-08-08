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
from datetime import timedelta
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
from sqlalchemy.ext.asyncio import AsyncSession

from chain_health.bot import keyboards, texts
from chain_health.bot.parsing import parse_positive_float
from chain_health.db.models import OutboxMessage
from chain_health.domain.errors import StaleMessageError
from chain_health.services.status import StatusService
from chain_health.services.users import UserService
from chain_health.timeutils import utcnow

logger = logging.getLogger(__name__)

_BENIGN_EDIT_ERRORS = ("message is not modified",)

# How long an outbox row belongs to the normal send before the background
# sender may touch it. Comfortably longer than a round trip plus a poll tick.
OUTBOX_GRACE = timedelta(minutes=1)


def _dump(method: TelegramMethod[Any]) -> str:
    """An aiogram method as JSON — only the fields the caller set explicitly.

    exclude_unset is required, not a size optimization: an unset field holds
    aiogram's `Default` sentinel, which asks for a bot-level setting at send
    time. It is not serializable, and it should not be — on revival the field
    is unset again and picks up the default again.
    """
    return method.model_dump_json(exclude_unset=True)


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
    # Whether this call should survive the process dying — see db.models.OutboxMessage.
    durable: bool = False
    # Set when the promise is written into the transaction.
    outbox_id: int | None = None


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
        self._delivered: list[int] = []
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
        self._queue.append(
            _Call(SendMessage(chat_id=chat_id, text=text, reply_markup=keyboard), durable=True)
        )
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
            _Call(SendMessage(chat_id=chat_id, text=text, reply_markup=reply_markup), durable=True)
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
            _Call(SendMessage(chat_id=chat_id, text=text, reply_markup=reply_markup), durable=True)
        )

    def edit(
        self,
        message: MaybeInaccessibleMessageUnion | None,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Edit an inline message, treating "not modified" as success.

        NOT durable, unlike a send. An edit is the state of one particular
        screen, not a fact; replayed a minute later it overwrites wherever the
        user has navigated since. The scenario is not hypothetical: a ride
        deletion fails to deliver, the user goes back to the list, and the
        queued edit then puts the stale ride card back on top of it.

        The price is that an undelivered card edit is not retried. The
        operation is applied either way and the next screen shows the truth —
        cheaper than teleporting the user into the past.
        """
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

    async def persist(self, session: AsyncSession) -> None:
        """Write the delivery promises into the same transaction as the change.

        Called by the middleware *before* the commit, so either both the change
        and the promise to report it are recorded, or neither is. Otherwise a
        process dying between the commit and the send would leave an applied
        operation with nobody told about it — which is precisely what reordering
        send and commit must not cost.
        """
        # next_attempt_at is offset: the normal send happens right after the
        # commit and takes a Telegram round trip. A row visible to the sender
        # immediately would be picked up by a poll tick landing inside that
        # window, and the user would get the same reply twice in ordinary
        # operation rather than after a crash. The sender must only ever see
        # what the normal path is no longer going to delete.
        due = utcnow() + OUTBOX_GRACE
        rows = [
            (
                item,
                OutboxMessage(
                    method=type(item.method).__name__,
                    payload=_dump(item.method),
                    next_attempt_at=due,
                ),
            )
            for item in self._queue
            if isinstance(item, _Call) and item.durable
        ]
        if not rows:
            return
        session.add_all([row for _item, row in rows])
        # flush, not commit: the ids are needed right here so a successful send
        # has something to delete. The unit of work still owns the commit.
        await session.flush()
        for item, row in rows:
            item.outbox_id = row.id

    def delivered(self) -> list[int]:
        """Outbox row ids whose messages went out. The middleware deletes them."""
        return self._delivered

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
        stale: StaleMessageError | None = None
        for item in queued:
            if isinstance(item, _PinnedSync):
                await self._sync_pinned(item)
                continue
            try:
                await self._call(item)
            except StaleMessageError as exc:
                # Telegram says the target message is gone or unusable. Not
                # transient — retrying cannot fix it — so the promise is dropped
                # rather than queued. Re-raised below so dp.errors still runs:
                # a handler that opened a dialog and then failed to show its
                # prompt leaves an FSM state that would swallow the user's next
                # message, and clearing it is on_error's job.
                stale = stale or exc
                if item.outbox_id is not None:
                    self._delivered.append(item.outbox_id)
            except Exception:
                # Everything else is treated as transient: the update is applied
                # and committed by now, and no network mishap may undo it or
                # turn into an error toast for an operation that succeeded.
                # Durable calls stay in the outbox and get retried.
                logger.warning(
                    "Could not send %s%s",
                    type(item.method).__name__,
                    " — left queued" if item.outbox_id else "",
                    exc_info=True,
                )
            else:
                if item.outbox_id is not None:
                    self._delivered.append(item.outbox_id)

        # Raised only once the rest of the queue has gone out — the callback
        # answer that stops the spinner is usually queued behind the edit.
        if stale is not None:
            raise stale

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
