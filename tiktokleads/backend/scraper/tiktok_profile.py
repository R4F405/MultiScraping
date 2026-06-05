import re

from backend.storage import database as db

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"\+?\d[\d\s-]{7,}\d")
WEB_RE = re.compile(r"https?://[^\s)]+", re.IGNORECASE)


def _confidence(email: str | None, phone: str | None, website: str | None, source: str) -> float:
    score = 0.15
    if email:
        score += 0.6
    if phone:
        score += 0.15
    if website:
        score += 0.1
    if source == "website_email":
        score += 0.05
    elif source == "bio_email":
        score += 0.03
    return min(1.0, score)


def _extract_contacts_from_bio(bio: str) -> tuple[str | None, str | None, str | None]:
    email_match = EMAIL_RE.search(bio or "")
    phone_match = PHONE_RE.search(bio or "")
    web_match = WEB_RE.search(bio or "")
    return (
        email_match.group(0) if email_match else None,
        phone_match.group(0) if phone_match else None,
        web_match.group(0) if web_match else None,
    )


def _lead_has_persistable_email(lead: dict) -> bool:
    e = lead.get("email")
    return bool(e and str(e).strip())


async def enrich_item(client, item: dict, job_id: str) -> tuple[dict, str]:
    profile = await client.enrich_author(item["author_id"], item["author_handle"])
    bio_email, bio_phone, bio_website = _extract_contacts_from_bio(profile.get("bio", ""))
    email = profile.get("email") or bio_email
    phone = profile.get("phone") or bio_phone
    website = profile.get("website") or bio_website
    contact_source = "none"
    if profile.get("email"):
        contact_source = "website_email" if website else "bio_email"
    elif bio_email:
        contact_source = "bio_email"
    elif phone:
        contact_source = "phone"
    elif website:
        contact_source = "website"

    lead = {
        "author_id": profile["author_id"],
        "handle": profile["handle"],
        "email": email,
        "phone": phone,
        "website": website,
        "source_confidence": _confidence(email, phone, website, contact_source),
    }
    if _lead_has_persistable_email(lead):
        await db.upsert_profile(profile)
        await db.upsert_lead(job_id, lead)
    return lead, contact_source
