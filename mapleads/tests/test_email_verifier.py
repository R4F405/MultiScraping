import pytest

from backend.scraper.email_verifier import verify_email_mx


@pytest.mark.asyncio
async def test_valid_domain_gmail(monkeypatch):
    import backend.scraper.email_verifier as verifier

    def _ok(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(verifier.dns.resolver, "resolve", _ok)
    result = await verify_email_mx("test@gmail.com")
    assert result == "valid"


@pytest.mark.asyncio
async def test_valid_domain_google(monkeypatch):
    import backend.scraper.email_verifier as verifier

    def _ok(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(verifier.dns.resolver, "resolve", _ok)
    result = await verify_email_mx("test@google.com")
    assert result == "valid"


@pytest.mark.asyncio
async def test_invalid_invented_domain(monkeypatch):
    import backend.scraper.email_verifier as verifier

    def _fail(*_args, **_kwargs):
        raise RuntimeError("NXDOMAIN")

    monkeypatch.setattr(verifier.dns.resolver, "resolve", _fail)
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


@pytest.mark.asyncio
async def test_accept_a_record_when_mx_missing(monkeypatch):
    import backend.scraper.email_verifier as verifier

    def _resolve(qname, rtype, *args, **kwargs):
        if rtype == "MX":
            raise RuntimeError("no mx")
        if rtype == "A":
            return object()
        raise RuntimeError("unknown")

    monkeypatch.setattr(verifier.settings, "email_dns_accept_a", True)
    monkeypatch.setattr(verifier.dns.resolver, "resolve", _resolve)
    result = await verify_email_mx("a@only-a-record.example")
    assert result == "valid"


@pytest.mark.asyncio
async def test_no_a_fallback_when_flag_off(monkeypatch):
    import backend.scraper.email_verifier as verifier

    def _resolve(_qname, rtype, *args, **kwargs):
        if rtype == "MX":
            raise RuntimeError("no mx")
        if rtype == "A":
            return object()
        raise RuntimeError("unknown")

    monkeypatch.setattr(verifier.settings, "email_dns_accept_a", False)
    monkeypatch.setattr(verifier.dns.resolver, "resolve", _resolve)
    result = await verify_email_mx("a@only-a-record.example")
    assert result == "invalid"
