import argparse
import json
import sqlite3
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config.settings import Settings
from app.database.connection import create_database_engine, initialize_database
from app.database.models import RecordingStatus
from app.maintenance import copy_dataset, restore_dataset
from app.services.export import DatasetExport
from app.services.management import Management
from app.storage.local import LocalStorage


COMMANDS = {"stats", "list-recordings", "moderate-recording", "delete-participant", "export-dataset", "backup", "restore"}


def add_commands(commands) -> None:
    backup = commands.add_parser("backup", help="Copy SQLite and referenced audio to a new directory")
    backup.add_argument("destination", type=Path)
    restore = commands.add_parser("restore", help="Verify and restore a backup into a new directory")
    restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path)
    export = commands.add_parser("export-dataset", help="Export metadata and original audio")
    export.add_argument("destination", type=Path, help="New output directory")
    export.add_argument("--format", choices=("jsonl", "csv"), default="jsonl")
    export.add_argument("--language", metavar="CODE", help="Limit export to one language code")
    export.add_argument("--all-takes", action="store_true", help="Include every recording status")
    commands.add_parser("stats", help="Show corpus and recording counts")
    recordings = commands.add_parser("list-recordings", help="List recent recordings")
    recordings.add_argument("--limit", type=int, default=20)
    moderate = commands.add_parser("moderate-recording", help="Accept or reject a current take")
    moderate.add_argument("recording_id", type=UUID)
    moderate.add_argument("status", choices=("accepted", "rejected"))
    deletion = commands.add_parser("delete-participant", help="Delete a participant and all audio")
    deletion.add_argument("participant_id", type=UUID)
    deletion.add_argument("--confirm", action="store_true", required=True)


async def run_command(settings: Settings, args: argparse.Namespace) -> str:
    if args.command in {"backup", "restore"}:
        try:
            if args.command == "backup":
                count = await copy_dataset(
                    settings.database_path, LocalStorage(settings.local_storage_path), args.destination
                )
            else:
                count = await restore_dataset(args.backup, args.destination)
        except sqlite3.Error as exc:
            raise ValueError(f"SQLite maintenance failed: {exc}") from exc
        return f"Copied SQLite and {count} audio files to {args.destination}."

    await initialize_database(settings.database_path)
    engine = create_database_engine(settings.database_path)
    try:
        sessions = async_sessionmaker(engine)
        storage = LocalStorage(settings.local_storage_path)
        if args.command == "export-dataset":
            count = await DatasetExport(sessions, storage).run(
                args.destination, format=args.format, language_code=args.language,
                all_takes=args.all_takes,
            )
            return f"Exported {count} recordings to {args.destination}."
        management = Management(sessions, storage)
        if args.command == "stats":
            result = await management.stats()
        elif args.command == "list-recordings":
            if args.limit <= 0:
                raise ValueError("--limit must be positive")
            result = await management.recordings(args.limit)
        elif args.command == "moderate-recording":
            if not await management.moderate(args.recording_id, RecordingStatus(args.status)):
                raise ValueError("Recording not found.")
            return "Recording updated."
        else:
            if not await management.delete_participant(args.participant_id):
                raise ValueError("Participant not found.")
            return "Participant deleted."
        return json.dumps(result, ensure_ascii=False, indent=2)
    finally:
        await engine.dispose()
