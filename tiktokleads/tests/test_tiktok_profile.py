from backend.scraper.tiktok_profile import _confidence, _extract_contacts_from_bio, _lead_has_persistable_email


def test_lead_has_persistable_email():
    assert _lead_has_persistable_email({"email": "a@b.co", "phone": None, "website": None})
    assert not _lead_has_persistable_email({"email": None, "phone": "+34 600 000 000", "website": None})
    assert not _lead_has_persistable_email({"email": None, "phone": None, "website": "https://x.com"})
    assert not _lead_has_persistable_email({"email": None, "phone": "", "website": None})
    assert not _lead_has_persistable_email({"email": "  ", "phone": None, "website": None})


def test_extract_contacts_from_bio():
    email, phone, website = _extract_contacts_from_bio(
        "hola contacto foo@example.com +34 612 345 678 https://acme.test"
    )
    assert email == "foo@example.com"
    assert "+34 612 345 678" in phone
    assert website == "https://acme.test"


def test_confidence_scoring():
    assert _confidence(None, None, None, "none") < _confidence("a@b.com", None, None, "bio_email")
