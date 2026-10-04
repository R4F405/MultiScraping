import logging
import re
from urllib.parse import parse_qs, quote, urlparse

from backend.scraper.ig_client import IgAuthError, ig_get, ig_mobile_get
from backend.scraper.ig_session import get_enrichment_session
from backend.scraper.email_finder import find_email_in_website

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# "info [at] shop (dot) com" → "info@shop.com". Only bracketed forms, so plain
# words like "at"/"punto" inside a sentence are never rewritten.
_AT_OBFUSCATION = re.compile(r"\s*[\[(]\s*(?:at|arroba)\s*[\])]\s*", re.I)
_DOT_OBFUSCATION = re.compile(r"\s*[\[(]\s*(?:dot|punto)\s*[\])]\s*", re.I)

# Phone candidates: optional +/00 prefix, 9-15 digits once separators go.
_PHONE_CANDIDATE = re.compile(r"(?<![\w+])(?:\+|00)?\d[\d\s().\-]{6,20}\d(?!\w)")
# Without an explicit international prefix a number is only trusted when a
# keyword/emoji right before it says it is a phone (avoids dates, counters…).
_PHONE_HINT = re.compile(
    r"(tel|tlf|tfno|tel[eé]fono|phone|m[oó]vil|cel|whats|wsp|wpp|llama|call|contact|📞|☎|📱|📲)",
    re.I,
)
_WHATSAPP_HOSTS = {"wa.me", "api.whatsapp.com", "whatsapp.com", "www.whatsapp.com", "chat.whatsapp.com"}

# Domains that appear in bios/business fields but are never real contact emails
_JUNK_EMAIL_DOMAINS = {
    "linktr.ee", "beacons.ai", "solo.to", "bio.site",
    "example.com", "sampleemail.com", "noreply.com",
}

PROFILE_URL = "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"


def _extract_follower_count(user: dict) -> int:
    """Normalize follower count across Instagram payload variants."""
    direct_value = user.get("follower_count")
    if isinstance(direct_value, int):
        return max(direct_value, 0)

    edge_followed_by = user.get("edge_followed_by")
    if isinstance(edge_followed_by, dict):
        edge_count = edge_followed_by.get("count")
        if isinstance(edge_count, int):
            return max(edge_count, 0)

    followers_count = user.get("followers_count")
    if isinstance(followers_count, int):
        return max(followers_count, 0)

    return 0


class _Throttled(Exception):
    """Mobile lookup gave up on HTTP 429 — skip the web fallback."""


async def _fetch_mobile_user(username: str, user_id: str | None, session) -> dict | None:
    """Full user object from the Android API — the only 2026 surface that
    still returns ``public_email`` / ``public_phone_number`` /
    ``contact_phone_number``. By id when known (``users/{id}/info/``), else by
    username (``users/{username}/usernameinfo/``)."""
    if user_id:
        data = await ig_mobile_get(
            f"users/{user_id}/info/",
            params={
                "is_prefetch": "false",
                "entry_point": "profile",
                "from_module": "feed_timeline",
                "is_app_start": "false",
            },
            session=session,
            purpose="enrich",
        )
    else:
        data = await ig_mobile_get(f"users/{quote(username)}/usernameinfo/", session=session, purpose="enrich")

    if data.get("error") == "private":
        return {"pk": user_id, "username": username, "is_private": True}
    if data.get("error"):
        logger.debug("get_profile(%s): mobile fetch error — %s", username, data.get("error"))
        if data.get("status_code") == 429:
            # The account is being throttled: hitting web_profile_info right
            # away would only add another request to the same limit.
            raise _Throttled()
        return None
    return data.get("user") or None


async def _fetch_web_user(username: str, session) -> dict | None:
    data = await ig_get(PROFILE_URL.format(username=quote(username)), session=session)
    if "error" in data:
        logger.debug("get_profile(%s): web fetch error — %s", username, data.get("error"))
        return None
    user = (data.get("data") or {}).get("user")
    if not user:
        logger.debug("get_profile(%s): no user in response", username)
        return None
    return user


async def get_profile(
    username: str,
    user_id: str | None = None,
    *,
    mobile: bool = False,
    strict_auth: bool = False,
) -> dict | None:
    """Fetch an Instagram profile and extract contact fields (email, phone…).

    Uses the guest account (Fase 2 enrichment session) when one is
    configured, so the high-volume profile-checking traffic never rides on
    the main account that pulled the followers list. Falls back to the main
    session, then to fully anonymous, when no guest account is set up.

    ``mobile=True`` (followers enrichment) asks the Android API first and
    falls back to ``web_profile_info``. ``strict_auth=True`` lets
    :class:`IgAuthError` propagate instead of silently degrading to web, so
    the caller can stop when the session dies.

    Returns None for fetch errors. Private profiles return ``private: True``.
    The ``email``/``phone`` fields may be None if nothing was found anywhere.
    """
    session = get_enrichment_session()
    user = None
    if mobile and session is not None and session.authenticated:
        try:
            user = await _fetch_mobile_user(username, user_id, session)
        except _Throttled:
            return None
        except IgAuthError:
            if strict_auth:
                raise
            logger.warning("get_profile(%s): mobile session rejected — falling back to web", username)
    if user is None:
        user = await _fetch_web_user(username, session)
    if user is None:
        return None

    instagram_id = str(user.get("pk") or user.get("id") or user_id or "") or None

    if user.get("is_private"):
        logger.debug("get_profile(%s): private profile — skipping", username)
        return {"username": username, "instagram_id": instagram_id, "private": True, "email": None, "phone": None}

    email, email_source = _extract_email(user)
    phone, phone_source = _extract_phone(user)
    website = _extract_website(user)

    # Fallback: scrape the linked website for an email.
    if not email and website and not _is_whatsapp_url(website):
        email, email_source = await _email_from_website(website)

    return {
        "instagram_id": instagram_id,
        "username": user.get("username") or username,
        "full_name": user.get("full_name"),
        "email": email,
        "email_source": email_source,
        "phone": phone,
        "phone_source": phone_source,
        "website": website,
        "bio": user.get("biography"),
        "category": (
            user.get("category") or user.get("category_name") or user.get("business_category_name")
        ),
        "city": user.get("city_name") or None,
        "follower_count": _extract_follower_count(user),
        "is_business": bool(
            user.get("is_business") or user.get("is_business_account") or user.get("account_type") in (2, 3)
        ),
        "private": False,
    }


def _is_junk_email(email: str) -> bool:
    domain = email.split("@")[-1].lower()
    return domain in _JUNK_EMAIL_DOMAINS


def _deobfuscate(text: str) -> str:
    return _DOT_OBFUSCATION.sub(".", _AT_OBFUSCATION.sub("@", text))


def _extract_email(user: dict) -> tuple[str | None, str | None]:
    # public_email: mobile API (the "Email" button of business/creator
    # profiles). business_email: legacy web payload.
    for field, source in (("public_email", "public_email"), ("business_email", "business_field")):
        value = (user.get(field) or "").strip()
        if value and not _is_junk_email(value):
            return value, source

    bio = _deobfuscate(user.get("biography") or "")
    for match in EMAIL_REGEX.findall(bio):
        if not _is_junk_email(match):
            return match.rstrip("."), "bio_regex"

    return None, None


def _clean_phone(raw: str) -> str | None:
    raw = (raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not 7 <= len(digits) <= 15:
        return None
    if raw.startswith("+"):
        return "+" + digits
    if raw.startswith("00"):
        return "+" + digits[2:]
    return digits


def _is_whatsapp_url(url: str) -> bool:
    try:
        return (urlparse(url).hostname or "").lower() in _WHATSAPP_HOSTS
    except ValueError:
        return False


def _phone_from_whatsapp_url(url: str) -> str | None:
    """wa.me/34612345678 or api.whatsapp.com/send?phone=34612345678 → +34612345678."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return None
    if (parsed.hostname or "").lower() not in _WHATSAPP_HOSTS:
        return None
    number = (parse_qs(parsed.query).get("phone") or [""])[0]
    if not number and (parsed.hostname or "").lower() == "wa.me":
        number = parsed.path.strip("/").split("/")[0]
    digits = re.sub(r"\D", "", number)
    return "+" + digits if 8 <= len(digits) <= 15 else None


def _bio_link_urls(user: dict) -> list[str]:
    urls = [user.get("external_url") or ""]
    for link in user.get("bio_links") or []:
        if isinstance(link, dict):
            urls.append(link.get("url") or link.get("lynx_url") or "")
    return [u for u in urls if u]


def _extract_website(user: dict) -> str | None:
    urls = _bio_link_urls(user)
    for url in urls:
        if not _is_whatsapp_url(url):
            return url
    return urls[0] if urls else None


def _extract_phone(user: dict) -> tuple[str | None, str | None]:
    # Mobile API: the "Call"/"Text" button of business profiles.
    number = (user.get("public_phone_number") or "").strip()
    if number:
        cc = re.sub(r"\D", "", str(user.get("public_phone_country_code") or ""))
        digits = re.sub(r"\D", "", number)
        phone = _clean_phone(f"+{cc}{digits}" if cc and not digits.startswith(cc) else number)
        if phone:
            return phone, "public_phone"

    for field, source in (("contact_phone_number", "contact_phone"), ("business_phone_number", "business_field")):
        phone = _clean_phone(user.get(field) or "")
        if phone:
            return phone, source

    for url in _bio_link_urls(user):
        phone = _phone_from_whatsapp_url(url)
        if phone:
            return phone, "whatsapp_link"

    bio = user.get("biography") or ""
    for match in re.finditer(r"(?:https?://)?(?:wa\.me|api\.whatsapp\.com)/\S+", bio):
        phone = _phone_from_whatsapp_url(match.group(0))
        if phone:
            return phone, "whatsapp_link"

    for match in _PHONE_CANDIDATE.finditer(bio):
        raw = match.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if not 9 <= len(digits) <= 15:
            continue
        explicit_prefix = raw.startswith(("+", "00"))
        hinted = bool(_PHONE_HINT.search(bio[max(0, match.start() - 25):match.start()]))
        if explicit_prefix or hinted:
            phone = _clean_phone(raw)
            if phone:
                return phone, "bio_regex"

    return None, None


async def _email_from_website(url: str) -> tuple[str | None, str | None]:
    try:
        emails = await find_email_in_website(url)
        if emails:
            return emails[0], "website_scrape"
    except Exception as e:
        logger.debug("website scrape failed for %s: %s", url, e)
    return None, None
