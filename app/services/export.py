import asyncio
import csv
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    Assignment,
    CorpusText,
    Language,
    Participant,
    ParticipantDeletion,
    Recording,
    RecordingStatus,
)
from app.storage.base import StorageBackend


ExportFormat = Literal["jsonl", "csv"]
FIELDS = (
    "recording_id", "speaker_id", "text_id", "text", "audio",
    "language", "language_code", "dialect", "duration", "status",
)


class DatasetExport:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], storage: StorageBackend):
        self.sessions = sessions
        self.storage = storage

    async def run(
        self, destination: Path, *, format: ExportFormat, language_code: str | None,
        all_takes: bool,
    ) -> int:
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"Export destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)

        with TemporaryDirectory(prefix=".dataset-", dir=destination.parent) as directory:
            staging = Path(directory)
            (staging / "audio").mkdir()
            count = 0
            async with self.sessions.begin() as session:
                # Keep moderation and deletion from changing the snapshot while copying audio.
                await session.execute(text("BEGIN IMMEDIATE"))
                statement = (
                    select(
                        Recording.recording_id, Participant.speaker_id, CorpusText.text_id,
                        CorpusText.text, Language.name, Language.code, Recording.dialect,
                        Recording.duration, Recording.status, Recording.storage_key,
                    )
                    .select_from(Recording)
                    .join(Assignment, Assignment.assignment_id == Recording.assignment_id)
                    .join(Participant, Participant.participant_id == Assignment.participant_id)
                    .join(CorpusText, CorpusText.text_id == Assignment.text_id)
                    .join(Language, Language.language_id == Recording.language_id)
                    .where(~select(ParticipantDeletion.participant_id).where(
                        ParticipantDeletion.participant_id == Participant.participant_id
                    ).exists())
                    .order_by(Recording.recording_id)
                )
                if not all_takes:
                    statement = statement.where(Recording.status == RecordingStatus.ACCEPTED)
                if language_code is not None:
                    code = language_code.strip().lower()
                    if await session.scalar(select(Language.language_id).where(Language.code == code)) is None:
                        raise ValueError(f"Unknown language code: {code}")
                    statement = statement.where(Language.code == code)

                with (staging / f"metadata.{format}").open("w", encoding="utf-8", newline="") as manifest:
                    writer = csv.DictWriter(manifest, fieldnames=FIELDS) if format == "csv" else None
                    if writer is not None:
                        writer.writeheader()
                    rows = await session.stream(statement.execution_options(yield_per=100))
                    async for row in rows:
                        recording_id, speaker_id, text_id, prompt, language, code, dialect, duration, status, key = row
                        audio = f"audio/{recording_id}.ogg"
                        with await self.storage.get(key) as source, (staging / audio).open("wb") as target:
                            await asyncio.to_thread(shutil.copyfileobj, source, target)
                        item = dict(zip(FIELDS, (
                            str(recording_id), str(speaker_id), text_id, prompt, audio,
                            language, code, dialect, duration, status.value,
                        ), strict=True))
                        if writer is None:
                            manifest.write(json.dumps(item, ensure_ascii=False) + "\n")
                        else:
                            writer.writerow(item)
                        count += 1
                if destination.exists() or destination.is_symlink():
                    raise FileExistsError(f"Export destination already exists: {destination}")
                staging.rename(destination)
            return count
