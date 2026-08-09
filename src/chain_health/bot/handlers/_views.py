"""Screens shared by the group and chain handlers.

Both live here rather than in one of the handler modules because the sharing
crosses subjects in both directions: retiring a chain (`chains.py`) ends on the
group screen, and creating a chain (`groups.py`) does too. Keeping either view
in a subject module would make one subject import the other.
"""

from aiogram.types import InlineKeyboardMarkup

from chain_health.bot import keyboards, texts
from chain_health.services.garage import GarageService
from chain_health.services.status import StatusService


async def group_detail_view(
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


async def chain_detail_view(
    garage: GarageService, status_service: StatusService, user_id: int, chain_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    chain = await garage.require_chain(user_id, chain_id)
    status = await status_service.chain_status(chain)
    text = texts.chain_detail_text(status)
    keyboard = keyboards.chain_detail_keyboard(chain, chain.group_id)
    return text, keyboard
