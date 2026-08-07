from chain_health.bot import texts
from chain_health.bot.ui import Responder
from chain_health.domain.values import ChainStatus


class TelegramReminderNotifier:
    """``ReminderNotifier`` over the Telegram Bot API.

    Private chats only (enforced by PrivateChatOnlyMiddleware — see
    docs/ARCHITECTURE.md D8), so ``chat_id == user_id`` for every user this
    notifier can reach.
    """

    def __init__(self, responder: Responder) -> None:
        self._responder = responder

    async def send_reminder(self, user_id: int, status: ChainStatus) -> None:
        await self._responder.reply(user_id, user_id, texts.reminder_text(status))

    async def deliver_pending(self) -> None:
        await self._responder.flush()
