import logging

from aiogram import Router
from aiogram.types import CallbackQuery
from dishka import FromDishka

from chain_health.bot import texts
from chain_health.bot.ui import Responder

logger = logging.getLogger(__name__)

router = Router(name="fallback")


@router.callback_query()
async def cb_unknown(callback: CallbackQuery, responder: FromDishka[Responder]) -> None:
    """Catches any callback no other router matched.

    An unmatched callback returns UNHANDLED with no exception raised, so
    dp.errors never sees it — without this, the Telegram spinner would hang
    with no way to recover. Must be the LAST router included.
    """
    logger.info("Unhandled callback: data=%r user_id=%s", callback.data, callback.from_user.id)
    responder.answer_callback(callback, texts.ERR_STALE_BUTTON)
