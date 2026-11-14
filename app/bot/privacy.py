from uuid import UUID

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.exc import SQLAlchemyError

from app.bot.keyboards import BACK, SETTINGS_MENU, START_MENU, TASK_MENU
from app.services.maintenance import Maintenance
from app.services.participants import Participants


class DeletionFlow(StatesGroup):
    confirm = State()


def privacy_router(maintenance: Maintenance, participants: Participants) -> Router:
    router = Router(name="privacy")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)

    async def show_stats(message: Message, telegram_id: int, *, from_settings: bool = False) -> None:
        counts = await maintenance.stats(telegram_id)
        back = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=BACK, callback_data="settings:menu")
        ]]) if from_settings else None
        assignments = counts["assignments"]
        await message.answer(
            f"Completed tasks: {assignments.get('completed', 0)}\n"
            f"Skipped tasks: {assignments.get('skipped', 0)}\n"
            f"Voice takes: {sum(counts['recordings'].values())}",
            reply_markup=back,
        )

    @router.message(Command("stats"))
    async def stats(message: Message) -> None:
        await show_stats(message, message.from_user.id)

    async def ask_to_delete(
        message: Message, state: FSMContext, telegram_id: int, *, from_settings: bool = False,
    ) -> None:
        await state.clear()
        participant_id = await maintenance.participant_id(telegram_id)
        if participant_id is None:
            await message.answer("No profile data to delete.", reply_markup=START_MENU)
            return
        confirmation = await message.answer(
            "Delete your profile, task history, and all voice takes from the bot? "
            "Contact the owner about existing backups and exports.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Delete my data", callback_data=f"delete:{participant_id.hex}"),
                InlineKeyboardButton(text="Cancel", callback_data="delete:cancel"),
            ]]),
        )

        await state.set_data({"confirmation_id": confirmation.message_id, "from_settings": from_settings})
        await state.set_state(DeletionFlow.confirm)

    @router.message(Command("delete_data"))
    async def confirm_deletion(message: Message, state: FSMContext) -> None:
        await ask_to_delete(message, state, message.from_user.id)

    @router.callback_query(F.data.in_({"settings:stats", "settings:delete"}))
    async def privacy_option(query: CallbackQuery, state: FSMContext) -> None:
        if not isinstance(query.message, Message):
            await query.answer()
            return
        telegram_id = query.from_user.id
        if not await participants.is_active(telegram_id):
            await query.answer("Use /start to join.", show_alert=True)
            return
        await state.clear()
        await query.answer()
        if query.data == "settings:stats":
            await show_stats(query.message, telegram_id, from_settings=True)
        else:
            await ask_to_delete(query.message, state, telegram_id, from_settings=True)

    @router.callback_query(F.data.regexp(r"^delete:(cancel|[a-f0-9]{32})$"))
    async def delete(query: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        if (
            not isinstance(query.message, Message)
            or await state.get_state() != DeletionFlow.confirm.state
            or data.get("confirmation_id") != query.message.message_id
        ):
            await query.answer("This request has expired.", show_alert=True)
            return

        if query.data == "delete:cancel":
            await state.clear()
            await query.answer()
            active = await participants.is_active(query.from_user.id)
            if active and data.get("from_settings"):
                await query.message.answer("Deletion cancelled.", reply_markup=SETTINGS_MENU)
            else:
                await query.message.answer("Deletion cancelled.", reply_markup=TASK_MENU if active else START_MENU)
            return

        participant_id = await maintenance.participant_id(query.from_user.id)
        if participant_id != UUID(hex=query.data.removeprefix("delete:")):
            await query.answer("This request has expired.", show_alert=True)
            return
        await state.clear()
        await query.answer()
        try:
            await maintenance.delete_participant(participant_id)
        except (OSError, SQLAlchemyError):
            await query.message.answer("Deletion paused. Use /delete_data to retry.", reply_markup=START_MENU)
            return
        await query.message.answer("Your data was deleted.", reply_markup=START_MENU)

    return router
