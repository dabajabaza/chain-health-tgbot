from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message
from dishka import FromDishka

from chain_health.bot import keyboards, texts
from chain_health.bot.context import message_user_id
from chain_health.bot.parsing import NUMERIC_MESSAGE_RE, parse_positive_float
from chain_health.bot.ui import Responder
from chain_health.domain.constants import MAX_DISTANCE_KM
from chain_health.domain.errors import NoActiveChainError
from chain_health.services.mileage import MileageService

router = Router(name="mileage")


@router.message(F.text.regexp(NUMERIC_MESSAGE_RE), StateFilter(None))
async def add_mileage(
    message: Message,
    mileage: FromDishka[MileageService],
    responder: FromDishka[Responder],
) -> None:
    distance_km = parse_positive_float(message.text, max_value=MAX_DISTANCE_KM)
    if distance_km is None:
        await message.answer(texts.ASK_DISTANCE_RETRY)
        return

    user_id = message_user_id(message)
    try:
        recorded = await mileage.record_ride(user_id=user_id, distance_km=distance_km)
    except NoActiveChainError:
        await message.answer(texts.NO_ACTIVE_CHAIN)
        return

    text = texts.ride_recorded_text(recorded.group.name, recorded.status, distance_km)
    if recorded.status.over_limit:
        keyboard = keyboards.rotation_prompt_keyboard(recorded.group.id)
        await responder.reply_with_inline_keyboard(
            message.chat.id, user_id, text, keyboard, data_changed=True
        )
    else:
        await responder.reply(message.chat.id, user_id, text, data_changed=True)
