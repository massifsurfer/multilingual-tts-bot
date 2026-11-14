import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.maintenance import copy_dataset, restore_dataset
from app.storage.local import LocalStorage


class MaintenanceTest(IsolatedAsyncioTestCase):
    async def test_restore_preserves_database_and_audio_and_rejects_corruption(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "original.sqlite"
            with closing(sqlite3.connect(database)) as source, source:
                source.execute("CREATE TABLE recordings (storage_key TEXT PRIMARY KEY)")
                source.execute("INSERT INTO recordings VALUES (?)", ("rus/first.ogg",))

            audio = LocalStorage(root / "original-audio")
            with BytesIO(b"OggS original audio") as stream:
                await audio.save(stream, "rus/first.ogg")

            backup = root / "backup"
            self.assertEqual(await copy_dataset(database, audio, backup), 1)
            restored = root / "restored"
            self.assertEqual(await restore_dataset(backup, restored), 1)
            with closing(sqlite3.connect(restored / "database.sqlite")) as copy:
                self.assertEqual(copy.execute("SELECT * FROM recordings").fetchall(), [("rus/first.ogg",)])
            self.assertEqual(
                (restored / "audio/rus/first.ogg").read_bytes(), b"OggS original audio"
            )
            with self.assertRaises(FileExistsError):
                await restore_dataset(backup, restored)

            (backup / "audio/rus/first.ogg").write_bytes(b"corrupted")
            failed = root / "failed"
            with self.assertRaisesRegex(ValueError, "audio checksum mismatch"):
                await restore_dataset(backup, failed)
            self.assertFalse(failed.exists())
            self.assertFalse(list(root.glob(".dataset-copy-*")))

    async def test_low_disk_space_rejects_save_before_creating_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            storage = LocalStorage(root, min_free_bytes=100)
            with patch("app.storage.local.shutil.disk_usage") as usage, BytesIO(b"payload") as stream:
                usage.return_value.free = 99
                with self.assertLogs("app.storage.local", level="WARNING"):
                    with self.assertRaises(OSError):
                        await storage.save(stream, "test.ogg")
            self.assertFalse(list(root.iterdir()))
