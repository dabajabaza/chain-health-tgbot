from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from dishka import FromDishka

from chain_health.bot import texts
from chain_health.bot.context import message_user_id
from chain_health.bot.states import Onboarding
from chain_health.bot.ui import Responder
from chain_health.domain.constants import MAX_NAME_LENGTH
from chain_health.services.garage import GarageService

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    garage: FromDishka[GarageService],
) -> None:
    groups = await garage.list_groups(message_user_id(message))
    if groups:
        await state.clear()
        await message.answer(texts.WELCOME_BACK)
        return
    await state.set_state(Onboarding.group_name)
    await message.answer(texts.ONBOARDING_ASK_GROUP_NAME)


@router.message(Onboarding.group_name)
async def onboarding_group_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer(texts.ONBOARDING_ASK_GROUP_NAME)
        return
    if len(name) > MAX_NAME_LENGTH:
        await message.answer(texts.ASK_NAME_TOO_LONG_RETRY)
        return
    await state.update_data(group_name=name)
    await state.set_state(Onboarding.chain_name)
    await message.answer(texts.ONBOARDING_ASK_CHAIN_NAME)


@router.message(Onboarding.chain_name)
async def onboarding_chain_name(
    message: Message,
    state: FSMContext,
    garage: FromDishka[GarageService],
    responder: FromDishka[Responder],
) -> None:
    chain_name = (message.text or "").strip()
    if not chain_name:
        await message.answer(texts.ONBOARDING_ASK_CHAIN_NAME)
        return
    if len(chain_name) > MAX_NAME_LENGTH:
        await message.answer(texts.ASK_NAME_TOO_LONG_RETRY)
        return

    data = await state.get_data()
    group_name = data["group_name"]
    user_id = message_user_id(message)
    group = await garage.create_group(user_id, group_name)
    await garage.add_chain(group, chain_name)
    await state.clear()

    await responder.reply(
        message.chat.id,
        user_id,
        texts.onboarding_done(group_name, chain_name),
        data_changed=True,
    )
