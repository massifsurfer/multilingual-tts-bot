import asyncio
import errno
import logging
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO


_TEMP_PREFIX = ".upload-"


class LocalStorage:
    def __init__(self, root: Path, *, min_free_bytes: int = 0) -> None:
        self._root = root.resolve()
        self._min_free_bytes = min_free_bytes

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if "\\" in key or "\0" in key or any(
            part in {"", ".", ".."} or part.startswith(_TEMP_PREFIX) for part in parts
        ):
            raise ValueError("Invalid storage key")

        path = self._root.joinpath(*parts)
        if path.resolve() != path:
            raise ValueError("Storage key must not traverse symbolic links")
        return path

    async def save(self, source: BinaryIO, key: str) -> str:
        await asyncio.to_thread(self._save, source, key)
        return key

    def _save(self, source: BinaryIO, key: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self._min_free_bytes and shutil.disk_usage(path.parent).free < self._min_free_bytes:
            logging.getLogger(__name__).warning("Audio storage is below its free-space threshold")
            raise OSError(errno.ENOSPC, "Audio storage is below its free-space threshold")
        with NamedTemporaryFile(dir=path.parent, prefix=_TEMP_PREFIX) as temporary:
            shutil.copyfileobj(source, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
            # Publish atomically. An existing destination makes link fail.
            os.link(temporary.name, path)

    async def get(self, key: str) -> BinaryIO:
        return await asyncio.to_thread(self._get, key)

    def _get(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete, key)

    def _delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._exists, key)

    def _exists(self, key: str) -> bool:
        return self._path(key).is_file()
