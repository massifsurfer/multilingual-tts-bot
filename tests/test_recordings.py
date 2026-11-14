from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.connection import create_database_engine, initialize_database
from app.database.models import Assignment, AssignmentStatus, CorpusText, Language, Recording, RecordingStatus
from app.services.assignments import AssignmentOutcome, Assignments
from app.services.participants import Participants
from app.services.recordings import Recordings, VoiceOutcome
from app.storage.local import LocalStorage


class RecordingsTest(IsolatedAsyncioTestCase):
    async def test_voice_validation_rollback_and_successful_retry(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        path = root / "test.sqlite"
        await initialize_database(path)
        engine = create_database_engine(path)
        self.addAsyncCleanup(engine.dispose)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async with sessions.begin() as session:
            language = Language(name="Русский", code="rus")
            session.add(language)
            await session.flush()
            session.add(CorpusText(text="Текст для проверки", language_id=language.language_id))

        participants = Participants(sessions)
        storage = LocalStorage(root / "audio")
        validator = patch("app.services.recordings.decoded_duration", return_value=1.0)
        decode = validator.start()
        self.addCleanup(validator.stop)
        recordings = Recordings(sessions, storage, max_bytes=80, max_seconds=120)
        payload = b"OggS\x00\x02" + bytes(20) + b"\x01\x13OpusHead" + bytes(11)

        async def download(destination):
            destination.write(payload)

        async def oversized(destination):
            destination.write(bytes(81))

        self.assertEqual(await recordings.submit(1, 100, 1, None, download), VoiceOutcome.NO_TASK)
        await participants.consent(1)
        await participants.choose_language(1, language.language_id)
        await participants.set_dialect(1, language.language_id, "Тестовый")
        assignments = Assignments(sessions)
        prompt = await assignments.next(1)
        self.assertEqual(await assignments.rerecord(1), AssignmentOutcome.TASK_OPEN)
        self.assertEqual(
            await recordings.submit(1, 100, 1, None, oversized), VoiceOutcome.TOO_LARGE
        )
        async def invalid(destination):
            destination.write(b"not audio")

        decode.return_value = None
        self.assertEqual(await recordings.submit(1, 100, 1, None, invalid), VoiceOutcome.INVALID)
        decode.return_value = 1.0

        with patch.object(storage, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await recordings.submit(1, 100, 1, None, download)
        async with sessions() as session:
            self.assertEqual(await session.scalar(select(Assignment.status)), AssignmentStatus.PENDING)
            self.assertIsNone(await session.scalar(select(Recording)))

        self.assertEqual(await recordings.submit(1, 100, 1, None, download, file_unique_id="first"), VoiceOutcome.SAVED)
        self.assertEqual(await recordings.submit(1, 100, 1, None, oversized), VoiceOutcome.SAVED)
        async with sessions() as session:
            recording = (await session.scalars(select(Recording))).one()
            self.assertEqual((recording.duration, recording.dialect), (1, "Тестовый"))
            self.assertEqual(await session.scalar(select(Assignment.status)), AssignmentStatus.COMPLETED)
        with await storage.get(recording.storage_key) as source:
            self.assertEqual(source.read(), payload)

        self.assertEqual(await assignments.rerecord(1), prompt)
        self.assertEqual(await assignments.skip(1), AssignmentOutcome.SKIPPED)
        self.assertEqual(await assignments.next(1), AssignmentOutcome.NO_TEXTS)
        self.assertEqual(await assignments.rerecord(1), prompt)

        self.assertEqual(
            await recordings.submit(1, 999, 1, None, oversized, file_unique_id="first"),
            VoiceOutcome.DUPLICATE,
        )
        decode.return_value = 2.0
        with patch.object(storage, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await recordings.submit(1, 101, 2, None, download)
        async with sessions() as session:
            self.assertEqual(await session.scalar(select(Recording.status)), RecordingStatus.CURRENT)

        self.assertEqual(await recordings.submit(1, 101, 2, None, download), VoiceOutcome.SAVED)
        self.assertEqual(await recordings.submit(1, 101, 2, None, oversized), VoiceOutcome.SAVED)
        async with sessions() as session:
            takes = list(await session.scalars(select(Recording).order_by(Recording.duration)))
        self.assertEqual([take.status for take in takes], [
            RecordingStatus.SUPERSEDED, RecordingStatus.CURRENT,
        ])
        self.assertEqual({take.assignment_id for take in takes}, {recording.assignment_id})
        self.assertEqual(len({take.storage_key for take in takes}), 2)
        for take in takes:
            self.assertEqual(
                take.storage_key, f"rus/{take.recorded_at:%Y/%m}/{take.recording_id}.ogg"
            )
            with await storage.get(take.storage_key) as source:
                self.assertEqual(source.read(), payload)
