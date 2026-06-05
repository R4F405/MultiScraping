import re

EMAIL_LIKE_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)

STRONG_HINTS: tuple[str, ...] = (
    "contact",
    "email",
    "gmail",
    "outlook",
    "yahoo",
    "whatsapp",
    "wa.me",
    "linktr.ee",
    "linktree",
    "booking",
    "collab",
    "business",
    "press",
    "media@",
    "info@",
    "hello@",
)


def email_likelihood_score(item: dict) -> int:
    """Heurística ligera para priorizar candidatos con más probabilidad de email."""
    desc = str(item.get("description") or "").lower()
    hashtags = item.get("hashtags") or []
    tags_text = " ".join(str(x).lower() for x in hashtags if x is not None)
    text = f"{desc} {tags_text}".strip()
    if not text:
        return 0

    score = 0
    if EMAIL_LIKE_RE.search(text):
        score += 10
    if "@" in text:
        score += 3

    for hint in STRONG_HINTS:
        if hint in text:
            score += 2

    return score


def sort_discovery_items_by_email_likelihood(items: list[dict]) -> list[dict]:
    """Orden estable: mejor score primero, desempate por handle."""
    return sorted(
        items,
        key=lambda item: (
            -email_likelihood_score(item),
            str(item.get("author_handle") or "").lower(),
        ),
    )
