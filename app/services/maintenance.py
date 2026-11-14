from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    Assignment,
    AssignmentStatus,
    Language,
    Participant,
    ParticipantDeletion,
    Recording,
    RecordingStatus,
    TelegramIdentity,
)
from app.storage.base import StorageBackend


class Maintenance:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], storage: StorageBackend):
        self._sessions = sessions
        self._storage = storage

    async def stats(self, telegram_id: int | None = None) -> dict:
        async with self._sessions() as session:
            assignments = select(Assignment.status, func.count()).group_by(Assignment.status)
            recordings = select(Recording.status, func.count()).group_by(Recording.status)
            result = {}
            if telegram_id is None:
                result["participants"] = await session.scalar(select(func.count()).select_from(Participant))
            else:
                identity = await session.get(TelegramIdentity, telegram_id)
                if identity is None:
                    return {"assignments": {}, "recordings": {}}
                assignments = assignments.where(Assignment.participant_id == identity.participant_id)
                recordings = recordings.join(Assignment).where(
                    Assignment.participant_id == identity.participant_id
                )
            result["assignments"] = dict((await session.execute(assignments)).all())
            result["recordings"] = dict((await session.execute(recordings)).all())
            return result

    async def recordings(self, status: RecordingStatus | None, limit: int) -> list[dict]:
        query = (
            select(Recording, Language.code)
            .join(Language)
            .outerjoin(Assignment, Recording.assignment_id == Assignment.assignment_id)
            .outerjoin(ParticipantDeletion, ParticipantDeletion.participant_id == Assignment.participant_id)
            .where(ParticipantDeletion.participant_id.is_(None))
            .order_by(Recording.recorded_at.desc(), Recording.recording_id)
            .limit(limit)
        )
        if status is not None:
            query = query.where(Recording.status == status)
        async with self._sessions() as session:
            rows = await session.execute(query)
            return [
                {
                    "recording_id": str(recording.recording_id),
                    "assignment_id": str(recording.assignment_id),
                    "storage_key": recording.storage_key,
                    "language_code": code,
                    "duration": recording.duration,
                    "status": recording.status.value,
                }
                for recording, code in rows
            ]

    async def moderate(self, recording_id: UUID, status: RecordingStatus) -> bool:
        if status not in {RecordingStatus.ACCEPTED, RecordingStatus.REJECTED}:
            raise ValueError("Choose accepted or rejected.")
        async with self._sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            recording = await session.get(Recording, recording_id)
            if recording is None:
                return False
            assignment = await session.get(Assignment, recording.assignment_id)
            if await session.get(ParticipantDeletion, assignment.participant_id):
                raise ValueError("Participant deletion is in progress.")
            if assignment.status == AssignmentStatus.PENDING:
                raise ValueError("Finish or cancel the pending retake before moderation.")
            if recording.status == RecordingStatus.SUPERSEDED:
                raise ValueError("Superseded takes cannot be moderated.")
            if status == RecordingStatus.ACCEPTED:
                current = await session.scalar(select(Recording.recording_id).where(
                    Recording.assignment_id == assignment.assignment_id,
                    Recording.recording_id != recording_id,
                    Recording.status.in_((RecordingStatus.CURRENT, RecordingStatus.ACCEPTED)),
                ))
                if current is not None:
                    raise ValueError("This assignment already has a current or accepted take.")
                if not await self._storage.exists(recording.storage_key):
                    raise ValueError("The audio file is missing.")
            recording.status = status
            return True

    async def delete_participant(self, participant_id: UUID) -> bool:
        # Persist the deletion request before touching files, so a failed
        # attempt can be retried and new recordings cannot be submitted.
        async with self._sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            participant = await session.get(Participant, participant_id)
            if participant is None:
                return False
            participant.is_active = False
            if await session.get(ParticipantDeletion, participant_id) is None:
                session.add(ParticipantDeletion(participant_id=participant_id))

        async with self._sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            participant = await session.get(Participant, participant_id)
            if participant is None:
                return True
            keys = await session.scalars(
                select(Recording.storage_key).join(Assignment)
                .where(Assignment.participant_id == participant_id)
            )
            for key in keys:
                await self._storage.delete(key)
            await session.delete(participant)
            return True

    async def participant_id(self, telegram_id: int) -> UUID | None:
        async with self._sessions() as session:
            identity = await session.get(TelegramIdentity, telegram_id)
            return identity.participant_id if identity else None
