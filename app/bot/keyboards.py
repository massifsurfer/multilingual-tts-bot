from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


HELP = "Help"
START = "Start participation"
NEXT = "Next text"
RERECORD = "Record another take"
SKIP = "Skip task"
SETTINGS = "Settings"
STOP = "Stop participation"
BACK = "Back"
KEEP_DIALECT = "Keep current dialect"
NO_DIALECT = "Continue without dialect"
CLEAR_DIALECT = "Clear dialect"

TASK_BUTTONS = {NEXT, RERECORD, SKIP, HELP}


def reply_keyboard(*rows: tuple[str, ...]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
    )


START_MENU = reply_keyboard((START, HELP))
TASK_MENU = reply_keyboard((NEXT, RERECORD), (SKIP, HELP), (SETTINGS,))
def dialect_menu(has_dialect: bool) -> ReplyKeyboardMarkup:
    if has_dialect:
        return reply_keyboard((KEEP_DIALECT,), (CLEAR_DIALECT, BACK))
    return reply_keyboard((NO_DIALECT, BACK))


SETTINGS_MENU = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text=label, callback_data=f"settings:{action}")]
    for label, action in (
        ("Language & dialect", "language"),
        ("Participation", "participation"),
        ("Stats", "stats"),
        ("Delete data", "delete"),
        (BACK, "home"),
    )
])
