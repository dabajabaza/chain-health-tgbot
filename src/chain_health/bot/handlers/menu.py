"""The menu root: `/menu`, the ☰ button, and the group list."""

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka import FromDishka

from chain_health.bot import keyboards, texts
from chain_health.bot.callbacks import MenuAction, MenuCB
from chain_health.bot.context import message_user_id
from chain_health.bot.ui import Responder
from chain_health.services.garage import GarageService

router = Router(name="menu")


@router.message(or_f(Command("menu"), F.text == texts.BTN_MENU))
async def open_menu(
    message: Message,
    state: FSMContext,
    garage: FromDishka[GarageService],
    responder: FromDishka[Responder],
) -> None:
    # No StateFilter here on purpose: ☰ Меню/`/menu` must always work as an
    # escape hatch out of a dialog, not just when there isn't one — registered
    # first in this router, so it's tried before any fsm_* handler below for
    # the same update (see D3, same pattern as status.py's show_status).
    await state.clear()
    current_group = await garage.current_group(message_user_id(message))
    responder.reply_raw(
        message.chat.id, texts.MENU_ROOT, keyboards.root_menu_keyboard(current_group)
    )


@router.callback_query(MenuCB.filter(F.action == MenuAction.ROOT))
async def cb_menu_root(
    callback: CallbackQuery,
    state: FSMContext,
    garage: FromDishka[GarageService],
    responder: FromDishka[Responder],
) -> None:
    await state.clear()
    current_group = await garage.current_group(callback.from_user.id)
    responder.edit(callback.message, texts.MENU_ROOT, keyboards.root_menu_keyboard(current_group))
    responder.answer_callback(callback)


@router.callback_query(MenuCB.filter(F.action == MenuAction.GROUPS))
async def cb_menu_groups(
    callback: CallbackQuery,
    state: FSMContext,
    garage: FromDishka[GarageService],
    responder: FromDishka[Responder],
) -> None:
    await state.clear()
    user_id = callback.from_user.id
    groups = await garage.list_groups(user_id)
    current_group = await garage.current_group(user_id)
    current_id = current_group.id if current_group is not None else None
    responder.edit(
        callback.message,
        texts.groups_list_text(len(groups)),
        keyboards.groups_list_keyboard(groups, current_id),
    )
    responder.answer_callback(callback)
