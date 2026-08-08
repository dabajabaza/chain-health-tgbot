from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from dishka import FromDishka

from chain_health.bot import texts
from chain_health.bot.context import message_user_id
from chain_health.bot.ui import Responder
from chain_health.services.access import AccessService

router = Router(name="admin")


async def _stop_dialog(state: FSMContext) -> None:
    """An admin command interrupts an open dialog, the way /menu does.

    The admin router is included first, so its handlers take the update whole
    and propagation stops before any fsm_* handler — meaning the state is never
    cleared. A user who taps "add a chain", then types /invite instead of the
    name, leaves AddChain.name live: their next unrelated message is silently
    swallowed as a chain name.
    """
    await state.clear()


@router.message(Command("invite"))
async def cmd_invite(
    message: Message,
    state: FSMContext,
    access: FromDishka[AccessService],
    responder: FromDishka[Responder],
) -> None:
    user_id = message_user_id(message)
    if not access.is_admin(user_id):
        return
    await _stop_dialog(state)
    invite = await access.create_invite(user_id)
    assert message.bot is not None
    # me(), not get_me(): aiogram caches the result and __main__ warms it at
    # startup, so this command does no network I/O inside its transaction.
    bot_user = await message.bot.me()
    link = f"https://t.me/{bot_user.username}?start={invite.code}"
    responder.reply_raw(message.chat.id, texts.invite_created(link))


@router.message(Command("allow"))
async def cmd_allow(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    access: FromDishka[AccessService],
    responder: FromDishka[Responder],
) -> None:
    if not access.is_admin(message_user_id(message)):
        return
    await _stop_dialog(state)
    args = (command.args or "").strip()
    # isdecimal(), not isdigit(): isdigit() is true for characters like the
    # superscript "³" that int() then rejects with ValueError.
    if not args.isdecimal():
        responder.reply_raw(message.chat.id, texts.ALLOW_USAGE)
        return
    user_id = int(args)
    await access.allow_user(user_id)
    responder.reply_raw(message.chat.id, texts.user_allowed(user_id))
