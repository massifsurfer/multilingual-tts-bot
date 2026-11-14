from datetime import UTC, datetime

from sqlalchemy import select, text, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import Language, Participant, ParticipantDeletion, ParticipantLanguage, TelegramIdentity


class Participants:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @staticmethod
    async def _find(session: AsyncSession, telegram_id: int) -> Participant | None:
        identity = await session.get(TelegramIdentity, telegram_id)
        return await session.get(Participant, identity.participant_id) if identity else None

    async def is_active(self, telegram_id: int) -> bool:
        async with self._sessions() as session:
            participant = await self._find(session, telegram_id)
            return bool(participant and participant.is_active and participant.consent_at)

    async def consent(self, telegram_id: int) -> bool:
        async with self._sessions.begin() as session:
            # Serialize SQLite writers before reading the identity.
            await session.execute(text("BEGIN IMMEDIATE"))
            participant = await self._find(session, telegram_id)
            if participant is None:
                participant = Participant()
                session.add(participant)
                await session.flush()
                session.add(
                    TelegramIdentity(telegram_id=telegram_id, participant_id=participant.participant_id)
                )
            if await session.get(ParticipantDeletion, participant.participant_id) is not None:
                return False
            if not participant.is_active or participant.consent_at is None:
                participant.consent_at = datetime.now(UTC).replace(tzinfo=None)
                participant.is_active = True
            return True

    async def stop(self, telegram_id: int) -> None:
        async with self._sessions.begin() as session:
            participant_id = select(TelegramIdentity.participant_id).where(
                TelegramIdentity.telegram_id == telegram_id
            )
            await session.execute(
                update(Participant)
                .where(Participant.participant_id == participant_id.scalar_subquery())
                .values(is_active=False)
            )

    async def languages(self) -> list[Language]:
        async with self._sessions() as session:
            result = await session.scalars(
                select(Language).where(Language.is_active).order_by(Language.name)
            )
            return list(result)

    async def active_language(self, telegram_id: int) -> ParticipantLanguage | None:
        async with self._sessions() as session:
            result = await session.scalars(
                select(ParticipantLanguage)
                .join(TelegramIdentity, TelegramIdentity.participant_id == ParticipantLanguage.participant_id)
                .join(Language)
                .where(
                    TelegramIdentity.telegram_id == telegram_id,
                    ParticipantLanguage.is_active,
                    Language.is_active,
                )
                .order_by(ParticipantLanguage.language_id)
            )
            return result.first()

    async def choose_language(self, telegram_id: int, language_id: int) -> bool:
        async with self._sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            participant = await self._find(session, telegram_id)
            language = await session.get(Language, language_id)
            if (
                not participant or not participant.is_active or not participant.consent_at
                or not language or not language.is_active
            ):
                return False

            await session.execute(
                update(ParticipantLanguage)
                .where(ParticipantLanguage.participant_id == participant.participant_id)
                .values(is_active=False)
            )
            choice = insert(ParticipantLanguage).values(
                participant_id=participant.participant_id, language_id=language_id, is_active=True
            )
            await session.execute(
                choice.on_conflict_do_update(
                    index_elements=["participant_id", "language_id"], set_={"is_active": True}
                )
            )
            return True

    async def set_dialect(self, telegram_id: int, language_id: int, dialect: str | None) -> bool:
        active_participant_id = (
            select(Participant.participant_id)
            .join(TelegramIdentity)
            .where(
                TelegramIdentity.telegram_id == telegram_id,
                Participant.is_active,
                Participant.consent_at.is_not(None),
            )
            .scalar_subquery()
        )
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(ParticipantLanguage)
                .where(
                    ParticipantLanguage.participant_id == active_participant_id,
                    ParticipantLanguage.language_id == language_id,
                    ParticipantLanguage.is_active,
                    ParticipantLanguage.language_id.in_(select(Language.language_id).where(Language.is_active)),
                )
                .values(dialect=dialect)
                .returning(ParticipantLanguage.language_id)
            )
            return result.scalar_one_or_none() is not None
