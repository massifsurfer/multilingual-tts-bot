from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.connection import create_database_engine, initialize_database
from app.database.models import (
    AbuseEvent,
    AbuseEventType,
    Assignment,
    CorpusText,
    Language,
    Participant,
    ParticipantLanguage,
    ParticipantRestriction,
    Recording,
    RecordingStatus,
    TelegramIdentity,
)


class DatabaseTest(IsolatedAsyncioTestCase):
    async def test_preserves_recording_history_and_cascades_participant_deletion(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "test.sqlite"
            await initialize_database(path)
            engine = create_database_engine(path)
            self.addAsyncCleanup(engine.dispose)
            sessions = async_sessionmaker(engine, expire_on_commit=False)

            async with sessions.begin() as session:
                participant = Participant()
                language = Language(name="Русский", code="rus")
                session.add_all([participant, language])
                await session.flush()
                corpus_text = CorpusText(text="Сегодня хорошая погода", language_id=language.language_id)
                session.add(corpus_text)
                await session.flush()
                assignment = Assignment(
                    participant_id=participant.participant_id, text_id=corpus_text.text_id
                )
                session.add_all([
                    assignment,
                    TelegramIdentity(telegram_id=5_000_000_000, participant_id=participant.participant_id),
                    ParticipantLanguage(
                        participant_id=participant.participant_id, language_id=language.language_id
                    ),
                    ParticipantRestriction(participant_id=participant.participant_id),
                    AbuseEvent(
                        participant_id=participant.participant_id, event_type=AbuseEventType.INVALID_AUDIO
                    ),
                ])
                await session.flush()
                recording = Recording(
                    assignment_id=assignment.assignment_id,
                    storage_key="rus/original.ogg",
                    duration=2.5,
                    language_id=language.language_id,
                )
                session.add(recording)

            self.assertFalse(participant.is_active)
            self.assertIsNone(participant.consent_at)
            self.assertNotEqual(participant.participant_id, participant.speaker_id)
            await initialize_database(path)

            async with sessions.begin() as session:
                self.assertEqual(await session.scalar(select(func.count()).select_from(Recording)), 1)
                self.assertEqual(
                    await session.scalar(select(TelegramIdentity.telegram_id)), 5_000_000_000
                )

            with self.assertRaises(IntegrityError):
                async with sessions.begin() as session:
                    session.add(Recording(
                        assignment_id=assignment.assignment_id,
                        storage_key="rus/retake.ogg",
                        duration=3.0,
                        language_id=language.language_id,
                    ))

            async with sessions.begin() as session:
                original = await session.get(Recording, recording.recording_id)
                self.assertEqual(original.status, RecordingStatus.CURRENT)
                original.status = RecordingStatus.SUPERSEDED
                await session.flush()
                session.add(Recording(
                    assignment_id=assignment.assignment_id,
                    storage_key="rus/retake.ogg",
                    duration=3.0,
                    language_id=language.language_id,
                ))

            async with sessions.begin() as session:
                self.assertEqual(
                    set(await session.scalars(select(Recording.status))),
                    {RecordingStatus.SUPERSEDED, RecordingStatus.CURRENT},
                )
                await session.execute(delete(Participant))

            async with sessions() as session:
                for model in (
                    TelegramIdentity, ParticipantLanguage, Assignment, Recording,
                    ParticipantRestriction, AbuseEvent,
                ):
                    self.assertEqual(await session.scalar(select(func.count()).select_from(model)), 0)
                self.assertEqual(await session.scalar(select(func.count()).select_from(CorpusText)), 1)
