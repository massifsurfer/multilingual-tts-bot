from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    Assignment, CorpusText, Participant, ParticipantDeletion, Recording, RecordingStatus,
)
from app.storage.base import StorageBackend


class Management:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], storage: StorageBackend):
        self.sessions = sessions
        self.storage = storage

    async def stats(self) -> dict:
        async with self.sessions.begin() as session:
            result = {}
            for model in (Participant, CorpusText, Assignment, Recording):
                result[model.__tablename__] = await session.scalar(select(func.count()).select_from(model))
            statuses = await session.execute(
                select(Recording.status, func.count()).group_by(Recording.status)
            )
            result["recording_statuses"] = {status.value: count for status, count in statuses}
            return result

    async def recordings(self, limit: int) -> list[dict]:
        async with self.sessions() as session:
            rows = await session.execute(
                select(Recording, CorpusText.text, Assignment.participant_id)
                .select_from(Recording)
                .join(Assignment)
                .join(CorpusText)
                .order_by(Recording.recorded_at.desc(), Recording.recording_id)
                .limit(limit)
            )
            return [
                {
                    "recording_id": str(take.recording_id),
                    "assignment_id": str(take.assignment_id),
                    "participant_id": str(participant_id),
                    "storage_key": take.storage_key,
                    "duration": take.duration,
                    "status": take.status.value,
                    "text": prompt,
                }
                for take, prompt, participant_id in rows
            ]

    async def moderate(self, recording_id: UUID, status: RecordingStatus) -> bool:
        async with self.sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            take = await session.get(Recording, recording_id)
            if take is None:
                return False
            if take.status not in (RecordingStatus.CURRENT, RecordingStatus.ACCEPTED):
                if take.status == status:
                    return True
                raise ValueError("Only the current take can be moderated.")
            take.status = status
            return True

    async def delete_participant(self, participant_id: UUID) -> bool:
        # Persist the deletion request before touching audio so a retry can find every key.
        async with self.sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            participant = await session.get(Participant, participant_id)
            if participant is None:
                return False
            participant.is_active = False
            if await session.get(ParticipantDeletion, participant_id) is None:
                session.add(ParticipantDeletion(participant_id=participant_id))

        async with self.sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            participant = await session.get(Participant, participant_id)
            if participant is None:
                return True
            keys = await session.scalars(
                select(Recording.storage_key).join(Assignment)
                .where(Assignment.participant_id == participant_id)
            )
            for key in keys:
                await self.storage.delete(key)
            await session.delete(participant)
            return True
