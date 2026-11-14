from asyncio import gather
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from app.storage.local import LocalStorage


class BrokenStream(BytesIO):
    def read(self, size=-1):
        chunk = super().read(min(size, 3))
        if not chunk:
            raise OSError("Interrupted read")
        return chunk


class LocalStorageTest(IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "audio"
        self.storage = LocalStorage(self.root)
        self.key = "rus/2026/11/original.ogg"

    async def test_round_trip_preserves_original_bytes(self):
        original = b"OggS\x00\xff"
        with BytesIO(original) as source:
            self.assertEqual(await self.storage.save(source, self.key), self.key)
            self.assertFalse(source.closed)
        self.assertTrue(await self.storage.exists(self.key))

        with await self.storage.get(self.key) as stored:
            self.assertEqual(stored.read(), original)

        await self.storage.delete(self.key)
        await self.storage.delete(self.key)
        self.assertFalse(await self.storage.exists(self.key))

    async def test_concurrent_saves_do_not_overwrite(self):
        originals = [b"first", b"second"]
        results = await gather(
            *(self.storage.save(BytesIO(audio), self.key) for audio in originals),
            return_exceptions=True,
        )
        self.assertEqual(results.count(self.key), 1)
        self.assertEqual(sum(isinstance(result, FileExistsError) for result in results), 1)
        with await self.storage.get(self.key) as stored:
            self.assertEqual(stored.read(), originals[results.index(self.key)])
        self.assertEqual(list(self.root.rglob(".upload-*")), [])

    async def test_failed_copy_removes_temporary_file(self):
        with BrokenStream(b"partial audio") as source, self.assertRaises(OSError):
            await self.storage.save(source, self.key)
        self.assertFalse(await self.storage.exists(self.key))
        self.assertEqual(list(self.root.rglob(".upload-*")), [])

    async def test_rejects_paths_outside_storage(self):
        self.root.mkdir()
        outside = self.root.parent / "outside.ogg"
        outside.write_bytes(b"untouched")
        (self.root / "link").symlink_to(outside)

        for key in ("../outside.ogg", str(outside), "rus//file.ogg", "link"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.storage.delete(key)
        self.assertEqual(outside.read_bytes(), b"untouched")
