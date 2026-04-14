from unittest.mock import AsyncMock, patch

import pytest

from backend.instagram.ig_enrichment import extract_emails_from_text, enrich_email_from_bio_url


def test_extract_emails_from_text_normalizes_and_dedupes():
    html = """
    Contact: SALES@Acme.com
    mailto:sales@acme.com
    noreply@acme.com
    hello@example.com
    """
    emails = extract_emails_from_text(html)
    assert emails == ["sales@acme.com"]


@pytest.mark.anyio
async def test_enrich_email_from_bio_url_success():
    mock_resp = type("R", (), {"status_code": 200, "text": "write us at team@brand.io"})()
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("backend.instagram.ig_enrichment.httpx.AsyncClient", return_value=mock_client):
        email = await enrich_email_from_bio_url("https://brand.io")
    assert email == "team@brand.io"


@pytest.mark.anyio
async def test_enrich_email_from_bio_url_handles_timeout():
    mock_client = AsyncMock()
    mock_client.get.side_effect = Exception("timeout")
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("backend.instagram.ig_enrichment.httpx.AsyncClient", return_value=mock_client):
        email = await enrich_email_from_bio_url("https://brand.io")
    assert email is None
