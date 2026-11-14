from aiogram import F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards import HELP, NEXT, RERECORD, SKIP, START_MENU, TASK_MENU
from app.services.abuse import AbuseProtection
from app.services.assignments import AssignmentOutcome, Assignments, ReadingTask


RESPONSES = {
    AssignmentOutcome.NEED_CONSENT: "Use /start to review the terms and join.",
    AssignmentOutcome.NEED_LANGUAGE: "Choose a language in Settings.",
    AssignmentOutcome.NO_TEXTS: "No new texts for this language.",
    AssignmentOutcome.NO_PENDING: "No open task. Choose Next text.",
    AssignmentOutcome.TASK_OPEN: "Record or skip the open task first.",
    AssignmentOutcome.SKIPPED: "Task skipped.",
}


def tasks_router(assignments: Assignments, abuse: AbuseProtection) -> Router:
    router = Router(name="tasks")
    router.message.filter(F.chat.type == ChatType.PRIVATE)

    async def answer_task(message: Message, result: ReadingTask | AssignmentOutcome) -> None:
        if result == AssignmentOutcome.BLOCKED:
            reason = await abuse.ban_reason(message.from_user.id)
            await message.answer(reason or "Participation is restricted.", reply_markup=TASK_MENU)
            return
        menu = START_MENU if result == AssignmentOutcome.NEED_CONSENT else TASK_MENU
        await message.answer(
            result.text if isinstance(result, ReadingTask) else RESPONSES[result],
            reply_markup=menu,
        )

    @router.message(Command("help"))
    @router.message(F.text == HELP)
    async def help_message(message: Message) -> None:
        await message.answer(
            "<b>Quick guide</b>\n"
            "• <b>Start participation</b> — Review and accept the terms.\n"
            "• <b>Language</b> — Choose what to read.\n"
            "• <b>Next text</b> — Get a prompt and read it in a voice message.\n"
            "• <b>Record another take</b> — Redo your last recording.\n"
            "• <b>Skip task</b> — Skip a prompt or cancel a retake.\n"
            "• <b>Settings</b> — Manage language, dialect, and participation.\n"
            "• <b>Keep current dialect</b> — Keep your saved dialect.\n"
            "• <b>Clear dialect</b> — Remove your saved dialect.\n"
            "• <b>Stop participation</b> — Open Settings → Participation to pause.\n"
            "• <b>Stats</b> (/stats) — See your progress.\n"
            "• <b>Delete data</b> (/delete_data) — Request deletion.",
            parse_mode=ParseMode.HTML,
        )

    @router.message(Command("next"))
    @router.message(F.text == NEXT)
    async def next_task(message: Message, state: FSMContext) -> None:
        await state.clear()
        result = await assignments.next(message.from_user.id)
        await answer_task(message, result)

    @router.message(Command("rerecord"))
    @router.message(F.text == RERECORD)
    async def rerecord_task(message: Message, state: FSMContext) -> None:
        await state.clear()
        result = await assignments.rerecord(message.from_user.id)
        if isinstance(result, ReadingTask):
            await message.answer("Send another take.")
        await answer_task(message, result)

    @router.message(Command("skip"))
    @router.message(F.text == SKIP)
    async def skip_task(message: Message, state: FSMContext) -> None:
        await state.clear()
        result = await assignments.skip(message.from_user.id)
        await answer_task(message, result)

    return router
