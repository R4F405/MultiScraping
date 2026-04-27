import logging
from datetime import datetime, timedelta

from backend.storage import database as db

logger = logging.getLogger(__name__)


class Deduplicator:
    def __init__(self):
        self._seen: set[str] = set()

    async def load_from_db(self, staleness_days: int = 3) -> None:
        """Load already-processed usernames into RAM at job start.

        - ig_leads (has email): always hard-skip — no need to re-fetch
        - ig_skipped (no email/private): skip only if checked within staleness_days,
          so profiles that updated their bio get another chance after the window
        """
        leads_usernames = await db.get_leads_usernames()
        self._seen.update(leads_usernames)

        cutoff = (datetime.now() - timedelta(days=staleness_days)).isoformat()
        recent_skipped = await db.get_recent_skipped_usernames(cutoff)
        self._seen.update(recent_skipped)

        logger.info(
            "Deduplicator: %d leads + %d recent skipped = %d profiles to skip",
            len(leads_usernames), len(recent_skipped), len(self._seen),
        )

    def should_skip(self, username: str) -> bool:
        return username in self._seen

    def mark_seen(self, username: str):
        self._seen.add(username)

    @property
    def skipped_count(self) -> int:
        return len(self._seen)
