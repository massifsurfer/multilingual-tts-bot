from dataclasses import dataclass
from enum import Enum, auto

from sqlalchemy import func, literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    Assignment,
    AssignmentStatus,
    CorpusText,
    Language,
    Participant,
    ParticipantLanguage,
    ParticipantRestriction,
    Recording,
    RecordingStatus,
    TelegramIdentity,
)

from app.services.abuse import current_ban, utc_now


class AssignmentOutcome(Enum):
    NEED_CONSENT = auto()
    NEED_LANGUAGE = auto()
    NO_TEXTS = auto()
    NO_PENDING = auto()
    SKIPPED = auto()
    TASK_OPEN = auto()
    BLOCKED = auto()


@dataclass(frozen=True)
class ReadingTask:
    text: str


class Assignments:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def next(self, telegram_id: int) -> ReadingTask | AssignmentOutcome:
        return await self._handle(telegram_id, action="next")

    async def skip(self, telegram_id: int) -> AssignmentOutcome:
        result = await self._handle(telegram_id, action="skip")
        assert isinstance(result, AssignmentOutcome)
        return result

    async def rerecord(self, telegram_id: int) -> ReadingTask | AssignmentOutcome:
        return await self._handle(telegram_id, action="rerecord")

    async def _handle(self, telegram_id: int, *, action: str) -> ReadingTask | AssignmentOutcome:
        async with self._sessions.begin() as session:
            # Keep selection and insertion in the same SQLite write transaction.
            await session.execute(text("BEGIN IMMEDIATE"))
            participant_id = await session.scalar(
                select(Participant.participant_id)
                .join(TelegramIdentity)
                .where(
                    TelegramIdentity.telegram_id == telegram_id,
                    Participant.is_active,
                    Participant.consent_at.is_not(None),
                )
            )
            if participant_id is None:
                return AssignmentOutcome.NEED_CONSENT

            if action != "skip":
                restriction = await session.get(ParticipantRestriction, participant_id)
                if current_ban(restriction, utc_now()):
                    return AssignmentOutcome.BLOCKED

            language_id = await session.scalar(
                select(ParticipantLanguage.language_id)
                .join(Language)
                .where(
                    ParticipantLanguage.participant_id == participant_id,
                    ParticipantLanguage.is_active,
                    Language.is_active,
                )
                .order_by(ParticipantLanguage.language_id)
                .limit(1)
            )
            if language_id is None:
                return AssignmentOutcome.NEED_LANGUAGE

            pending = (
                await session.execute(
                    select(Assignment, CorpusText.text)
                    .join(CorpusText)
                    .where(
                        Assignment.participant_id == participant_id,
                        Assignment.status == AssignmentStatus.PENDING,
                        CorpusText.language_id == language_id,
                        CorpusText.is_active,
                    )
                    .order_by(Assignment.assigned_at, Assignment.assignment_id)
                    .limit(1)
                )
            ).first()
            if pending:
                assignment, prompt = pending
                live_take = await session.scalar(
                    select(Recording.recording_id).where(
                        Recording.assignment_id == assignment.assignment_id,
                        Recording.status.in_((RecordingStatus.CURRENT, RecordingStatus.ACCEPTED, RecordingStatus.REJECTED)),
                    )
                )
                if action == "skip":
                    assignment.status = (
                        AssignmentStatus.COMPLETED if live_take else AssignmentStatus.SKIPPED
                    )
                    return AssignmentOutcome.SKIPPED
                if action == "rerecord" and live_take is None:
                    return AssignmentOutcome.TASK_OPEN
                return ReadingTask(prompt)
            if action == "skip":
                return AssignmentOutcome.NO_PENDING
            if action == "rerecord":
                latest = (await session.execute(
                    select(Assignment, CorpusText.text)
                    .join(CorpusText)
                    .join(Recording)
                    .where(
                        Assignment.participant_id == participant_id,
                        Assignment.status == AssignmentStatus.COMPLETED,
                        CorpusText.language_id == language_id,
                        CorpusText.is_active,
                        Recording.status.in_((RecordingStatus.CURRENT, RecordingStatus.ACCEPTED, RecordingStatus.REJECTED)),
                    )
                    .order_by(Recording.recorded_at.desc(), literal_column("recordings.rowid").desc())
                    .limit(1)
                )).first()
                if latest is None:
                    return AssignmentOutcome.NO_PENDING
                assignment, prompt = latest
                assignment.status = AssignmentStatus.PENDING
                return ReadingTask(prompt)

            assigned = select(Assignment.text_id).where(Assignment.participant_id == participant_id)
            corpus_text = await session.scalar(
                select(CorpusText)
                .where(
                    CorpusText.language_id == language_id,
                    CorpusText.is_active,
                    CorpusText.text_id.not_in(assigned),
                )
                .order_by(func.random())
                .limit(1)
            )
            if corpus_text is None:
                return AssignmentOutcome.NO_TEXTS

            session.add(Assignment(participant_id=participant_id, text_id=corpus_text.text_id))
            return ReadingTask(corpus_text.text)
