import logging
import re
from collections.abc import Awaitable, Callable
from enum import Enum, auto
from io import BytesIO
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    Assignment,
    AssignmentStatus,
    CorpusText,
    Language,
    Participant,
    ParticipantLanguage,
    ParticipantRestriction,
    RecordedTelegramFile,
    Recording,
    RecordingStatus,
    TelegramIdentity,
    VoiceReceipt,
)
from app.services.abuse import current_ban, utc_now
from app.services.audio import decoded_duration
from app.storage.base import StorageBackend


class VoiceOutcome(Enum):
    SAVED = auto()
    NO_TASK = auto()
    BLOCKED = auto()
    TOO_LARGE = auto()
    INVALID = auto()
    TOO_LONG = auto()
    DUPLICATE = auto()


class UploadTooLarge(ValueError):
    pass


class BoundedAudio(BytesIO):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit

    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > self.limit:
            raise UploadTooLarge
        return super().write(data)


class Recordings:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], storage: StorageBackend,
        *, max_bytes: int, max_seconds: int,
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._max_bytes = max_bytes
        self._max_seconds = max_seconds

    @staticmethod
    async def _pending(session: AsyncSession, telegram_id: int):
        return (await session.execute(
            select(Assignment, CorpusText.language_id, ParticipantLanguage.dialect)
            .join(CorpusText)
            .join(Language)
            .join(Participant, Participant.participant_id == Assignment.participant_id)
            .join(TelegramIdentity)
            .join(
                ParticipantLanguage,
                (ParticipantLanguage.participant_id == Participant.participant_id)
                & (ParticipantLanguage.language_id == CorpusText.language_id),
            )
            .where(
                TelegramIdentity.telegram_id == telegram_id,
                Participant.is_active,
                Participant.consent_at.is_not(None),
                ParticipantLanguage.is_active,
                Language.is_active,
                CorpusText.is_active,
                Assignment.status == AssignmentStatus.PENDING,
            )
            .order_by(Assignment.assigned_at, Assignment.assignment_id)
            .limit(1)
        )).first()

    async def submit(
        self, telegram_id: int, update_id: int, duration: int, file_size: int | None,
        download: Callable[[BytesIO], Awaitable[object]], *, file_unique_id: str | None = None,
    ) -> VoiceOutcome:
        async with self._sessions() as session:
            if await session.get(VoiceReceipt, update_id):
                return VoiceOutcome.SAVED
            if file_unique_id:
                identity = await session.get(TelegramIdentity, telegram_id)
                if identity and await session.get(RecordedTelegramFile, (identity.participant_id, file_unique_id)):
                    return VoiceOutcome.DUPLICATE
            pending = await self._pending(session, telegram_id)
            if pending is None:
                return VoiceOutcome.NO_TASK
            assignment_id = pending[0].assignment_id

        if not 0 < duration <= self._max_seconds:
            return VoiceOutcome.INVALID
        if file_size is not None and file_size > self._max_bytes:
            return VoiceOutcome.TOO_LARGE

        with BoundedAudio(self._max_bytes) as audio:
            try:
                await download(audio)
            except UploadTooLarge:
                return VoiceOutcome.TOO_LARGE
            actual_duration = await decoded_duration(audio.getvalue(), self._max_seconds)
            if actual_duration is None:
                return VoiceOutcome.INVALID
            if actual_duration > self._max_seconds:
                return VoiceOutcome.TOO_LONG
            audio.seek(0)
            return await self._save(
                telegram_id, update_id, assignment_id, actual_duration, audio, file_unique_id
            )

    async def _save(self, telegram_id, update_id, assignment_id, duration, audio, file_unique_id):
        recording_id = uuid4()
        storage_key = None
        stored = False
        try:
            async with self._sessions.begin() as session:
                await session.execute(text("BEGIN IMMEDIATE"))
                if await session.get(VoiceReceipt, update_id):
                    return VoiceOutcome.SAVED
                pending = await self._pending(session, telegram_id)
                if pending is None or pending[0].assignment_id != assignment_id:
                    return VoiceOutcome.NO_TASK
                assignment, language_id, dialect = pending
                restriction = await session.get(ParticipantRestriction, assignment.participant_id)
                now = utc_now()
                if current_ban(restriction, now):
                    return VoiceOutcome.BLOCKED
                if file_unique_id and await session.get(
                    RecordedTelegramFile, (assignment.participant_id, file_unique_id)
                ):
                    return VoiceOutcome.DUPLICATE
                code = await session.scalar(select(Language.code).where(Language.language_id == language_id))
                folder = code if code and re.fullmatch("[a-z]{3}", code) else f"language-{language_id}"
                storage_key = f"{folder}/{now:%Y/%m}/{recording_id}.ogg"
                await self._storage.save(audio, storage_key)
                stored = True
                await session.execute(
                    update(Recording)
                    .where(
                        Recording.assignment_id == assignment_id,
                        Recording.status.in_((RecordingStatus.CURRENT, RecordingStatus.ACCEPTED, RecordingStatus.REJECTED)),
                    )
                    .values(status=RecordingStatus.SUPERSEDED)
                )
                session.add(Recording(
                    recording_id=recording_id, assignment_id=assignment_id,
                    storage_key=storage_key, duration=duration, language_id=language_id,
                    dialect=dialect, recorded_at=now,
                ))
                await session.flush()
                session.add(VoiceReceipt(update_id=update_id, recording_id=recording_id))
                if file_unique_id:
                    session.add(RecordedTelegramFile(
                        participant_id=assignment.participant_id, file_unique_id=file_unique_id
                    ))
                assignment.status = AssignmentStatus.COMPLETED
        except Exception:
            if stored:
                try:
                    await self._storage.delete(storage_key)
                except OSError:
                    logging.getLogger(__name__).error("Could not clean up audio after database failure")
            raise
        return VoiceOutcome.SAVED
