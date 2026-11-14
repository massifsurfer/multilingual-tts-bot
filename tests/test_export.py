import csv
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.connection import create_database_engine, initialize_database
from app.database.models import (
    Assignment, CorpusText, Language, Participant, ParticipantDeletion, Recording,
    RecordingStatus, TelegramIdentity,
)
from app.services.export import DatasetExport
from app.storage.local import LocalStorage


class ExportTest(IsolatedAsyncioTestCase):
    async def test_accepted_export_and_filtered_history(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        database = root / "test.sqlite"
        await initialize_database(database)
        engine = create_database_engine(database)
        self.addAsyncCleanup(engine.dispose)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = LocalStorage(root / "originals")
        export = DatasetExport(sessions, storage)

        async with sessions.begin() as session:
            speaker, deleting = Participant(), Participant()
            russian = Language(name="Russian", code="rus")
            english = Language(name="English", code="eng")
            session.add_all([speaker, deleting, russian, english])
            await session.flush()
            session.add_all([
                TelegramIdentity(telegram_id=9876543210, participant_id=speaker.participant_id),
                ParticipantDeletion(participant_id=deleting.participant_id),
            ])
            for owner, language, prompt, statuses in (
                (speaker, russian, 'Привет, "мир"!', ["accepted", "superseded", "rejected"]),
                (speaker, english, "Other language", ["current"]),
                (deleting, russian, "Pending deletion", ["accepted"]),
            ):
                sentence = CorpusText(text=prompt, language_id=language.language_id)
                session.add(sentence)
                await session.flush()
                assignment = Assignment(participant_id=owner.participant_id, text_id=sentence.text_id)
                session.add(assignment)
                await session.flush()
                for status in statuses:
                    key = f"{assignment.assignment_id}-{status}"
                    await storage.save(BytesIO(status.encode()), key)
                    session.add(Recording(
                        assignment_id=assignment.assignment_id, storage_key=key,
                        duration=1.5, language_id=language.language_id, dialect="Test",
                        status=RecordingStatus(status),
                    ))

        target = root / "default"
        self.assertEqual(await export.run(target, format="jsonl", language_code=None, all_takes=False), 1)
        raw = (target / "metadata.jsonl").read_text()
        item = json.loads(raw)
        self.assertEqual(item["speaker_id"], str(speaker.speaker_id))
        self.assertEqual(item["text"], 'Привет, "мир"!')
        self.assertEqual(item["language_code"], "rus")
        self.assertNotIn("9876543210", raw)
        self.assertNotIn(str(speaker.participant_id), raw)
        self.assertEqual((target / item["audio"]).read_bytes(), b"accepted")

        history = root / "history"
        self.assertEqual(await export.run(history, format="csv", language_code="RUS", all_takes=True), 3)
        with (history / "metadata.csv").open(newline="") as manifest:
            rows = list(csv.DictReader(manifest))
        self.assertEqual({row["status"] for row in rows}, {"accepted", "superseded", "rejected"})
        for row in rows:
            self.assertEqual(row["speaker_id"], item["speaker_id"])
            self.assertEqual(row["text"], item["text"])
            self.assertEqual((history / row["audio"]).read_bytes(), row["status"].encode())

        with self.assertRaises(FileExistsError):
            await export.run(target, format="csv", language_code=None, all_takes=True)
        for path in (root / "originals").glob("*accepted"):
            await storage.delete(path.name)
        failed = root / "missing-audio"
        with self.assertRaises(FileNotFoundError):
            await export.run(failed, format="jsonl", language_code=None, all_takes=False)
        self.assertFalse(failed.exists())
        self.assertEqual(list(root.glob(".dataset-*")), [])
