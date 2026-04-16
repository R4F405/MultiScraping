"""
TikTok search/discovery layer.
Finds creators matching a hashtag or keyword via XHR interception.
"""
import logging

from backend.config.settings import settings
from backend.tiktok.tt_browser import intercept_search_xhr, rotate_browser_identity
from backend.tiktok.tt_retry import retry_with_backoff

logger = logging.getLogger(__name__)


async def find_creators(keyword: str, max_results: int = 100) -> list[dict]:
    """
    Search TikTok for creators matching keyword or hashtag.

    Args:
        keyword: Search term, e.g. "#fotografo" or "fotografo barcelona"
        max_results: Maximum number of creators to return

    Returns:
        List of dicts: {unique_id, sec_uid, nickname, follower_count}
    """
    # Normalize: strip leading '#' so TikTok search works correctly
    clean_keyword = keyword.lstrip("#").strip()
    if not clean_keyword:
        logger.warning("find_creators: empty keyword after normalization")
        return []

    logger.info("find_creators: searching for '%s' (max %d)", clean_keyword, max_results)

    async def _search_once() -> list[dict]:
        try:
            return await intercept_search_xhr(clean_keyword, max_results=max_results)
        except RuntimeError as exc:
            if "tiktok_challenge_detected" not in str(exc):
                raise
            await rotate_browser_identity(reason="challenge_detected")
            raise

    raw = await retry_with_backoff(
        _search_once,
        max_retries=max(0, settings.retry_max_attempts - 1),
        base_delay=settings.retry_base_delay,
        max_delay=settings.retry_max_delay,
        retry_on=(RuntimeError,),
    )

    # Filter: require uniqueId
    valid = [c for c in raw if c.get("unique_id")]

    # Deduplicate by unique_id (should already be done in tt_browser but belt+suspenders)
    seen: set[str] = set()
    result = []
    for creator in valid:
        uid = creator["unique_id"].lower()
        if uid not in seen:
            seen.add(uid)
            result.append(creator)

    logger.info("find_creators: found %d unique creators for '%s'", len(result), clean_keyword)
    return result[:max_results]
