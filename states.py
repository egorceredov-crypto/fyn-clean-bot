from aiogram.fsm.state import State, StatesGroup

class CleaningOrder(StatesGroup):
    cleaning_type = State()
    flat_type = State()
    date = State()
    time = State()
    metro_station = State()
    address = State()
    phone = State()
    name = State()
    comment = State()
    photos = State()
    preview = State()
    edit_field = State()

class AdminStates(StatesGroup):
    cancel_reason = State()
    executor_edit = State()


class ExecutorStates(StatesGroup):
    registration_name = State()
    registration_phone = State()
    admin_add_id = State()
    admin_add_name = State()
    admin_add_phone = State()
    admin_add_bio = State()
    admin_edit_select = State()
    admin_edit_name = State()
    admin_edit_phone = State()
    admin_edit_bio = State()
    menu = State()

class RatingStates(StatesGroup):
    comment = State()
