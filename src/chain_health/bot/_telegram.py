"""Low-level helpers over the aiogram types, below the Responder.

Separate from ui.py because both the Responder and the dp.errors handler need
`is_benign_edit_error`: leaving it in ui.py forced bot/errors.py to import the
transport it sits above.
"""

from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.types import InaccessibleMessage, MaybeInaccessibleMessageUnion, Message

from chain_health.domain.errors import StaleMessageError

_BENIGN_EDIT_ERRORS = ("message is not modified",)


def dump(method: TelegramMethod[Any]) -> str:
    """An aiogram method as JSON — only the fields the caller set explicitly.

    exclude_unset is required, not a size optimization: an unset field holds
    aiogram's `Default` sentinel, which asks for a bot-level setting at send
    time. It is not serializable, and it should not be — on revival the field
    is unset again and picks up the default again.
    """
    return method.model_dump_json(exclude_unset=True)


def is_benign_edit_error(exc: TelegramBadRequest) -> bool:
    message = str(exc).lower()
    return any(pattern in message for pattern in _BENIGN_EDIT_ERRORS)


def require_editable(message: MaybeInaccessibleMessageUnion | None) -> Message:
    """The message behind an inline keyboard, if it can still be edited.

    Checked when the intent is recorded, not when it is sent: a callback whose
    origin message is gone or older than 48h is a bad request we can reject
    before touching any data, and the dp.errors handler turns StaleMessageError
    into one friendly toast instead of a traceback with a hung spinner.
    """
    if message is None or isinstance(message, InaccessibleMessage):
        raise StaleMessageError("Message is no longer accessible")
    return message
