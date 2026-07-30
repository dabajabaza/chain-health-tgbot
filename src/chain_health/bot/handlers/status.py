from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from dishka import FromDishka

from chain_health.bot import texts
from chain_health.bot.context import message_user_id
from chain_health.bot.ui import Responder
from chain_health.services.status import StatusService

router = Router(name="status")


@router.message(or_f(Command("status"), F.text == texts.BTN_STATUS))
async def show_status(
    message: Message,
    state: FSMContext,
    status_service: FromDishka[StatusService],
    responder: FromDishka[Responder],
) -> None:
    # No StateFilter here on purpose: 📊 Статус/`/status` must always work as
    # an escape hatch out of a dialog, not just when there isn't one — this
    # router is checked before menu.router's fsm_* handlers (see D3).
    await state.clear()
    user_id = message_user_id(message)
    view = await status_service.build(user_id)
    await responder.reply(message.chat.id, user_id, texts.pinned_status_text(view))
