from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings

from app.database.models import (
    AbuseEvent, AbuseEventType, Participant, ParticipantRestriction, RestrictionAudit,
    RestrictionStatus, TelegramIdentity, VoiceAttempt, VoiceReceipt,
)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def current_ban(restriction: ParticipantRestriction | None, now: datetime) -> str | None:
    if restriction and restriction.status == RestrictionStatus.BANNED:
        if restriction.banned_until is None or restriction.banned_until > now:
            return restriction.ban_reason or "Participation is restricted."
    return None


class AbuseProtection:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings):
        self.sessions = sessions
        self.settings = settings

    async def ban_reason(self, telegram_id: int) -> str | None:
        async with self.sessions() as session:
            restriction = await session.scalar(
                select(ParticipantRestriction).join(TelegramIdentity,
                    TelegramIdentity.participant_id == ParticipantRestriction.participant_id)
                .where(TelegramIdentity.telegram_id == telegram_id)
            )
            return current_ban(restriction, utc_now())

    async def check_voice(self, telegram_id: int, update_id: int, duration: int, size: int | None,
                          mime_type: str | None = None) -> str | None:
        now = utc_now()
        settings = self.settings
        async with self.sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            if await session.get(VoiceReceipt, update_id):
                return None
            participant_id = await session.scalar(
                select(Participant.participant_id).join(TelegramIdentity)
                .where(TelegramIdentity.telegram_id == telegram_id, Participant.is_active,
                       Participant.consent_at.is_not(None))
            )
            if participant_id is None:
                return "Use /start and accept the participation terms first."
            restriction = await session.get(ParticipantRestriction, participant_id)
            ban = current_ban(restriction, now)
            if ban:
                return ban
            attempt = await session.get(VoiceAttempt, update_id)
            if attempt:
                return attempt.rejection
            cutoff = now - timedelta(seconds=settings.upload_window_seconds)
            await session.execute(delete(VoiceAttempt).where(VoiceAttempt.created_at < now - timedelta(days=1)))
            count = await session.scalar(select(func.count()).select_from(VoiceAttempt).where(
                VoiceAttempt.participant_id == participant_id, VoiceAttempt.created_at > cutoff
            ))
            attempt = VoiceAttempt(update_id=update_id, participant_id=participant_id, created_at=now)
            session.add(attempt)
            if size is not None and size > settings.max_voice_bytes:
                await self._violation(session, attempt, restriction, AbuseEventType.AUDIO_TOO_LARGE,
                                      now, severe=size >= settings.severe_voice_bytes)
            elif not 0 < duration <= settings.max_voice_seconds:
                kind = AbuseEventType.AUDIO_TOO_LONG if duration > 0 else AbuseEventType.INVALID_AUDIO
                await self._violation(session, attempt, restriction, kind, now)
            elif mime_type and mime_type.split(";", 1)[0].strip().lower() not in {"audio/ogg", "audio/opus", "application/ogg"}:
                await self._violation(session, attempt, restriction, AbuseEventType.INVALID_AUDIO, now)
            elif count >= settings.upload_limit:
                await self._violation(session, attempt, restriction, AbuseEventType.UPLOAD_RATE_EXCEEDED, now)
            return attempt.rejection

    async def reject_download(self, update_id: int, kind: AbuseEventType) -> str | None:
        async with self.sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            attempt = await session.get(VoiceAttempt, update_id)
            if attempt is None or attempt.rejection:
                return attempt.rejection if attempt else None
            restriction = await session.get(ParticipantRestriction, attempt.participant_id)
            await self._violation(session, attempt, restriction, kind, utc_now())
            return attempt.rejection

    async def _violation(self, session, attempt, restriction, kind, now, *, severe=False):
        settings = self.settings
        reason = {
            AbuseEventType.AUDIO_TOO_LARGE: "The voice message is too large.",
            AbuseEventType.AUDIO_TOO_LONG: "The voice message is too long.",
            AbuseEventType.SPAM_DETECTED: "That voice message was already submitted. Record a new take.",
            AbuseEventType.INVALID_AUDIO: "Send a valid Telegram voice message.",
            AbuseEventType.UPLOAD_RATE_EXCEEDED: "Too many uploads. Try again later.",
        }[kind]
        attempt.rejection = reason
        session.add(AbuseEvent(participant_id=attempt.participant_id, event_type=kind, created_at=now))
        if restriction and (restriction.exempt_from_auto_ban or restriction.status == RestrictionStatus.EXEMPT
                            or current_ban(restriction, now)):
            return
        await session.flush()
        cutoff = now - timedelta(seconds=settings.violation_window_seconds)
        if restriction and restriction.updated_at > cutoff:
            cutoff = restriction.updated_at
        count = await session.scalar(select(func.count()).select_from(AbuseEvent).where(
            AbuseEvent.participant_id == attempt.participant_id, AbuseEvent.created_at > cutoff
        ))
        if severe or count >= settings.violation_limit:
            if restriction is None:
                restriction = ParticipantRestriction(participant_id=attempt.participant_id)
                session.add(restriction)
            restriction.status = RestrictionStatus.BANNED
            restriction.banned_at = now
            restriction.banned_until = now + timedelta(seconds=settings.auto_ban_seconds)
            restriction.ban_reason = f"Temporarily blocked. {reason}"
            restriction.updated_at = now
            attempt.rejection = restriction.ban_reason

    async def change(self, participant_id: UUID, admin_id: int, action: str, reason: str) -> bool:
        now = utc_now()
        async with self.sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            if await session.get(Participant, participant_id) is None:
                return False
            restriction = await session.get(ParticipantRestriction, participant_id)
            if restriction is None:
                restriction = ParticipantRestriction(participant_id=participant_id, exempt_from_auto_ban=False)
                session.add(restriction)
            if action == "ban":
                restriction.status = RestrictionStatus.BANNED
                restriction.ban_reason = reason
                restriction.banned_at = now
                restriction.banned_until = None
            else:
                if action != "unban":
                    restriction.exempt_from_auto_ban = action == "exempt"
                if action == "unban" or not current_ban(restriction, now):
                    restriction.status = (
                        RestrictionStatus.EXEMPT if restriction.exempt_from_auto_ban else RestrictionStatus.ACTIVE
                    )
                    restriction.ban_reason = restriction.banned_at = restriction.banned_until = None
            restriction.updated_at = now
            session.add(RestrictionAudit(participant_id=participant_id, admin_telegram_id=admin_id,
                                         action=action, reason=reason))
            return True

    async def stats(self, participant_id: UUID | None = None) -> str:
        async with self.sessions() as session:
            query = select(AbuseEvent.event_type, func.count()).group_by(AbuseEvent.event_type)
            lines = []
            if participant_id is not None:
                if await session.get(Participant, participant_id) is None:
                    return "Participant not found."
                restriction = await session.get(ParticipantRestriction, participant_id)
                ban = current_ban(restriction, utc_now())
                exempt = bool(restriction and restriction.exempt_from_auto_ban)
                lines.append(f"Status: {'banned' if ban else 'exempt' if exempt else 'active'}. Auto-ban exemption: {exempt}.")
                if ban:
                    lines.append(f"Reason: {ban}. Until: {restriction.banned_until or 'manual unban'}")
                query = query.where(AbuseEvent.participant_id == participant_id)
            counts = await session.execute(query)
            lines.extend(f"{kind.value}: {count}" for kind, count in counts)
            if participant_id is not None:
                recent = await session.execute(
                    select(AbuseEvent.created_at, AbuseEvent.event_type)
                    .where(AbuseEvent.participant_id == participant_id)
                    .order_by(AbuseEvent.event_id.desc())
                    .limit(10)
                )
                lines.extend(f"{created_at:%Y-%m-%d %H:%M:%S} UTC  {kind.value}" for created_at, kind in recent)
            return "\n".join(lines) or "No violations."
