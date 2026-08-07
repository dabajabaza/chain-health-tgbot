from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka import FromDishka

from chain_health.bot import texts
from chain_health.bot.context import message_user_id
from chain_health.bot.ui import Responder
from chain_health.services.access import AccessService

router = Router(name="admin")


@router.message(Command("invite"))
async def cmd_invite(
    message: Message, access: FromDishka[AccessService], responder: FromDishka[Responder]
) -> None:
    user_id = message_user_id(message)
    if not access.is_admin(user_id):
        return
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
    access: FromDishka[AccessService],
    responder: FromDishka[Responder],
) -> None:
    if not access.is_admin(message_user_id(message)):
        return
    args = (command.args or "").strip()
    # isdecimal(), not isdigit(): isdigit() is true for characters like the
    # superscript "³" that int() then rejects with ValueError.
    if not args.isdecimal():
        responder.reply_raw(message.chat.id, texts.ALLOW_USAGE)
        return
    user_id = int(args)
    await access.allow_user(user_id)
    responder.reply_raw(message.chat.id, texts.user_allowed(user_id))
