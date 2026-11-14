from uuid import UUID

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.exc import SQLAlchemyError

from app.bot.keyboards import START_MENU
from app.services.maintenance import Maintenance


def privacy_router(maintenance: Maintenance) -> Router:
    router = Router(name="privacy")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)

    @router.message(Command("stats"))
    async def stats(message: Message) -> None:
        counts = await maintenance.stats(message.from_user.id)
        assignments = counts["assignments"]
        await message.answer(
            f"Completed tasks: {assignments.get('completed', 0)}\n"
            f"Skipped tasks: {assignments.get('skipped', 0)}\n"
            f"Voice takes: {sum(counts['recordings'].values())}"
        )

    @router.message(Command("delete_data"))
    async def confirm_deletion(message: Message) -> None:
        participant_id = await maintenance.participant_id(message.from_user.id)
        if participant_id is None:
            await message.answer("No profile data to delete.")
            return
        await message.answer(
            "Delete your profile, task history, and all voice takes from the bot? "
            "Contact the owner about existing backups and exports.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Delete my data", callback_data=f"delete:{participant_id.hex}")
            ]]),
        )

    @router.callback_query(F.data.regexp(r"^delete:[a-f0-9]{32}$"))
    async def delete(query: CallbackQuery, state: FSMContext) -> None:
        participant_id = await maintenance.participant_id(query.from_user.id)
        if participant_id != UUID(hex=query.data.removeprefix("delete:")):
            await query.answer("This request has expired.", show_alert=True)
            return
        await query.answer()
        try:
            await maintenance.delete_participant(participant_id)
        except (OSError, SQLAlchemyError):
            await query.message.answer("Deletion paused. Use /delete_data to retry.")
            return
        await state.clear()
        await query.message.answer("Your data was deleted.", reply_markup=START_MENU)

    return router
