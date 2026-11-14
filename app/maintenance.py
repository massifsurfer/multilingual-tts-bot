import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from app.storage.local import LocalStorage


def _snapshot(source: Path, target: Path) -> list[str]:
    uri = source.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as database:
        with closing(sqlite3.connect(target)) as copy:
            database.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("SQLite integrity check failed.")
            if copy.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("SQLite foreign key check failed.")
            return [row[0] for row in copy.execute(
                "SELECT storage_key FROM recordings ORDER BY storage_key"
            )]


async def copy_dataset(
    database: Path, audio: LocalStorage, destination: Path, *, expected: dict | None = None
) -> int:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix=".dataset-copy-", dir=destination.parent) as directory:
        staging = Path(directory)
        target_database = staging / "database.sqlite"
        keys = _snapshot(database, target_database)
        with target_database.open("rb") as stream:
            database_hash = hashlib.file_digest(stream, "sha256").hexdigest()

        (staging / "audio").mkdir()
        target_audio = LocalStorage(staging / "audio")
        hashes = {}
        for key in keys:
            with await audio.get(key) as stream:
                await target_audio.save(stream, key)
            with await target_audio.get(key) as stream:
                hashes[key] = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest = {"database_sha256": database_hash, "audio_sha256": hashes}
        if expected is not None and expected["audio_sha256"] != hashes:
            raise ValueError("Backup audio checksum mismatch.")
        (staging / "checksums.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"Destination already exists: {destination}")
        staging.rename(destination)
    return len(keys)


async def restore_dataset(backup: Path, destination: Path) -> int:
    manifest = json.loads((backup / "checksums.json").read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict) or not isinstance(manifest.get("database_sha256"), str)
        or not isinstance(manifest.get("audio_sha256"), dict)
    ):
        raise ValueError("Invalid backup checksum manifest.")
    database = backup / "database.sqlite"
    with database.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != manifest["database_sha256"]:
            raise ValueError("Backup database checksum mismatch.")
    return await copy_dataset(database, LocalStorage(backup / "audio"), destination, expected=manifest)
