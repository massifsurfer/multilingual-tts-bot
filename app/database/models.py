from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, Text, UniqueConstraint, func, text as sql_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AssignmentStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class RecordingStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RestrictionStatus(StrEnum):
    ACTIVE = "active"
    BANNED = "banned"
    EXEMPT = "exempt"


class AbuseEventType(StrEnum):
    AUDIO_TOO_LONG = "audio_too_long"
    AUDIO_TOO_LARGE = "audio_too_large"
    UPLOAD_RATE_EXCEEDED = "upload_rate_exceeded"
    INVALID_AUDIO = "invalid_audio"
    SPAM_DETECTED = "spam_detected"


def enum_column(enum_type: type[StrEnum]) -> Enum:
    return Enum(
        enum_type,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class Language(Base):
    __tablename__ = "languages"

    language_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    code: Mapped[str | None] = mapped_column(unique=True)
    is_active: Mapped[bool] = mapped_column(server_default=sql_text("1"))


class Participant(Base):
    __tablename__ = "participants"

    participant_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    speaker_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    consent_at: Mapped[datetime | None]
    is_active: Mapped[bool] = mapped_column(server_default=sql_text("0"))


class TelegramIdentity(Base):
    __tablename__ = "telegram_identities"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.participant_id", ondelete="CASCADE"), unique=True
    )


class ParticipantLanguage(Base):
    __tablename__ = "participant_languages"

    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.participant_id", ondelete="CASCADE"), primary_key=True
    )
    language_id: Mapped[int] = mapped_column(ForeignKey("languages.language_id"), primary_key=True)
    dialect: Mapped[str | None]
    is_active: Mapped[bool] = mapped_column(server_default=sql_text("1"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class CorpusText(Base):
    __tablename__ = "texts"
    __table_args__ = (Index("ix_texts_language_active", "language_id", "is_active"),)

    text_id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    language_id: Mapped[int] = mapped_column(ForeignKey("languages.language_id"))
    dialect: Mapped[str | None]
    difficulty: Mapped[str | None]
    category: Mapped[str | None]
    is_active: Mapped[bool] = mapped_column(server_default=sql_text("1"))


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("participant_id", "text_id"),)

    assignment_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.participant_id", ondelete="CASCADE")
    )
    text_id: Mapped[int] = mapped_column(ForeignKey("texts.text_id"))
    assigned_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    status: Mapped[AssignmentStatus] = mapped_column(
        enum_column(AssignmentStatus), server_default=AssignmentStatus.PENDING.value
    )


class Recording(Base):
    __tablename__ = "recordings"
    __table_args__ = (
        Index(
            "uq_recordings_live_assignment",
            "assignment_id",
            unique=True,
            sqlite_where=sql_text("status IN ('current', 'accepted')"),
        ),
    )

    recording_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    assignment_id: Mapped[UUID] = mapped_column(
        ForeignKey("assignments.assignment_id", ondelete="CASCADE"), index=True
    )
    storage_key: Mapped[str] = mapped_column(unique=True)
    duration: Mapped[float]
    language_id: Mapped[int] = mapped_column(ForeignKey("languages.language_id"))
    dialect: Mapped[str | None]
    recorded_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    status: Mapped[RecordingStatus] = mapped_column(
        enum_column(RecordingStatus), server_default=RecordingStatus.CURRENT.value
    )


class ParticipantRestriction(Base):
    __tablename__ = "participant_restrictions"

    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.participant_id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[RestrictionStatus] = mapped_column(
        enum_column(RestrictionStatus), server_default=RestrictionStatus.ACTIVE.value
    )
    ban_reason: Mapped[str | None] = mapped_column(Text)
    banned_at: Mapped[datetime | None]
    banned_until: Mapped[datetime | None]
    exempt_from_auto_ban: Mapped[bool] = mapped_column(server_default=sql_text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )


class AbuseEvent(Base):
    __tablename__ = "abuse_events"
    __table_args__ = (Index("ix_abuse_events_participant_created", "participant_id", "created_at"),)

    event_id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.participant_id", ondelete="CASCADE")
    )
    event_type: Mapped[AbuseEventType] = mapped_column(enum_column(AbuseEventType))
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class VoiceReceipt(Base):
    __tablename__ = "voice_receipts"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    recording_id: Mapped[UUID] = mapped_column(
        ForeignKey("recordings.recording_id", ondelete="CASCADE"), index=True
    )


class VoiceAttempt(Base):
    __tablename__ = "voice_attempts"
    __table_args__ = (Index("ix_voice_attempts_participant_time", "participant_id", "created_at"),)

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.participant_id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime]
    rejection: Mapped[str | None]


class RestrictionAudit(Base):
    __tablename__ = "restriction_audit"

    audit_id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.participant_id", ondelete="CASCADE"), index=True
    )
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str]
    reason: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class ParticipantDeletion(Base):
    __tablename__ = "participant_deletions"

    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.participant_id", ondelete="CASCADE"), primary_key=True
    )


class RecordedTelegramFile(Base):
    __tablename__ = "recorded_telegram_files"

    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.participant_id", ondelete="CASCADE"), primary_key=True
    )
    file_unique_id: Mapped[str] = mapped_column(primary_key=True)
