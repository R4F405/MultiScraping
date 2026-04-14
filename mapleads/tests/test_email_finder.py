import pytest

from backend.scraper.email_finder import _extract_emails, EMAIL_REGEX


def test_extract_basic_email():
    html = '<a href="mailto:info@empresa.com">Contacto</a>'
    emails = _extract_emails(html)
    assert "info@empresa.com" in emails


def test_extract_multiple_emails():
    html = "Escríbenos a ventas@empresa.com o soporte@empresa.com"
    emails = _extract_emails(html)
    assert "ventas@empresa.com" in emails
    assert "soporte@empresa.com" in emails


def test_filter_sentry_email():
    html = "error at abc@sentry.io and real@empresa.com"
    emails = _extract_emails(html)
    assert "abc@sentry.io" not in emails
    assert "real@empresa.com" in emails


def test_filter_image_ref():
    html = "background: url(logo@2x.png) and contact@empresa.com"
    emails = _extract_emails(html)
    assert not any("@2x" in e for e in emails)
    assert "contact@empresa.com" in emails


def test_filter_example_com():
    html = "test@example.com is not a real email"
    emails = _extract_emails(html)
    assert "test@example.com" not in emails


def test_returns_deduplicated():
    html = "info@empresa.com info@empresa.com info@empresa.com"
    emails = _extract_emails(html)
    assert emails.count("info@empresa.com") == 1


def test_no_emails_returns_empty():
    html = "<html><body><p>Sin emails aquí</p></body></html>"
    emails = _extract_emails(html)
    assert emails == []


def test_regex_matches_various_formats():
    cases = [
        "user@domain.com",
        "user.name+tag@sub.domain.org",
        "user_123@empresa.es",
    ]
    for email in cases:
        assert EMAIL_REGEX.search(email), f"Should match: {email}"
