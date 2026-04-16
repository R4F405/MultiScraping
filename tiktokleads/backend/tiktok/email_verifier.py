import asyncio
import logging

import dns.resolver

logger = logging.getLogger(__name__)


async def verify_email_mx(email: str) -> str:
    """
    Verify that the domain of an email has valid MX records.

    Returns:
        "valid"   — domain has MX records
        "invalid" — domain has no MX records or DNS lookup failed
    """
    if not email or "@" not in email:
        return "invalid"

    domain = email.split("@")[1].lower()

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: dns.resolver.resolve(domain, "MX"),
        )
        logger.debug("verify_email_mx: %s → valid", email)
        return "valid"
    except Exception as exc:
        logger.debug("verify_email_mx: %s → invalid (%s)", email, exc)
        return "invalid"
