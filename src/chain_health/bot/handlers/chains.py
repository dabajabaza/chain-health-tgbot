"""Chains: view, limit, resource, retire."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka import FromDishka

from chain_health.bot import texts
from chain_health.bot.callbacks import ChainAction, ChainCB
from chain_health.bot.context import callback_chat_id, message_user_id
from chain_health.bot.dialogs import ask_number_or_retry, start_edit_dialog
from chain_health.bot.parsing import parse_positive_float
from chain_health.bot.states import EditChainLimit, EditChainResource
from chain_health.bot.ui import Responder
from chain_health.domain.constants import MAX_CYCLE_LIMIT_KM
from chain_health.services.garage import GarageService
from chain_health.services.status import StatusService

from ._views import chain_detail_view, group_detail_view

router = Router(name="chains")


@router.callback_query(ChainCB.filter(F.action == ChainAction.VIEW))
async def cb_chain_view(
    callback: CallbackQuery,
    callback_data: ChainCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
    responder: FromDishka[Responder],
) -> None:
    await state.clear()
    text, keyboard = await chain_detail_view(
        garage, status_service, callback.from_user.id, callback_data.chain_id
    )
    responder.edit(callback.message, text, keyboard)
    responder.answer_callback(callback)


@router.callback_query(ChainCB.filter(F.action == ChainAction.SET_LIMIT))
async def cb_chain_set_limit(
    callback: CallbackQuery,
    callback_data: ChainCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
    responder: FromDishka[Responder],
) -> None:
    chain = await garage.require_chain(callback.from_user.id, callback_data.chain_id)
    await start_edit_dialog(
        callback,
        state,
        EditChainLimit.value,
        texts.ASK_CHAIN_LIMIT,
        {"chain_id": chain.id},
        responder,
    )


@router.message(EditChainLimit.value)
async def fsm_chain_limit(
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
    chain = await garage.require_chain(user_id, data["chain_id"])
    await garage.set_chain_limit(chain, value)
    await state.clear()
    text, keyboard = await chain_detail_view(garage, status_service, user_id, chain.id)
    responder.reply_raw(message.chat.id, text, keyboard)


@router.callback_query(ChainCB.filter(F.action == ChainAction.SET_RESOURCE))
async def cb_chain_set_resource(
    callback: CallbackQuery,
    callback_data: ChainCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
    responder: FromDishka[Responder],
) -> None:
    chain = await garage.require_chain(callback.from_user.id, callback_data.chain_id)
    await start_edit_dialog(
        callback,
        state,
        EditChainResource.value,
        texts.ASK_CHAIN_RESOURCE,
        {"chain_id": chain.id},
        responder,
    )


@router.message(EditChainResource.value)
async def fsm_chain_resource(
    message: Message,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
    responder: FromDishka[Responder],
) -> None:
    raw = (message.text or "").strip()
    if raw == "-":
        value = None
    else:
        value = parse_positive_float(raw, max_value=MAX_CYCLE_LIMIT_KM)
        if value is None:
            responder.reply_raw(message.chat.id, texts.ASK_NUMBER_RETRY)
            return
    user_id = message_user_id(message)
    data = await state.get_data()
    chain = await garage.require_chain(user_id, data["chain_id"])
    await garage.set_chain_resource(chain, value)
    await state.clear()
    text, keyboard = await chain_detail_view(garage, status_service, user_id, chain.id)
    responder.reply_raw(message.chat.id, text, keyboard)


@router.callback_query(ChainCB.filter(F.action == ChainAction.RETIRE))
async def cb_chain_retire(
    callback: CallbackQuery,
    callback_data: ChainCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
    responder: FromDishka[Responder],
) -> None:
    await state.clear()
    user_id = callback.from_user.id
    chain = await garage.require_chain(user_id, callback_data.chain_id)
    await garage.retire_chain(chain)
    text, keyboard = await group_detail_view(garage, status_service, user_id, chain.group_id)
    responder.edit(callback.message, text, keyboard)
    await responder.sync_pinned(callback_chat_id(callback), user_id)
    responder.answer_callback(callback, texts.chain_retired_text(chain))
