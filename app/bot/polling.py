from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import SimpleEventIsolation
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.admin import admin_router
from app.bot.onboarding import onboarding_router
from app.bot.privacy import privacy_router
from app.bot.tasks import tasks_router
from app.bot.voice import voice_router
from app.config.settings import Settings
from app.database.connection import create_database_engine, initialize_database
from app.services.abuse import AbuseProtection
from app.services.assignments import Assignments
from app.services.maintenance import Maintenance
from app.services.participants import Participants
from app.services.recordings import Recordings
from app.storage.local import LocalStorage


async def run_bot(settings: Settings, token: str, consent_text: str) -> None:
    await initialize_database(settings.database_path, settings.languages)
    engine = create_database_engine(settings.database_path)
    try:
        dispatcher = Dispatcher(events_isolation=SimpleEventIsolation())
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        abuse = AbuseProtection(sessions, settings)
        storage = LocalStorage(settings.local_storage_path, min_free_bytes=settings.min_audio_free_bytes)
        dispatcher.include_router(privacy_router(Maintenance(sessions, storage)))
        dispatcher.include_router(admin_router(abuse, settings.admin_telegram_ids))
        dispatcher.include_router(onboarding_router(Participants(sessions), consent_text))
        dispatcher.include_router(tasks_router(Assignments(sessions), abuse))
        dispatcher.include_router(voice_router(Recordings(
            sessions, storage,
            max_bytes=settings.max_voice_bytes, max_seconds=settings.max_voice_seconds,
        ), abuse))
        async with Bot(token) as bot:
            await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        await engine.dispose()
