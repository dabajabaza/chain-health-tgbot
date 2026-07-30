from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from dishka import FromDishka

from chain_health.bot import keyboards, texts
from chain_health.bot.callbacks import MenuAction, MenuCB, RideAction, RideCB
from chain_health.bot.context import callback_chat_id, message_user_id
from chain_health.bot.states import EditRideAmount
from chain_health.bot.ui import (
    Responder,
    ask_number_or_retry,
    edit_text_or_ignore,
    start_edit_dialog,
)
from chain_health.domain.constants import MAX_DISTANCE_KM
from chain_health.services.rides import RideService

router = Router(name="rides")


async def _rides_list_view(
    rides_service: RideService, user_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    rides = await rides_service.recent_rides(user_id)
    text = texts.RIDES_LIST_HEADER if rides else texts.RIDES_EMPTY
    return text, keyboards.rides_list_keyboard(rides)


@router.callback_query(MenuCB.filter(F.action == MenuAction.RIDES))
async def cb_menu_rides(
    callback: CallbackQuery, state: FSMContext, rides_service: FromDishka[RideService]
) -> None:
    await state.clear()
    text, keyboard = await _rides_list_view(rides_service, callback.from_user.id)
    await edit_text_or_ignore(callback.message, text, keyboard)
    await callback.answer()


@router.callback_query(RideCB.filter(F.action == RideAction.DELETE))
async def cb_ride_delete(
    callback: CallbackQuery,
    callback_data: RideCB,
    state: FSMContext,
    rides_service: FromDishka[RideService],
    responder: FromDishka[Responder],
) -> None:
    await state.clear()
    user_id = callback.from_user.id
    ride = await rides_service.require_ride(user_id, callback_data.ride_id)
    await rides_service.delete_ride(ride)
    text, keyboard = await _rides_list_view(rides_service, user_id)
    await edit_text_or_ignore(callback.message, text, keyboard)
    await responder.sync_pinned(callback_chat_id(callback), user_id)
    await callback.answer(texts.ride_deleted_text())


@router.callback_query(RideCB.filter(F.action == RideAction.EDIT))
async def cb_ride_edit(
    callback: CallbackQuery,
    callback_data: RideCB,
    state: FSMContext,
    rides_service: FromDishka[RideService],
) -> None:
    ride = await rides_service.require_ride(callback.from_user.id, callback_data.ride_id)
    await start_edit_dialog(
        callback, state, EditRideAmount.value, texts.ASK_RIDE_AMOUNT, {"ride_id": ride.id}
    )


@router.message(EditRideAmount.value)
async def fsm_ride_amount(
    message: Message,
    state: FSMContext,
    rides_service: FromDishka[RideService],
    responder: FromDishka[Responder],
) -> None:
    value = await ask_number_or_retry(message, max_value=MAX_DISTANCE_KM)
    if value is None:
        return
    user_id = message_user_id(message)
    data = await state.get_data()
    ride = await rides_service.require_ride(user_id, data["ride_id"])
    await rides_service.edit_ride(ride, value)
    await state.clear()
    await message.answer(texts.ride_updated_text(value))
    await responder.sync_pinned(message.chat.id, user_id)
