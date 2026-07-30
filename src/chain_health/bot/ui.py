import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
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
from chain_health.domain.values import StatusView
from chain_health.services.status import StatusService
from chain_health.services.users import UserService

logger = logging.getLogger(__name__)

_BENIGN_EDIT_ERRORS = ("message is not modified",)


def is_benign_edit_error(exc: TelegramBadRequest) -> bool:
    message = str(exc).lower()
    return any(pattern in message for pattern in _BENIGN_EDIT_ERRORS)


async def edit_text_or_ignore(
    message: MaybeInaccessibleMessageUnion | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit an inline message, treating "not modified" as success.

    Raises StaleMessageError when the message is gone, too old, or otherwise
    not editable, so the dp.errors handler can turn it into one friendly
    toast instead of a traceback with a hung callback spinner.
    """
    if message is None or isinstance(message, InaccessibleMessage):
        raise StaleMessageError("Message is no longer accessible")

    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if is_benign_edit_error(exc):
            return
        raise StaleMessageError(str(exc)) from exc


async def start_edit_dialog(
    callback: CallbackQuery,
    state: FSMContext,
    next_state: State,
    prompt: str,
    data: dict[str, int],
) -> None:
    """Shared shape of every "edit this value" entry point: stash the
    authorized entity's id in FSM state, prompt for the new value, and answer
    the callback so its spinner stops.
    """
    await state.update_data(data)
    await state.set_state(next_state)
    await edit_text_or_ignore(callback.message, prompt)
    await callback.answer()


async def ask_number_or_retry(message: Message, *, max_value: float) -> float | None:
    """Parses the numeric FSM reply, or asks again and returns None.

    max_value has no default and is required, on purpose: a bare
    parse_positive_float(message.text) call once let a ride-edit/limit-edit
    dialog accept an unbounded number, permanently corrupting cycle_km/total_km
    or silencing over-limit warnings forever. Every caller must pick a sane
    bound rather than being able to forget one.
    """
    value = parse_positive_float(message.text, max_value=max_value)
    if value is None:
        await message.answer(texts.ASK_NUMBER_RETRY)
    return value


class Responder:
    """Single place that keeps the reply-keyboard placeholder and the pinned
    status message in sync with the database, so handlers never do it by hand.
    """

    def __init__(self, bot: Bot, users: UserService, status_service: StatusService) -> None:
        self._bot = bot
        self._users = users
        self._status_service = status_service

    async def reply(
        self, chat_id: int, user_id: int, text: str, *, data_changed: bool = False
    ) -> None:
        view = await self._status_service.build(user_id)
        keyboard = keyboards.main_reply_keyboard(texts.placeholder_text(view))
        await self._bot.send_message(chat_id, text, reply_markup=keyboard)
        if data_changed:
            await self._sync_pinned(chat_id, user_id, view)

    async def reply_with_inline_keyboard(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup,
        *,
        data_changed: bool = False,
    ) -> None:
        # Telegram allows only one keyboard type per message, so the reply-keyboard
        # placeholder is not refreshed here — it catches up on the next plain reply().
        await self._bot.send_message(chat_id, text, reply_markup=reply_markup)
        if data_changed:
            await self.sync_pinned(chat_id, user_id)

    async def sync_pinned(self, chat_id: int, user_id: int) -> None:
        """Refresh the pinned status message only, without sending a new chat message.

        Used by menu actions where the edited inline message already gives the
        user feedback — the placeholder catches up lazily on the next reply().
        """
        view = await self._status_service.build(user_id)
        await self._sync_pinned(chat_id, user_id, view)

    async def _sync_pinned(self, chat_id: int, user_id: int, view: StatusView) -> None:
        """Best-effort per docs/ARCHITECTURE.md D10: any Telegram-API failure
        anywhere in this sync (edit, recreate, or pin) is logged and
        swallowed, never allowed to propagate and roll back a transaction
        that already recorded a real ride.
        """
        try:
            await self._do_sync_pinned(chat_id, user_id, view)
        except TelegramAPIError:
            logger.warning("Could not sync the pinned status message for user %s", user_id)

    async def _do_sync_pinned(self, chat_id: int, user_id: int, view: StatusView) -> None:
        pinned_text = texts.pinned_status_text(view)
        message_id = await self._users.pinned_message_id(user_id)

        if message_id is not None:
            try:
                await self._bot.edit_message_text(
                    pinned_text, chat_id=chat_id, message_id=message_id
                )
                return
            except TelegramBadRequest as exc:
                if is_benign_edit_error(exc):
                    return
                # Deleted or otherwise inaccessible — recreate it below.

        message = await self._bot.send_message(chat_id, pinned_text)
        # Store the id BEFORE pinning: a pin failure must not orphan the message
        # and make every later sync create yet another one.
        await self._users.set_pinned_message_id(user_id, message.message_id)
        await self._bot.pin_chat_message(chat_id, message.message_id, disable_notification=True)
