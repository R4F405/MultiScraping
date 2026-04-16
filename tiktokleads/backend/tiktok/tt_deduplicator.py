import asyncio

from backend.storage import database


class Deduplicator:
    """In-memory deduplication set backed by the database on startup."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = asyncio.Lock()

    async def preload(self) -> None:
        """Load all previously seen usernames from DB into memory."""
        self._seen = await database.get_all_seen_usernames()

    def is_duplicate(self, username: str) -> bool:
        return username.lower() in self._seen

    async def mark_seen(self, username: str) -> None:
        async with self._lock:
            self._seen.add(username.lower())

    @property
    def seen_count(self) -> int:
        return len(self._seen)


# Singleton
deduplicator = Deduplicator()
