from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.connection import create_database_engine, initialize_database
from app.database.models import (
    Assignment, CorpusText, Language, Recording, RecordingStatus, TelegramIdentity,
)
from app.services.management import Management
from app.services.participants import Participants
from app.storage.local import LocalStorage


class ManagementTest(IsolatedAsyncioTestCase):
    async def test_moderation_and_retryable_deletion_remove_all_takes(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        database = root / "test.sqlite"
        await initialize_database(database)
        engine = create_database_engine(database)
        self.addAsyncCleanup(engine.dispose)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = LocalStorage(root / "audio")
        management = Management(sessions, storage)
        participants = Participants(sessions)
        await participants.consent(7)

        async with sessions.begin() as session:
            identity = await session.get(TelegramIdentity, 7)
            language = Language(name="Russian", code="rus")
            session.add(language)
            await session.flush()
            prompt = CorpusText(text="Тест", language_id=language.language_id)
            session.add(prompt)
            await session.flush()
            task = Assignment(participant_id=identity.participant_id, text_id=prompt.text_id)
            session.add(task)
            await session.flush()
            old = Recording(
                assignment_id=task.assignment_id, storage_key="old.ogg", duration=1,
                language_id=language.language_id, status=RecordingStatus.SUPERSEDED,
            )
            latest = Recording(
                assignment_id=task.assignment_id, storage_key="latest.ogg", duration=2,
                language_id=language.language_id,
            )
            session.add_all([old, latest])
        for key in ("old.ogg", "latest.ogg"):
            with BytesIO(b"audio") as source:
                await storage.save(source, key)

        self.assertTrue(await management.moderate(latest.recording_id, RecordingStatus.ACCEPTED))
        with self.assertRaises(ValueError):
            await management.moderate(old.recording_id, RecordingStatus.ACCEPTED)
        self.assertEqual((await management.stats())["recording_statuses"], {
            "accepted": 1, "superseded": 1,
        })
        rows = await management.recordings(10)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("telegram_id" not in row for row in rows))

        delete = storage.delete
        deleted = []

        async def fail_second_delete(key):
            if deleted:
                raise OSError("Storage unavailable")
            await delete(key)
            deleted.append(key)

        with patch.object(storage, "delete", side_effect=fail_second_delete):
            with self.assertRaises(OSError):
                await management.delete_participant(identity.participant_id)
        self.assertEqual(len(await management.recordings(10)), 2)
        self.assertFalse(await participants.consent(7))

        self.assertTrue(await management.delete_participant(identity.participant_id))
        self.assertEqual(await management.recordings(10), [])
        self.assertEqual((await management.stats())["texts"], 1)
        async with sessions() as session:
            self.assertIsNone(await session.scalar(select(TelegramIdentity)))
        self.assertFalse(await storage.exists("old.ogg"))
        self.assertFalse(await storage.exists("latest.ogg"))
