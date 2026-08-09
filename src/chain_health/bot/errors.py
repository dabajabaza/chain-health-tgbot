import contextlib
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, Message

from chain_health.bot import texts
from chain_health.bot._telegram import is_benign_edit_error
from chain_health.bot.limits import TELEGRAM_CALLBACK_ANSWER_MAX_LEN
from chain_health.domain.errors import (
    DomainError,
    InvalidOperationError,
    NotFoundError,
    StaleMessageError,
)

logger = logging.getLogger(__name__)

_DOMAIN_MESSAGES: dict[type[DomainError], str] = {
    NotFoundError: texts.ERR_NOT_FOUND,
    InvalidOperationError: texts.ERR_INVALID_OPERATION,
    StaleMessageError: texts.ERR_STALE_MESSAGE,
}


async def on_error(
    event: ErrorEvent,
    bot: Bot,
    state: FSMContext | None = None,
) -> bool:
    """Global fallback: maps a raised exception to one user-facing reply and
    always answers a pending callback query, so the Telegram spinner never
    hangs. Registered via ``dp.errors.register`` (see __main__.build_dispatcher).

    Runs in a *fresh* dishka request scope — the one that raised was already
    rolled back and closed by the time this fires — so it deliberately takes
    no dishka dependencies (bot is already in the update's middleware data).
    """
    exc = event.exception
    callback = event.update.callback_query
    message = event.update.message

    if isinstance(exc, TelegramBadRequest) and is_benign_edit_error(exc):
        await _answer(bot, callback, message, None)
        return True

    if isinstance(exc, DomainError):
        logger.info("Domain error on update %s: %r", event.update.update_id, exc)
        text = _DOMAIN_MESSAGES.get(type(exc), texts.ERR_UNEXPECTED)
    elif isinstance(exc, TelegramBadRequest | TelegramForbiddenError):
        logger.warning("Telegram API error on update %s: %r", event.update.update_id, exc)
        text = texts.ERR_STALE_MESSAGE
    else:
        logger.error("Unhandled error on update %s", event.update.update_id, exc_info=exc)
        text = texts.ERR_UNEXPECTED

    # Read the FSM state fresh here rather than trusting a "raw_state" kwarg
    # snapshotted before the handler ran: a handler can set a *new* state
    # (e.g. cb_group_add's state.set_state(AddGroup.name)) and then fail —
    # the pre-handler snapshot would still show no dialog, so the dangling
    # state that handler just wrote would never get cleared. state.clear()
    # is a harmless no-op when there was nothing to clear.
    if state is not None:
        await state.clear()

    await _answer(bot, callback, message, text)
    return True


async def _answer(
    bot: Bot, callback: CallbackQuery | None, message: Message | None, text: str | None
) -> None:
    with contextlib.suppress(TelegramAPIError):
        if callback is not None:
            await callback.answer(text[:TELEGRAM_CALLBACK_ANSWER_MAX_LEN] if text else None)
        elif message is not None and text is not None:
            await bot.send_message(message.chat.id, text)
