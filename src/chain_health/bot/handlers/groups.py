"""Groups: create, view, make current, add a chain, set the default limit."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka import FromDishka

from chain_health.bot import texts
from chain_health.bot.callbacks import GroupAction, GroupCB
from chain_health.bot.context import callback_chat_id, message_user_id
from chain_health.bot.states import AddChain, AddGroup, EditGroupLimit
from chain_health.bot.ui import Responder, ask_number_or_retry, start_edit_dialog
from chain_health.domain.constants import MAX_CYCLE_LIMIT_KM, MAX_NAME_LENGTH
from chain_health.services.garage import GarageService
from chain_health.services.status import StatusService

from ._views import group_detail_view

router = Router(name="groups")


@router.callback_query(GroupCB.filter(F.action == GroupAction.ADD))
async def cb_group_add(
    callback: CallbackQuery, state: FSMContext, responder: FromDishka[Responder]
) -> None:
    await state.set_state(AddGroup.name)
    responder.edit(callback.message, texts.ASK_NEW_GROUP_NAME)
    responder.answer_callback(callback)


@router.message(AddGroup.name)
async def fsm_add_group_name(
    message: Message,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
    responder: FromDishka[Responder],
) -> None:
    name = (message.text or "").strip()
    if not name:
        responder.reply_raw(message.chat.id, texts.ASK_NEW_GROUP_NAME)
        return
    if len(name) > MAX_NAME_LENGTH:
        responder.reply_raw(message.chat.id, texts.ASK_NAME_TOO_LONG_RETRY)
        return
    user_id = message_user_id(message)
    group = await garage.create_group(user_id, name)
    await state.clear()
    text, keyboard = await group_detail_view(garage, status_service, user_id, group.id)
    responder.reply_raw(message.chat.id, text, keyboard)


@router.callback_query(GroupCB.filter(F.action == GroupAction.VIEW))
async def cb_group_view(
    callback: CallbackQuery,
    callback_data: GroupCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
    responder: FromDishka[Responder],
) -> None:
    await state.clear()
    text, keyboard = await group_detail_view(
        garage, status_service, callback.from_user.id, callback_data.group_id
    )
    responder.edit(callback.message, text, keyboard)
    responder.answer_callback(callback)


@router.callback_query(GroupCB.filter(F.action == GroupAction.SET_CURRENT))
async def cb_group_set_current(
    callback: CallbackQuery,
    callback_data: GroupCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
    responder: FromDishka[Responder],
) -> None:
    await state.clear()
    user_id = callback.from_user.id
    group = await garage.require_group(user_id, callback_data.group_id)
    await garage.set_current_group(user_id, group)
    text, keyboard = await group_detail_view(garage, status_service, user_id, group.id)
    responder.edit(callback.message, text, keyboard)
    await responder.sync_pinned(callback_chat_id(callback), user_id)
    responder.answer_callback(callback)


@router.callback_query(GroupCB.filter(F.action == GroupAction.ADD_CHAIN))
async def cb_group_add_chain(
    callback: CallbackQuery,
    callback_data: GroupCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
    responder: FromDishka[Responder],
) -> None:
    group = await garage.require_group(callback.from_user.id, callback_data.group_id)
    await start_edit_dialog(
        callback, state, AddChain.name, texts.ASK_NEW_CHAIN_NAME, {"group_id": group.id}, responder
    )


@router.message(AddChain.name)
async def fsm_add_chain_name(
    message: Message,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
    responder: FromDishka[Responder],
) -> None:
    name = (message.text or "").strip()
    if not name:
        responder.reply_raw(message.chat.id, texts.ASK_NEW_CHAIN_NAME)
        return
    if len(name) > MAX_NAME_LENGTH:
        responder.reply_raw(message.chat.id, texts.ASK_NAME_TOO_LONG_RETRY)
        return
    user_id = message_user_id(message)
    data = await state.get_data()
    group = await garage.require_group(user_id, data["group_id"])
    await garage.add_chain(group, name)
    await state.clear()
    text, keyboard = await group_detail_view(garage, status_service, user_id, group.id)
    responder.reply_raw(message.chat.id, text, keyboard)
    await responder.sync_pinned(message.chat.id, user_id)


@router.callback_query(GroupCB.filter(F.action == GroupAction.SET_LIMIT))
async def cb_group_set_limit(
    callback: CallbackQuery,
    callback_data: GroupCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
    responder: FromDishka[Responder],
) -> None:
    group = await garage.require_group(callback.from_user.id, callback_data.group_id)
    await start_edit_dialog(
        callback,
        state,
        EditGroupLimit.value,
        texts.ASK_GROUP_LIMIT,
        {"group_id": group.id},
        responder,
    )


@router.message(EditGroupLimit.value)
async def fsm_group_limit(
    message: Message,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
    responder: FromDishka[Responder],
) -> None:
    value = ask_number_or_retry(message, responder, max_value=MAX_CYCLE_LIMIT_KM)
    if value is None:
        return
    user_id = message_user_id(message)
    data = await state.get_data()
    group = await garage.require_group(user_id, data["group_id"])
    await garage.set_group_default_limit(group, value)
    await state.clear()
    text, keyboard = await group_detail_view(garage, status_service, user_id, group.id)
    responder.reply_raw(message.chat.id, text, keyboard)
