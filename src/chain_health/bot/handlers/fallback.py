import contextlib
import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

from chain_health.bot import texts

logger = logging.getLogger(__name__)

router = Router(name="fallback")


@router.callback_query()
async def cb_unknown(callback: CallbackQuery) -> None:
    """Catches any callback no other router matched.

    An unmatched callback returns UNHANDLED with no exception raised, so
    dp.errors never sees it — without this, the Telegram spinner would hang
    with no way to recover. Must be the LAST router included.
    """
    logger.info("Unhandled callback: data=%r user_id=%s", callback.data, callback.from_user.id)
    with contextlib.suppress(TelegramAPIError):
        await callback.answer(texts.ERR_STALE_BUTTON)
