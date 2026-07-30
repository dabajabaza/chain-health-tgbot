from collections.abc import Sequence

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from chain_health.bot import texts
from chain_health.bot.callbacks import (
    ChainAction,
    ChainCB,
    GroupAction,
    GroupCB,
    MenuAction,
    MenuCB,
    RideAction,
    RideCB,
    RotateAction,
    RotateCB,
)
from chain_health.db.models import Chain, Group, Ride


def _back_row(callback_data: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=texts.BTN_BACK, callback_data=callback_data)]


def main_reply_keyboard(placeholder: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BTN_STATUS), KeyboardButton(text=texts.BTN_MENU)]],
        resize_keyboard=True,
        input_field_placeholder=placeholder,
    )


def rotation_prompt_keyboard(group_id: int) -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(
        text=texts.ROTATE_BUTTON,
        callback_data=RotateCB(action=RotateAction.MENU, group_id=group_id).pack(),
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def root_menu_keyboard(current_group: Group | None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=texts.BTN_MY_GROUPS, callback_data=MenuCB(action=MenuAction.GROUPS).pack()
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.BTN_RECENT_RIDES, callback_data=MenuCB(action=MenuAction.RIDES).pack()
            )
        ],
    ]
    if current_group is not None:
        rotate_cb = RotateCB(action=RotateAction.MENU, group_id=current_group.id).pack()
        rows.append([InlineKeyboardButton(text=texts.ROTATE_BUTTON, callback_data=rotate_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def groups_list_keyboard(
    groups: Sequence[Group], current_group_id: int | None
) -> InlineKeyboardMarkup:
    rows = []
    for group in groups:
        label = ("✅ " if group.id == current_group_id else "") + group.name
        cb = GroupCB(action=GroupAction.VIEW, group_id=group.id).pack()
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_ADD_GROUP,
                callback_data=GroupCB(action=GroupAction.ADD).pack(),
            )
        ]
    )
    rows.append(_back_row(MenuCB(action=MenuAction.ROOT).pack()))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group_detail_keyboard(
    group: Group, chains: Sequence[Chain], *, is_current: bool
) -> InlineKeyboardMarkup:
    rows = []
    for chain in chains:
        label = ("🟢 " if chain.is_active else "⚪ ") + chain.name
        cb = ChainCB(action=ChainAction.VIEW, chain_id=chain.id, group_id=group.id).pack()
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])

    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_ADD_CHAIN,
                callback_data=GroupCB(action=GroupAction.ADD_CHAIN, group_id=group.id).pack(),
            )
        ]
    )
    if not is_current:
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.BTN_SET_CURRENT,
                    callback_data=GroupCB(action=GroupAction.SET_CURRENT, group_id=group.id).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_GROUP_LIMIT,
                callback_data=GroupCB(action=GroupAction.SET_LIMIT, group_id=group.id).pack(),
            )
        ]
    )
    rows.append(_back_row(MenuCB(action=MenuAction.GROUPS).pack()))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chain_detail_keyboard(chain: Chain, group_id: int) -> InlineKeyboardMarkup:
    def cb(action: ChainAction) -> str:
        return ChainCB(action=action, chain_id=chain.id, group_id=group_id).pack()

    rows = [
        [InlineKeyboardButton(text=texts.BTN_CHAIN_LIMIT, callback_data=cb(ChainAction.SET_LIMIT))],
        [
            InlineKeyboardButton(
                text=texts.BTN_CHAIN_RESOURCE, callback_data=cb(ChainAction.SET_RESOURCE)
            )
        ],
    ]
    if not chain.is_retired:
        rows.append(
            [InlineKeyboardButton(text=texts.BTN_RETIRE, callback_data=cb(ChainAction.RETIRE))]
        )
    rows.append(_back_row(GroupCB(action=GroupAction.VIEW, group_id=group_id).pack()))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rotation_candidates_keyboard(
    group_id: int, candidates: Sequence[Chain], recommended: Chain | None, totals: dict[int, float]
) -> InlineKeyboardMarkup:
    rows = []
    for chain in candidates:
        label = ("⭐ " if recommended is not None and chain.id == recommended.id else "") + (
            f"{chain.name} ({texts.fmt_km(totals[chain.id])} км)"
        )
        cb = RotateCB(action=RotateAction.CONFIRM, group_id=group_id, chain_id=chain.id).pack()
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])
    rows.append(_back_row(GroupCB(action=GroupAction.VIEW, group_id=group_id).pack()))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rides_list_keyboard(rides: Sequence[Ride]) -> InlineKeyboardMarkup:
    rows = []
    for ride in rides:
        label = f"{ride.ride_dt} · {ride.chain.name} · {texts.fmt_km(ride.distance_km)} км"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=RideCB(action=RideAction.EDIT, ride_id=ride.id).pack(),
                ),
                InlineKeyboardButton(
                    text="🗑",
                    callback_data=RideCB(action=RideAction.DELETE, ride_id=ride.id).pack(),
                ),
            ]
        )
    rows.append(_back_row(MenuCB(action=MenuAction.ROOT).pack()))
    return InlineKeyboardMarkup(inline_keyboard=rows)
