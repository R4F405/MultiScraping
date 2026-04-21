import asyncio
import logging

import dns.resolver

from backend.config.settings import settings

logger = logging.getLogger(__name__)


async def verify_email_mx(email: str) -> str:
    """
    Verify that the domain of an email can receive mail (MX preferred).

    When settings.email_dns_accept_a is True, a successful A record lookup
    counts as valid if MX is missing (some small hosts only publish A).

    Returns:
        "valid"   — MX exists (or A when accept_a enabled)
        "invalid" — no usable DNS records or lookup failed
    """
    if not email or "@" not in email:
        return "invalid"

    domain = email.split("@")[1].lower()
    loop = asyncio.get_running_loop()

    def _mx() -> None:
        dns.resolver.resolve(domain, "MX")

    def _a() -> None:
        dns.resolver.resolve(domain, "A")

    try:
        await loop.run_in_executor(None, _mx)
        logger.debug("verify_email_mx: %s → valid (MX)", email)
        return "valid"
    except Exception as mx_exc:
        logger.debug("verify_email_mx: %s no MX (%s)", email, mx_exc)

    if settings.email_dns_accept_a:
        try:
            await loop.run_in_executor(None, _a)
            logger.debug("verify_email_mx: %s → valid (A fallback)", email)
            return "valid"
        except Exception as a_exc:
            logger.debug("verify_email_mx: %s no A (%s)", email, a_exc)

    return "invalid"
