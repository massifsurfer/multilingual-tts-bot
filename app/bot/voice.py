import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, Update
from sqlalchemy.exc import SQLAlchemyError

from app.database.models import AbuseEventType
from app.services.abuse import AbuseProtection
from app.services.recordings import Recordings, VoiceOutcome


def voice_router(recordings: Recordings, abuse: AbuseProtection) -> Router:
    router = Router(name="voice")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    uploads = asyncio.Semaphore(2)

    @router.message(F.voice)
    async def receive(message: Message, bot: Bot, event_update: Update) -> None:
        voice = message.voice

        async def download(destination):
            async with asyncio.timeout(30):
                return await bot.download(voice, destination=destination, timeout=25)

        try:
            rejection = await abuse.check_voice(
                message.from_user.id, event_update.update_id, voice.duration, voice.file_size, voice.mime_type
            )
            if rejection:
                await message.answer(rejection)
                return
            async with uploads:
                outcome = await recordings.submit(
                    message.from_user.id, event_update.update_id, voice.duration, voice.file_size,
                    download, file_unique_id=voice.file_unique_id,
                )
            violation = {
                VoiceOutcome.INVALID: AbuseEventType.INVALID_AUDIO,
                VoiceOutcome.TOO_LARGE: AbuseEventType.AUDIO_TOO_LARGE,
                VoiceOutcome.TOO_LONG: AbuseEventType.AUDIO_TOO_LONG,
                VoiceOutcome.DUPLICATE: AbuseEventType.SPAM_DETECTED,
            }.get(outcome)
            if violation is not None:
                rejection = await abuse.reject_download(event_update.update_id, violation)
                if rejection:
                    await message.answer(rejection)
                    return
        except (TelegramAPIError, TimeoutError, OSError, SQLAlchemyError) as exc:
            logging.getLogger(__name__).warning("Voice upload failed (%s)", type(exc).__name__)
            await message.answer("Could not save it. Please resend.")
            return

        if outcome == VoiceOutcome.BLOCKED:
            reason = await abuse.ban_reason(message.from_user.id)
            await message.answer(reason or "Participation was restricted during upload.")
            return
        await message.answer({
            VoiceOutcome.TOO_LONG: "Voice message too long.",
            VoiceOutcome.DUPLICATE: "Record a new take.",
            VoiceOutcome.SAVED: "Recording saved.",
            VoiceOutcome.NO_TASK: "Choose Next text first.",
            VoiceOutcome.TOO_LARGE: "Voice message too large.",
            VoiceOutcome.INVALID: "Send a short Telegram voice message.",
        }[outcome])

    return router
