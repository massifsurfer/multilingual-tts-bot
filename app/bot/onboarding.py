from hashlib import sha256

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards import (
    BACK, CLEAR_DIALECT, KEEP_DIALECT, NO_DIALECT, SETTINGS, SETTINGS_MENU, START, START_MENU, STOP,
    TASK_BUTTONS, TASK_MENU, dialect_menu,
)
from app.services.participants import Participants


class SettingsFlow(StatesGroup):
    consent = State()
    dialect = State()
    confirm_pause = State()


def keyboard(*buttons: InlineKeyboardButton) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button] for button in buttons])


def onboarding_router(participants: Participants, consent_text: str) -> Router:
    router = Router(name="onboarding")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    consent_callback = "consent:" + sha256(consent_text.encode()).hexdigest()[:32]

    async def show_languages(message: Message, telegram_id: int, *, from_settings: bool = False) -> None:
        languages = await participants.languages()
        if not languages and not from_settings:
            await message.answer("No languages available yet.", reply_markup=TASK_MENU)
            return
        selected = await participants.active_language(telegram_id)
        buttons = [
            InlineKeyboardButton(
                text=f"✓ {language.name}" if selected and selected.language_id == language.language_id
                else language.name,
                callback_data=f"lang:{language.language_id}",
            )
            for language in languages
        ]
        back = "settings:menu" if from_settings else "settings:home"
        buttons.append(InlineKeyboardButton(text=BACK, callback_data=back))
        prompt = "Choose your main language." if languages else "No languages available."
        await message.answer(prompt, reply_markup=keyboard(*buttons))

    @router.message(CommandStart())
    @router.message(F.text == START)
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        telegram_id = message.from_user.id
        if await participants.is_active(telegram_id):
            if await participants.active_language(telegram_id) is None:
                await show_languages(message, telegram_id)
            else:
                await message.answer("Choose an action.", reply_markup=TASK_MENU)
            return
        confirmation = await message.answer(
            consent_text,
            reply_markup=keyboard(
                InlineKeyboardButton(text="I agree to participate", callback_data=consent_callback),
                InlineKeyboardButton(text="Cancel", callback_data="consent:cancel"),
            ),
        )
        await state.set_data({"confirmation_id": confirmation.message_id})
        await state.set_state(SettingsFlow.consent)

    @router.callback_query(F.data.startswith("consent:"))
    async def accept(query: CallbackQuery, state: FSMContext) -> None:
        if (
            not isinstance(query.message, Message)
            or await state.get_state() != SettingsFlow.consent.state
            or (await state.get_data()).get("confirmation_id") != query.message.message_id
        ):
            await query.answer("This confirmation has expired. Use /start.", show_alert=True)
            return
        if query.data == "consent:cancel":
            await state.clear()
            await query.answer()
            await query.message.answer("Choose an action.", reply_markup=START_MENU)
            return
        if query.data != consent_callback:
            await query.answer("Terms changed. Use /start.", show_alert=True)
            return
        if not await participants.consent(query.from_user.id):
            await query.answer("Data deletion is in progress.", show_alert=True)
            return
        await state.clear()
        await query.answer("Consent saved.")
        if isinstance(query.message, Message):
            await show_languages(query.message, query.from_user.id)

    async def show_settings(message: Message) -> None:
        await message.answer("Settings", reply_markup=SETTINGS_MENU)

    @router.message(Command("settings"))
    @router.message(F.text == SETTINGS)
    async def settings(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not await participants.is_active(message.from_user.id):
            await message.answer("Use /start to review the terms and join.", reply_markup=START_MENU)
            return
        await show_settings(message)

    async def show_participation(message: Message) -> None:
        await message.answer(
            "Participation",
            reply_markup=keyboard(
                InlineKeyboardButton(text=STOP, callback_data="settings:stop"),
                InlineKeyboardButton(text=BACK, callback_data="settings:menu"),
            ),
        )

    @router.callback_query(F.data.in_({"settings:home", "settings:menu", "settings:language", "settings:participation"}))
    async def settings_section(query: CallbackQuery, state: FSMContext) -> None:
        if not isinstance(query.message, Message):
            await query.answer()
            return
        if not await participants.is_active(query.from_user.id):
            await query.answer("Use /start to join.", show_alert=True)
            return
        await state.clear()
        await query.answer()
        if query.data == "settings:language":
            await show_languages(query.message, query.from_user.id, from_settings=True)
        elif query.data == "settings:participation":
            await show_participation(query.message)
        elif query.data == "settings:home":
            await query.message.answer("Choose an action.", reply_markup=TASK_MENU)
        else:
            await show_settings(query.message)

    @router.callback_query(F.data.regexp(r"^lang:[1-9][0-9]{0,17}$"))
    async def choose_language(query: CallbackQuery, state: FSMContext) -> None:
        language_id = int(query.data.removeprefix("lang:"))
        if not await participants.choose_language(query.from_user.id, language_id):
            await query.answer("Language unavailable. Open Settings.", show_alert=True)
            return
        await state.set_data({"language_id": language_id})
        await state.set_state(SettingsFlow.dialect)
        await query.answer("Language saved.")
        if isinstance(query.message, Message):
            selected = await participants.active_language(query.from_user.id)
            saved_dialect = selected.dialect if selected else None
            await query.message.answer(
                f"Current dialect: {saved_dialect or 'Not set'}\nEnter a new dialect or choose an option.",
                reply_markup=dialect_menu(bool(saved_dialect)),
            )

    async def ask_to_pause(message: Message, state: FSMContext, *, from_settings: bool) -> None:
        confirmation = await message.answer(
            "Pause participation? Your recordings will be kept.",
            reply_markup=keyboard(
                InlineKeyboardButton(text="Pause participation", callback_data="pause:confirm"),
                InlineKeyboardButton(text="Cancel", callback_data="pause:cancel"),
            ),
        )
        await state.set_data({"confirmation_id": confirmation.message_id, "from_settings": from_settings})
        await state.set_state(SettingsFlow.confirm_pause)

    @router.callback_query(F.data == "settings:stop")
    async def stop_from_settings(query: CallbackQuery, state: FSMContext) -> None:
        if not isinstance(query.message, Message):
            await query.answer()
            return
        if not await participants.is_active(query.from_user.id):
            await query.answer("Participation is already paused.", show_alert=True)
            return
        await query.answer()
        await ask_to_pause(query.message, state, from_settings=True)

    @router.message(Command("stop"))
    @router.message(F.text == STOP)
    async def stop(message: Message, state: FSMContext) -> None:
        if not await participants.is_active(message.from_user.id):
            await message.answer("Use /start to join.", reply_markup=START_MENU)
            return
        await ask_to_pause(message, state, from_settings=False)

    @router.callback_query(F.data.in_({"pause:confirm", "pause:cancel"}))
    async def resolve_pause(query: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        confirmation_id = data.get("confirmation_id")
        if (
            not isinstance(query.message, Message)
            or await state.get_state() != SettingsFlow.confirm_pause.state
            or confirmation_id != query.message.message_id
        ):
            await query.answer("This confirmation has expired.", show_alert=True)
            return
        if query.data == "pause:confirm":
            await participants.stop(query.from_user.id)
            await state.clear()
            await query.answer()
            await query.message.answer("Participation paused.", reply_markup=START_MENU)
        else:
            await state.clear()
            await query.answer()
            if data["from_settings"]:
                await show_participation(query.message)
            else:
                await query.message.answer("Choose an action.", reply_markup=TASK_MENU)

    @router.message(SettingsFlow.dialect, Command("done"))
    @router.message(SettingsFlow.dialect, F.text.in_({KEEP_DIALECT, NO_DIALECT, "Done"}))
    async def done(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Settings saved.", reply_markup=TASK_MENU)

    @router.message(SettingsFlow.dialect, F.text == BACK)
    async def back_from_dialect(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Choose an action below or select a language.", reply_markup=TASK_MENU)
        await show_languages(message, message.from_user.id, from_settings=True)

    @router.message(SettingsFlow.dialect, Command("clear_dialect"))
    @router.message(SettingsFlow.dialect, F.text == CLEAR_DIALECT)
    @router.message(SettingsFlow.dialect, F.text, ~F.text.startswith("/"), ~F.text.in_(TASK_BUTTONS))
    async def dialect(message: Message, state: FSMContext) -> None:
        value = message.text.strip()
        if message.text == CLEAR_DIALECT or value.startswith("/"):
            value = None
        elif not value or len(value) > 100:
            await message.answer("Enter a dialect (1–100 characters).")
            return
        data = await state.get_data()
        saved = await participants.set_dialect(message.from_user.id, data["language_id"], value)
        await state.clear()
        await message.answer(
            "Settings saved." if saved else "Selection expired. Open Settings.",
            reply_markup=TASK_MENU,
        )

    return router
