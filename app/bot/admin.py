from uuid import UUID

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.services.abuse import AbuseProtection


def admin_router(abuse: AbuseProtection, admin_ids: frozenset[int]) -> Router:
    router = Router(name="admin")
    router.message.filter(F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(admin_ids))

    @router.message(Command("abuse_stats"))
    async def stats(message: Message) -> None:
        await message.answer(await abuse.stats())

    @router.message(Command("user_info", "ban", "unban", "exempt", "unexempt"))
    async def manage(message: Message, command: CommandObject) -> None:
        args = (command.args or "").split(maxsplit=1)
        try:
            participant_id = UUID(args[0]) if args else None
        except ValueError:
            participant_id = None
        action = command.command
        if participant_id is None:
            await message.answer(f"Usage: /{action} <participant UUID>")
            return
        if action == "user_info":
            await message.answer(await abuse.stats(participant_id))
            return
        reason = args[1].strip() if len(args) == 2 else ""
        if not 1 <= len(reason) <= 200:
            await message.answer(f"Usage: /{action} <participant UUID> <reason, up to 200 characters>")
            return
        changed = await abuse.change(participant_id, message.from_user.id, action, reason)
        await message.answer("Restriction updated." if changed else "Participant not found.")

    return router
