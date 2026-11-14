from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


HELP = "Help"
START = "Start participation"
NEXT = "Next text"
RERECORD = "Record another take"
SKIP = "Skip task"
SETTINGS = "Settings"
STOP = "Stop participation"
DONE = "Done"
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
DIALECT_MENU = reply_keyboard((DONE, CLEAR_DIALECT), (SETTINGS,))
