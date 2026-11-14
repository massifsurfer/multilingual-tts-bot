from typing import BinaryIO, Protocol


class StorageBackend(Protocol):
    async def save(self, source: BinaryIO, key: str) -> str:
        """Copy from the stream's current position without taking ownership."""
        ...

    async def get(self, key: str) -> BinaryIO:
        """Open an object for reading. The caller closes the stream."""
        ...

    async def delete(self, key: str) -> None:
        """Remove an object if it exists."""
        ...

    async def exists(self, key: str) -> bool:
        """Return whether the key identifies a stored object."""
        ...
