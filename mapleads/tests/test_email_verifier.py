import pytest

from backend.scraper.email_verifier import verify_email_mx


@pytest.mark.asyncio
async def test_valid_domain_gmail():
    result = await verify_email_mx("test@gmail.com")
    assert result == "valid"


@pytest.mark.asyncio
async def test_valid_domain_google():
    result = await verify_email_mx("test@google.com")
    assert result == "valid"


@pytest.mark.asyncio
async def test_invalid_invented_domain():
    result = await verify_email_mx("test@dominio-inventado-xyz123456.com")
    assert result == "invalid"


@pytest.mark.asyncio
async def test_invalid_empty_email():
    result = await verify_email_mx("")
    assert result == "invalid"


@pytest.mark.asyncio
async def test_invalid_malformed_email():
    result = await verify_email_mx("not-an-email")
    assert result == "invalid"
