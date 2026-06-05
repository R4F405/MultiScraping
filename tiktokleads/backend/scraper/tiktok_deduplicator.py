import logging
from datetime import datetime, timedelta

from backend.scraper.tiktok_client import _norm_handle_key
from backend.storage import database as db

logger = logging.getLogger(__name__)


class TikTokDeduplicator:
    """Cross-job skip: leads con email (siempre) + skipped recientes sin email (ventana de días)."""

    def __init__(self):
        self._norm_handles: set[str] = set()

    async def load_from_db(self, staleness_days: int = 3) -> None:
        leads = await db.get_lead_handles_with_email_norms()
        n_leads = len(leads)
        self._norm_handles.update(leads)

        cutoff = (datetime.now() - timedelta(days=staleness_days)).isoformat()
        recent = await db.get_recent_skipped_handle_norms(cutoff)
        n_skipped = len(recent)
        self._norm_handles.update(recent)

        logger.info(
            "TikTokDeduplicator: %d leads con email + %d skipped recientes → %d handles a excluir",
            n_leads,
            n_skipped,
            len(self._norm_handles),
        )

    @property
    def exclude_set(self) -> set[str]:
        return self._norm_handles

    def should_skip(self, handle_or_norm: str) -> bool:
        nk = _norm_handle_key(handle_or_norm)
        return bool(nk) and nk in self._norm_handles
