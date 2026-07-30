from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from dishka import FromDishka

from chain_health.bot import keyboards, texts
from chain_health.bot.callbacks import (
    ChainAction,
    ChainCB,
    GroupAction,
    GroupCB,
    MenuAction,
    MenuCB,
)
from chain_health.bot.context import callback_chat_id, message_user_id
from chain_health.bot.parsing import parse_positive_float
from chain_health.bot.states import (
    AddChain,
    AddGroup,
    EditChainLimit,
    EditChainResource,
    EditGroupLimit,
)
from chain_health.bot.ui import (
    Responder,
    ask_number_or_retry,
    edit_text_or_ignore,
    start_edit_dialog,
)
from chain_health.domain.constants import MAX_CYCLE_LIMIT_KM, MAX_NAME_LENGTH
from chain_health.services.garage import GarageService
from chain_health.services.status import StatusService

router = Router(name="menu")


async def _group_detail_view(
    garage: GarageService, status_service: StatusService, user_id: int, group_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    group = await garage.require_group(user_id, group_id)
    statuses = await status_service.build_for_group(group)
    current_group = await garage.current_group(user_id)
    is_current = current_group is not None and current_group.id == group.id
    text = texts.group_detail_text(group, statuses)
    keyboard = keyboards.group_detail_keyboard(
        group, [s.chain for s in statuses], is_current=is_current
    )
    return text, keyboard


async def _chain_detail_view(
    garage: GarageService, status_service: StatusService, user_id: int, chain_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    chain = await garage.require_chain(user_id, chain_id)
    status = await status_service.chain_status(chain)
    text = texts.chain_detail_text(status)
    keyboard = keyboards.chain_detail_keyboard(chain, chain.group_id)
    return text, keyboard


@router.message(or_f(Command("menu"), F.text == texts.BTN_MENU))
async def open_menu(message: Message, state: FSMContext, garage: FromDishka[GarageService]) -> None:
    # No StateFilter here on purpose: ☰ Меню/`/menu` must always work as an
    # escape hatch out of a dialog, not just when there isn't one — registered
    # first in this router, so it's tried before any fsm_* handler below for
    # the same update (see D3, same pattern as status.py's show_status).
    await state.clear()
    current_group = await garage.current_group(message_user_id(message))
    await message.answer(texts.MENU_ROOT, reply_markup=keyboards.root_menu_keyboard(current_group))


@router.callback_query(MenuCB.filter(F.action == MenuAction.ROOT))
async def cb_menu_root(
    callback: CallbackQuery, state: FSMContext, garage: FromDishka[GarageService]
) -> None:
    await state.clear()
    current_group = await garage.current_group(callback.from_user.id)
    await edit_text_or_ignore(
        callback.message, texts.MENU_ROOT, keyboards.root_menu_keyboard(current_group)
    )
    await callback.answer()


@router.callback_query(MenuCB.filter(F.action == MenuAction.GROUPS))
async def cb_menu_groups(
    callback: CallbackQuery, state: FSMContext, garage: FromDishka[GarageService]
) -> None:
    await state.clear()
    user_id = callback.from_user.id
    groups = await garage.list_groups(user_id)
    current_group = await garage.current_group(user_id)
    current_id = current_group.id if current_group is not None else None
    await edit_text_or_ignore(
        callback.message,
        texts.groups_list_text(len(groups)),
        keyboards.groups_list_keyboard(groups, current_id),
    )
    await callback.answer()


@router.callback_query(GroupCB.filter(F.action == GroupAction.ADD))
async def cb_group_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddGroup.name)
    await edit_text_or_ignore(callback.message, texts.ASK_NEW_GROUP_NAME)
    await callback.answer()


@router.message(AddGroup.name)
async def fsm_add_group_name(
    message: Message,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer(texts.ASK_NEW_GROUP_NAME)
        return
    if len(name) > MAX_NAME_LENGTH:
        await message.answer(texts.ASK_NAME_TOO_LONG_RETRY)
        return
    user_id = message_user_id(message)
    group = await garage.create_group(user_id, name)
    await state.clear()
    text, keyboard = await _group_detail_view(garage, status_service, user_id, group.id)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(GroupCB.filter(F.action == GroupAction.VIEW))
async def cb_group_view(
    callback: CallbackQuery,
    callback_data: GroupCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
) -> None:
    await state.clear()
    text, keyboard = await _group_detail_view(
        garage, status_service, callback.from_user.id, callback_data.group_id
    )
    await edit_text_or_ignore(callback.message, text, keyboard)
    await callback.answer()


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
    text, keyboard = await _group_detail_view(garage, status_service, user_id, group.id)
    await edit_text_or_ignore(callback.message, text, keyboard)
    await responder.sync_pinned(callback_chat_id(callback), user_id)
    await callback.answer()


@router.callback_query(GroupCB.filter(F.action == GroupAction.ADD_CHAIN))
async def cb_group_add_chain(
    callback: CallbackQuery,
    callback_data: GroupCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
) -> None:
    group = await garage.require_group(callback.from_user.id, callback_data.group_id)
    await start_edit_dialog(
        callback, state, AddChain.name, texts.ASK_NEW_CHAIN_NAME, {"group_id": group.id}
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
        await message.answer(texts.ASK_NEW_CHAIN_NAME)
        return
    if len(name) > MAX_NAME_LENGTH:
        await message.answer(texts.ASK_NAME_TOO_LONG_RETRY)
        return
    user_id = message_user_id(message)
    data = await state.get_data()
    group = await garage.require_group(user_id, data["group_id"])
    await garage.add_chain(group, name)
    await state.clear()
    text, keyboard = await _group_detail_view(garage, status_service, user_id, group.id)
    await message.answer(text, reply_markup=keyboard)
    await responder.sync_pinned(message.chat.id, user_id)


@router.callback_query(GroupCB.filter(F.action == GroupAction.SET_LIMIT))
async def cb_group_set_limit(
    callback: CallbackQuery,
    callback_data: GroupCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
) -> None:
    group = await garage.require_group(callback.from_user.id, callback_data.group_id)
    await start_edit_dialog(
        callback, state, EditGroupLimit.value, texts.ASK_GROUP_LIMIT, {"group_id": group.id}
    )


@router.message(EditGroupLimit.value)
async def fsm_group_limit(
    message: Message,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
) -> None:
    value = await ask_number_or_retry(message, max_value=MAX_CYCLE_LIMIT_KM)
    if value is None:
        return
    user_id = message_user_id(message)
    data = await state.get_data()
    group = await garage.require_group(user_id, data["group_id"])
    await garage.set_group_default_limit(group, value)
    await state.clear()
    text, keyboard = await _group_detail_view(garage, status_service, user_id, group.id)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(ChainCB.filter(F.action == ChainAction.VIEW))
async def cb_chain_view(
    callback: CallbackQuery,
    callback_data: ChainCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
) -> None:
    await state.clear()
    text, keyboard = await _chain_detail_view(
        garage, status_service, callback.from_user.id, callback_data.chain_id
    )
    await edit_text_or_ignore(callback.message, text, keyboard)
    await callback.answer()


@router.callback_query(ChainCB.filter(F.action == ChainAction.SET_LIMIT))
async def cb_chain_set_limit(
    callback: CallbackQuery,
    callback_data: ChainCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
) -> None:
    chain = await garage.require_chain(callback.from_user.id, callback_data.chain_id)
    await start_edit_dialog(
        callback, state, EditChainLimit.value, texts.ASK_CHAIN_LIMIT, {"chain_id": chain.id}
    )


@router.message(EditChainLimit.value)
async def fsm_chain_limit(
    message: Message,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
) -> None:
    value = await ask_number_or_retry(message, max_value=MAX_CYCLE_LIMIT_KM)
    if value is None:
        return
    user_id = message_user_id(message)
    data = await state.get_data()
    chain = await garage.require_chain(user_id, data["chain_id"])
    await garage.set_chain_limit(chain, value)
    await state.clear()
    text, keyboard = await _chain_detail_view(garage, status_service, user_id, chain.id)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(ChainCB.filter(F.action == ChainAction.SET_RESOURCE))
async def cb_chain_set_resource(
    callback: CallbackQuery,
    callback_data: ChainCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
) -> None:
    chain = await garage.require_chain(callback.from_user.id, callback_data.chain_id)
    await start_edit_dialog(
        callback, state, EditChainResource.value, texts.ASK_CHAIN_RESOURCE, {"chain_id": chain.id}
    )


@router.message(EditChainResource.value)
async def fsm_chain_resource(
    message: Message,
    state: FSMContext,
    garage: FromDishka[GarageService],
    status_service: FromDishka[StatusService],
) -> None:
    raw = (message.text or "").strip()
    if raw == "-":
        value = None
    else:
        value = parse_positive_float(raw, max_value=MAX_CYCLE_LIMIT_KM)
        if value is None:
            await message.answer(texts.ASK_NUMBER_RETRY)
            return
    user_id = message_user_id(message)
    data = await state.get_data()
    chain = await garage.require_chain(user_id, data["chain_id"])
    await garage.set_chain_resource(chain, value)
    await state.clear()
    text, keyboard = await _chain_detail_view(garage, status_service, user_id, chain.id)
    await message.answer(text, reply_markup=keyboard)


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
    text, keyboard = await _group_detail_view(garage, status_service, user_id, chain.group_id)
    await edit_text_or_ignore(callback.message, text, keyboard)
    await responder.sync_pinned(callback_chat_id(callback), user_id)
    await callback.answer(texts.chain_retired_text(chain))
