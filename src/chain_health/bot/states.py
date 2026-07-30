from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    group_name = State()
    chain_name = State()


class AddGroup(StatesGroup):
    name = State()


class AddChain(StatesGroup):
    name = State()


class EditGroupLimit(StatesGroup):
    value = State()


class EditChainLimit(StatesGroup):
    value = State()


class EditChainResource(StatesGroup):
    value = State()


class EditRideAmount(StatesGroup):
    value = State()
