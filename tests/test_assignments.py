import json
from asyncio import gather
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.connection import create_database_engine, initialize_database
from app.database.models import Assignment, AssignmentStatus, CorpusText, Language
from app.services.assignments import AssignmentOutcome, Assignments, ReadingTask
from app.services.corpus import Corpus
from app.services.participants import Participants


class AssignmentsTest(IsolatedAsyncioTestCase):
    async def test_import_and_issue_each_active_text_once_per_participant(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        path = root / "test.sqlite"
        await initialize_database(path, (("rus", "Russian"), ("eng", "English")))
        await initialize_database(path, (("rus", "Russian"),))
        engine = create_database_engine(path)
        self.addAsyncCleanup(engine.dispose)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        corpus = Corpus(sessions)
        participants = Participants(sessions)
        assignments = Assignments(sessions)
        source = root / "texts.jsonl"
        source.write_text(
            '{"text":" Первый текст ","dialect":"северный","difficulty":"easy","category":"быт"}\n'
            '\n{"text":"Второй текст","dialect":null}\n{"text":"Первый текст"}\n',
            encoding="utf-8-sig",
        )
        self.assertEqual(await corpus.import_texts("rus", source), (2, 1))
        self.assertEqual(await corpus.import_texts("rus", source), (0, 3))
        source.write_text(
            '{"text":"Новый текст"}\n' + json.dumps({"text": "а" * 4097}), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "Строка 2"):
            await corpus.import_texts("rus", source)

        for invalid in ('{broken', '[]', '{"text":null}', '{"text":"Текст","dialect":7}',
                        '{"text":"Текст","id":1}'):
            with self.subTest(invalid=invalid):
                source.write_text('{"text":"Новый текст"}\n' + invalid, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Строка 2"):
                    await corpus.import_texts("rus", source)

        source.write_text('{"text":"English prompt"}\n', encoding="utf-8")
        await corpus.import_texts("eng", source)
        async with sessions.begin() as session:
            language_id = await session.scalar(select(Language.language_id).where(Language.code == "rus"))
            imported = await session.scalar(
                select(CorpusText).where(CorpusText.text == "Первый текст")
            )
            self.assertEqual(
                (imported.dialect, imported.difficulty, imported.category),
                ("северный", "easy", "быт"),
            )
            session.add(CorpusText(text="Скрытый текст", language_id=language_id, is_active=False))

        self.assertEqual(await assignments.next(101), AssignmentOutcome.NEED_CONSENT)
        await participants.consent(101)
        self.assertEqual(await assignments.next(101), AssignmentOutcome.NEED_LANGUAGE)
        await participants.choose_language(101, language_id)
        self.assertEqual(await assignments.skip(101), AssignmentOutcome.NO_PENDING)
        first, retry = await gather(assignments.next(101), assignments.next(101))
        self.assertIsInstance(first, ReadingTask)
        self.assertEqual(first, retry)
        self.assertEqual(await assignments.skip(101), AssignmentOutcome.SKIPPED)
        second = await assignments.next(101)
        self.assertIsInstance(second, ReadingTask)
        self.assertEqual({first.text, second.text}, {"Первый текст", "Второй текст"})
        self.assertEqual(await assignments.skip(101), AssignmentOutcome.SKIPPED)
        self.assertEqual(await assignments.next(101), AssignmentOutcome.NO_TEXTS)

        await participants.consent(102)
        await participants.choose_language(102, language_id)
        self.assertIn(await assignments.next(102), (first, second))
        await participants.stop(102)
        self.assertEqual(await assignments.next(102), AssignmentOutcome.NEED_CONSENT)
        async with sessions() as session:
            skipped = select(func.count()).select_from(Assignment).where(
                Assignment.status == AssignmentStatus.SKIPPED
            )
            self.assertEqual(await session.scalar(skipped), 2)
            self.assertEqual(await session.scalar(select(func.count()).select_from(Assignment)), 3)
