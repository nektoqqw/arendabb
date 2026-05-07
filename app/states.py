from aiogram.fsm.state import State, StatesGroup


class RentalBookingState(StatesGroup):
    choosing_start = State()
    choosing_end = State()
    waiting_phone = State()
    confirming = State()


class ProfileState(StatesGroup):
    waiting_phone = State()


class RegistrationState(StatesGroup):
    waiting_name = State()
    waiting_phone = State()


class AdminAddCarState(StatesGroup):
    waiting_name = State()
    waiting_description = State()
    waiting_price = State()
    waiting_engine = State()
    waiting_transmission = State()
    waiting_seats = State()
    waiting_has_ac = State()
    waiting_is_available = State()
    waiting_photo = State()


class AdminEditCarState(StatesGroup):
    waiting_name = State()
    waiting_description = State()
    waiting_price = State()
    waiting_engine = State()
    waiting_transmission = State()
    waiting_seats = State()
    waiting_has_ac = State()
    waiting_is_available = State()
    waiting_photo = State()


class AdminBlackoutState(StatesGroup):
    waiting_range = State()
