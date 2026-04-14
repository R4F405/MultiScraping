import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_with_backoff(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    retry_on: tuple = (Exception,),
    **kwargs: Any,
) -> T:
    """
    Execute async function with exponential backoff retry.

    Args:
        fn: Async function to execute
        max_retries: Maximum number of retries (default 3)
        base_delay: Initial delay in seconds (default 5.0)
        max_delay: Maximum delay in seconds (default 120.0)
        retry_on: Exception types to retry on (default all exceptions)
        *args: Positional arguments for fn
        **kwargs: Keyword arguments for fn

    Returns:
        Return value of fn if successful

    Raises:
        Original exception if max retries exhausted
    """
    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except retry_on as exc:
            if attempt == max_retries:
                raise
            # Exponential backoff: base_delay * 2^attempt + jitter
            delay = min(base_delay * (2 ** attempt) + random.uniform(0, 2), max_delay)
            logger.warning(
                "Retry %d/%d for %s after %.1fs: %s",
                attempt + 1,
                max_retries,
                fn.__name__,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
