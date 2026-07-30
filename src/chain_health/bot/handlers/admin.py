from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka import FromDishka

from chain_health.bot import texts
from chain_health.bot.context import message_user_id
from chain_health.services.access import AccessService

router = Router(name="admin")


@router.message(Command("invite"))
async def cmd_invite(message: Message, access: FromDishka[AccessService]) -> None:
    user_id = message_user_id(message)
    if not access.is_admin(user_id):
        return
    invite = await access.create_invite(user_id)
    assert message.bot is not None
    bot_user = await message.bot.get_me()
    link = f"https://t.me/{bot_user.username}?start={invite.code}"
    await message.answer(texts.invite_created(link))


@router.message(Command("allow"))
async def cmd_allow(
    message: Message, command: CommandObject, access: FromDishka[AccessService]
) -> None:
    if not access.is_admin(message_user_id(message)):
        return
    args = (command.args or "").strip()
    # isdecimal(), not isdigit(): isdigit() is true for characters like the
    # superscript "³" that int() then rejects with ValueError.
    if not args.isdecimal():
        await message.answer(texts.ALLOW_USAGE)
        return
    user_id = int(args)
    await access.allow_user(user_id)
    await message.answer(texts.user_allowed(user_id))
