import logging

from backend.storage import database as db

logger = logging.getLogger(__name__)


class Deduplicator:
    def __init__(self):
        self._seen: set[str] = set()

    async def load_from_db(self):
        """Load all already-processed usernames into RAM at job start.
        Keeps checks at O(1) instead of hitting SQLite on every iteration."""
        existing = await db.get_all_scraped_usernames()
        self._seen.update(existing)
        logger.info("Deduplicator: %d profiles already in DB — will skip them", len(self._seen))

    def should_skip(self, username: str) -> bool:
        return username in self._seen

    def mark_seen(self, username: str):
        self._seen.add(username)

    @property
    def skipped_count(self) -> int:
        return len(self._seen)
