from aiogram.fsm.state import State, StatesGroup

class FeedbackForm(StatesGroup):
    message = State()
    confirm = State()

class CargoForm(StatesGroup):
    from_city = State()
    to_city = State()
    cargo_type = State()
    weight = State()
    price = State()
    load_date = State()
    load_time = State()
    comment = State()
    confirm = State()

class CarrierRegister(StatesGroup):
    phone = State()
    confirm = State()

class SearchCargo(StatesGroup):
    from_city = State()
    to_city = State()

class SubscribeRoute(StatesGroup):
    from_city = State()
    to_city = State()

class RateForm(StatesGroup):
    score = State()
    comment = State()

class ProfileEdit(StatesGroup):
    phone = State()
    company = State()

class ChatForm(StatesGroup):
    message = State()

class VerifyForm(StatesGroup):
    phone = State()
    code = State()

class ReportForm(StatesGroup):
    user_id = State()
    report_type = State()
    description = State()

class EditCargo(StatesGroup):
    price = State()
    date = State()
    time = State()
    comment = State()


class Onboarding(StatesGroup):
    role = State()
    contact = State()
    legal_type = State()
    inn = State()
    company = State()

class LegalVerification(StatesGroup):
    inn = State()
    ogrn = State()
    director = State()
    doc = State()


class ClaimForm(StatesGroup):
    claim_type = State()
    title = State()
    description = State()
    amount = State()


class LegalCheck(StatesGroup):
    inn = State()
