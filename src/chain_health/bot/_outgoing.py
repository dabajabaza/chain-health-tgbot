"""What the Responder records before anything is sent.

The vocabulary of the queue, kept apart from the delivery logic in ui.py: these
types are also what runtime/outbox.py reasons about when it revives a promise.
"""

from dataclasses import dataclass
from typing import Any, Literal

from aiogram.methods import TelegramMethod

# raise    — a failure is a real failure and reaches dp.errors;
# not_modified — an edit reporting "not modified" succeeded: the user pressed
#            the same button twice and the screen already shows what it should;
# quiet    — cosmetic (dropping a spent keyboard); failing changes nothing.
OnError = Literal["raise", "not_modified", "quiet"]


@dataclass(slots=True)
class Call:
    """One Bot API call, plus how much its failure matters."""

    method: TelegramMethod[Any]
    on_error: OnError = "raise"
    # Whether this call should survive the process dying — see db.models.OutboxMessage.
    durable: bool = False
    # Set when the promise is written into the transaction.
    outbox_id: int | None = None


@dataclass(slots=True)
class PinnedSync:
    """Bring the user's pinned status message up to date.

    Compound on purpose: whether this edits or recreates depends on what
    Telegram answers, so it cannot be flattened into a single method at
    recording time. Everything it needs from the database (the text, the known
    message id) is read while recording, inside the transaction.
    """

    chat_id: int
    user_id: int
    text: str
    known_message_id: int | None
