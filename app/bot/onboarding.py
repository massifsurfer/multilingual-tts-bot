from hashlib import sha256

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards import (
    CLEAR_DIALECT, DIALECT_MENU, DONE, SETTINGS, START, START_MENU, STOP, TASK_BUTTONS, TASK_MENU,
)
from app.services.participants import Participants


class SettingsFlow(StatesGroup):
    dialect = State()


def keyboard(*buttons: InlineKeyboardButton) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button] for button in buttons])


def onboarding_router(participants: Participants, consent_text: str) -> Router:
    router = Router(name="onboarding")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    consent_callback = "consent:" + sha256(consent_text.encode()).hexdigest()[:32]

    async def show_languages(message: Message, telegram_id: int, *, settings: bool = False) -> None:
        languages = await participants.languages()
        if not languages and not settings:
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
        if settings:
            buttons.append(InlineKeyboardButton(text=STOP, callback_data="settings:stop"))
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
        await message.answer(
            consent_text,
            reply_markup=keyboard(InlineKeyboardButton(text="I agree to participate", callback_data=consent_callback)),
        )

    @router.callback_query(F.data.startswith("consent:"))
    async def accept(query: CallbackQuery, state: FSMContext) -> None:
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

    @router.message(Command("settings"))
    @router.message(F.text == SETTINGS)
    async def settings(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not await participants.is_active(message.from_user.id):
            await message.answer("Use /start to review the terms and join.", reply_markup=START_MENU)
            return
        await show_languages(message, message.from_user.id, settings=True)

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
            await query.message.answer(
                "Enter a dialect or choose Done.",
                reply_markup=DIALECT_MENU,
            )

    @router.callback_query(F.data == "settings:stop")
    async def stop_from_settings(query: CallbackQuery, state: FSMContext) -> None:
        await participants.stop(query.from_user.id)
        await state.clear()
        await query.answer("Participation paused.")
        if isinstance(query.message, Message):
            await query.message.answer("Participation paused.", reply_markup=START_MENU)

    @router.message(Command("stop"))
    @router.message(F.text == STOP)
    async def stop(message: Message, state: FSMContext) -> None:
        await participants.stop(message.from_user.id)
        await state.clear()
        await message.answer("Participation paused.", reply_markup=START_MENU)

    @router.message(SettingsFlow.dialect, Command("done"))
    @router.message(SettingsFlow.dialect, F.text == DONE)
    async def done(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Settings saved.", reply_markup=TASK_MENU)

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
