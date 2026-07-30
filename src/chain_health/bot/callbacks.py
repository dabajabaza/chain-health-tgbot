from enum import StrEnum

from aiogram.filters.callback_data import CallbackData
from pydantic import Field

# CallbackData packs Enum fields via `str(value.value)` (same as a plain str
# field), so these are wire-compatible with the string literals they replace.


class MenuAction(StrEnum):
    """What a MenuCB button does."""

    ROOT = "root"
    GROUPS = "groups"
    RIDES = "rides"


class GroupAction(StrEnum):
    """What a GroupCB button does."""

    VIEW = "view"
    ADD = "add"
    SET_CURRENT = "set_current"
    ADD_CHAIN = "add_chain"
    SET_LIMIT = "set_limit"


class ChainAction(StrEnum):
    """What a ChainCB button does."""

    VIEW = "view"
    SET_LIMIT = "set_limit"
    SET_RESOURCE = "set_resource"
    RETIRE = "retire"


class RotateAction(StrEnum):
    """What a RotateCB button does."""

    MENU = "menu"
    CONFIRM = "confirm"


class RideAction(StrEnum):
    """What a RideCB button does."""

    EDIT = "edit"
    DELETE = "delete"


class MenuCB(CallbackData, prefix="menu"):
    """Callback payload for top-level menu navigation buttons."""

    action: MenuAction = Field(description="Menu screen to show")


class GroupCB(CallbackData, prefix="group"):
    """Callback payload for group list / group detail buttons."""

    action: GroupAction = Field(description="Group operation to perform")
    group_id: int = Field(default=0, description="Target groups.id; 0 when the action needs none")


class ChainCB(CallbackData, prefix="chain"):
    """Callback payload for chain detail buttons."""

    action: ChainAction = Field(description="Chain operation to perform")
    chain_id: int = Field(default=0, description="Target chains.id; 0 when the action needs none")
    group_id: int = Field(default=0, description="groups.id to return to after the action")


class RotateCB(CallbackData, prefix="rotate"):
    """Callback payload for the rotation menu and its confirm step."""

    action: RotateAction = Field(description="Rotation step: show the menu or confirm a choice")
    group_id: int = Field(description="groups.id the rotation happens in")
    chain_id: int = Field(default=0, description="chains.id to activate; 0 on the menu step")


class RideCB(CallbackData, prefix="ride"):
    """Callback payload for buttons under a ride in the recent-rides list."""

    action: RideAction = Field(description="Ride operation to perform")
    ride_id: int = Field(description="Target rides.id")
