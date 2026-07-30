from enum import StrEnum

from aiogram.filters.callback_data import CallbackData

# CallbackData packs Enum fields via `str(value.value)` (same as a plain str
# field), so these are wire-compatible with the string literals they replace.


class MenuAction(StrEnum):
    ROOT = "root"
    GROUPS = "groups"
    RIDES = "rides"


class GroupAction(StrEnum):
    VIEW = "view"
    ADD = "add"
    SET_CURRENT = "set_current"
    ADD_CHAIN = "add_chain"
    SET_LIMIT = "set_limit"


class ChainAction(StrEnum):
    VIEW = "view"
    SET_LIMIT = "set_limit"
    SET_RESOURCE = "set_resource"
    RETIRE = "retire"


class RotateAction(StrEnum):
    MENU = "menu"
    CONFIRM = "confirm"


class RideAction(StrEnum):
    EDIT = "edit"
    DELETE = "delete"


class MenuCB(CallbackData, prefix="menu"):
    action: MenuAction


class GroupCB(CallbackData, prefix="group"):
    action: GroupAction
    group_id: int = 0


class ChainCB(CallbackData, prefix="chain"):
    action: ChainAction
    chain_id: int = 0
    group_id: int = 0


class RotateCB(CallbackData, prefix="rotate"):
    action: RotateAction
    group_id: int
    chain_id: int = 0


class RideCB(CallbackData, prefix="ride"):
    action: RideAction
    ride_id: int
