from asyncio import gather
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.connection import create_database_engine, initialize_database
from app.database.models import Language, Participant, TelegramIdentity
from app.services.participants import Participants


class ParticipantsTest(IsolatedAsyncioTestCase):
    async def test_consent_language_switch_and_stop(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "test.sqlite"
        await initialize_database(path)
        engine = create_database_engine(path)
        self.addAsyncCleanup(engine.dispose)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        service = Participants(sessions)
        telegram_id = 5_000_000_000

        async with sessions.begin() as session:
            russian = Language(name="Русский", code="rus")
            armenian = Language(name="Армянский", code="hye")
            inactive = Language(name="English", code="eng", is_active=False)
            session.add_all([russian, armenian, inactive])

        self.assertFalse(await service.is_active(telegram_id))
        self.assertFalse(await service.choose_language(telegram_id, russian.language_id))
        self.assertEqual(
            {language.code for language in await service.languages()}, {"rus", "hye"}
        )
        await gather(service.consent(telegram_id), service.consent(telegram_id))
        async with sessions() as session:
            identity = await session.get(TelegramIdentity, telegram_id)
            self.assertEqual(await session.scalar(select(func.count()).select_from(Participant)), 1)

        self.assertTrue(await service.is_active(telegram_id))
        self.assertFalse(await service.choose_language(telegram_id, inactive.language_id))
        self.assertTrue(await service.choose_language(telegram_id, russian.language_id))
        self.assertTrue(await service.set_dialect(telegram_id, russian.language_id, "Северный"))
        self.assertTrue(await service.choose_language(telegram_id, armenian.language_id))
        self.assertFalse(await service.set_dialect(telegram_id, russian.language_id, "Устаревший"))
        self.assertTrue(await service.choose_language(telegram_id, russian.language_id))
        self.assertEqual((await service.active_language(telegram_id)).dialect, "Северный")

        await service.stop(telegram_id)
        self.assertFalse(await service.is_active(telegram_id))
        self.assertFalse(await service.set_dialect(telegram_id, russian.language_id, None))
        await service.consent(telegram_id)
        self.assertTrue(await service.is_active(telegram_id))
        self.assertTrue(await service.set_dialect(telegram_id, russian.language_id, None))
        self.assertIsNone((await service.active_language(telegram_id)).dialect)
        async with sessions() as session:
            resumed = await session.get(TelegramIdentity, telegram_id)
            self.assertEqual(resumed.participant_id, identity.participant_id)
