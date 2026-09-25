import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_get_profile_extracts_business_email():
    from backend.scraper.ig_profile import get_profile

    fake_response = {
        "data": {
            "user": {
                "id": "123",
                "username": "testuser",
                "full_name": "Test User",
                "biography": "No email here",
                "business_email": "business@testuser.com",
                "business_phone_number": None,
                "external_url": None,
                "follower_count": 1000,
                "is_business_account": True,
                "is_private": False,
            }
        }
    }
    with patch("backend.scraper.ig_profile.ig_get", new=AsyncMock(return_value=fake_response)):
        profile = await get_profile("testuser")

    assert profile is not None
    assert profile["email"] == "business@testuser.com"
    assert profile["email_source"] == "business_field"


@pytest.mark.asyncio
async def test_get_profile_extracts_bio_email():
    from backend.scraper.ig_profile import get_profile

    fake_response = {
        "data": {
            "user": {
                "id": "456",
                "username": "biouser",
                "full_name": "Bio User",
                "biography": "Contact me: hello@biouser.com",
                "business_email": None,
                "business_phone_number": None,
                "external_url": None,
                "follower_count": 500,
                "is_business_account": False,
                "is_private": False,
            }
        }
    }
    with patch("backend.scraper.ig_profile.ig_get", new=AsyncMock(return_value=fake_response)):
        profile = await get_profile("biouser")

    assert profile["email"] == "hello@biouser.com"
    assert profile["email_source"] == "bio_regex"


@pytest.mark.asyncio
async def test_get_profile_returns_none_for_private():
    from backend.scraper.ig_profile import get_profile

    fake_response = {
        "data": {
            "user": {
                "id": "789",
                "username": "privateuser",
                "is_private": True,
            }
        }
    }
    with patch("backend.scraper.ig_profile.ig_get", new=AsyncMock(return_value=fake_response)):
        profile = await get_profile("privateuser")

    assert profile["private"] is True
    assert profile["email"] is None


@pytest.mark.asyncio
async def test_get_profile_returns_none_email_when_no_email():
    from backend.scraper.ig_profile import get_profile

    fake_response = {
        "data": {
            "user": {
                "id": "000",
                "username": "noemailuser",
                "full_name": "No Email",
                "biography": "Just a regular bio without contact info",
                "business_email": None,
                "business_phone_number": None,
                "external_url": None,
                "follower_count": 200,
                "is_business_account": False,
                "is_private": False,
            }
        }
    }
    with patch("backend.scraper.ig_profile.ig_get", new=AsyncMock(return_value=fake_response)):
        profile = await get_profile("noemailuser")

    assert profile["email"] is None


@pytest.mark.asyncio
async def test_get_profile_returns_none_on_fetch_error():
    from backend.scraper.ig_profile import get_profile

    with patch(
        "backend.scraper.ig_profile.ig_get",
        new=AsyncMock(return_value={"error": "max_retries_exceeded"}),
    ):
        profile = await get_profile("erroruser")

    assert profile is None


@pytest.mark.asyncio
async def test_get_profile_extracts_followers_from_edge_followed_by():
    from backend.scraper.ig_profile import get_profile

    fake_response = {
        "data": {
            "user": {
                "id": "321",
                "username": "edgefollowers",
                "full_name": "Edge Followers",
                "biography": "No email here",
                "business_email": None,
                "business_phone_number": None,
                "external_url": None,
                "edge_followed_by": {"count": 4321},
                "is_business_account": False,
                "is_private": False,
            }
        }
    }
    with patch("backend.scraper.ig_profile.ig_get", new=AsyncMock(return_value=fake_response)):
        profile = await get_profile("edgefollowers")

    assert profile is not None
    assert profile["follower_count"] == 4321


# ── Mobile API (Sept 2026): contact fields ───────────────────────────────────

class _Sess:
    authenticated = True
    ds_user_id = "111"
    sessionid = "111%3Atok"


def _mobile_user(**over):
    user = {
        "pk": 42,
        "username": "tienda",
        "full_name": "Tienda",
        "is_private": False,
        "biography": "",
        "external_url": "",
        "follower_count": 900,
        "is_business": True,
        "category": "Tienda de ropa",
        "city_name": "Madrid",
    }
    user.update(over)
    return {"user": user, "status": "ok"}


@pytest.mark.asyncio
async def test_mobile_profile_extracts_public_email_and_phone():
    from backend.scraper import ig_profile

    fake = AsyncMock(return_value=_mobile_user(
        public_email="hola@tienda.es",
        public_phone_country_code="34",
        public_phone_number="612 345 678",
    ))
    with (
        patch.object(ig_profile, "get_enrichment_session", return_value=_Sess()),
        patch.object(ig_profile, "ig_mobile_get", new=fake),
        patch.object(ig_profile, "ig_get", new=AsyncMock(side_effect=AssertionError("no web"))),
    ):
        profile = await ig_profile.get_profile("tienda", user_id="42", mobile=True)

    endpoint = fake.await_args.args[0]
    assert endpoint == "users/42/info/"
    assert fake.await_args.kwargs["purpose"] == "enrich"
    assert profile["email"] == "hola@tienda.es"
    assert profile["email_source"] == "public_email"
    assert profile["phone"] == "+34612345678"
    assert profile["phone_source"] == "public_phone"
    assert profile["category"] == "Tienda de ropa"
    assert profile["city"] == "Madrid"
    assert profile["is_business"] is True
    assert profile["instagram_id"] == "42"


@pytest.mark.asyncio
async def test_mobile_profile_contact_phone_and_whatsapp_link():
    from backend.scraper import ig_profile

    with (
        patch.object(ig_profile, "get_enrichment_session", return_value=_Sess()),
        patch.object(ig_profile, "ig_mobile_get", new=AsyncMock(return_value=_mobile_user(
            contact_phone_number="+52 55 1234 5678",
        ))),
    ):
        profile = await ig_profile.get_profile("tienda", user_id="42", mobile=True)
    assert profile["phone"] == "+525512345678"
    assert profile["phone_source"] == "contact_phone"

    with (
        patch.object(ig_profile, "get_enrichment_session", return_value=_Sess()),
        patch.object(ig_profile, "ig_mobile_get", new=AsyncMock(return_value=_mobile_user(
            external_url="https://wa.me/34699111222",
            bio_links=[{"url": "https://mitienda.es"}],
        ))),
        patch.object(ig_profile, "find_email_in_website", new=AsyncMock(return_value=[])),
    ):
        profile = await ig_profile.get_profile("tienda", user_id="42", mobile=True)
    assert profile["phone"] == "+34699111222"
    assert profile["phone_source"] == "whatsapp_link"
    # The website is the real site, not the WhatsApp link.
    assert profile["website"] == "https://mitienda.es"


@pytest.mark.asyncio
async def test_mobile_private_profile():
    from backend.scraper import ig_profile

    with (
        patch.object(ig_profile, "get_enrichment_session", return_value=_Sess()),
        patch.object(ig_profile, "ig_mobile_get", new=AsyncMock(return_value=_mobile_user(is_private=True))),
    ):
        profile = await ig_profile.get_profile("tienda", user_id="42", mobile=True)
    assert profile["private"] is True
    assert profile["email"] is None


@pytest.mark.asyncio
async def test_mobile_failure_falls_back_to_web():
    from backend.scraper import ig_profile

    web = {"data": {"user": {"id": "42", "username": "tienda", "is_private": False,
                             "biography": "", "business_email": "web@tienda.es"}}}
    with (
        patch.object(ig_profile, "get_enrichment_session", return_value=_Sess()),
        patch.object(ig_profile, "ig_mobile_get", new=AsyncMock(return_value={"error": "max_retries_exceeded"})),
        patch.object(ig_profile, "ig_get", new=AsyncMock(return_value=web)),
    ):
        profile = await ig_profile.get_profile("tienda", user_id="42", mobile=True)
    assert profile["email"] == "web@tienda.es"
    assert profile["email_source"] == "business_field"


@pytest.mark.asyncio
async def test_strict_auth_propagates_dead_session():
    from backend.scraper import ig_profile
    from backend.scraper.ig_client import IgAuthError

    with (
        patch.object(ig_profile, "get_enrichment_session", return_value=_Sess()),
        patch.object(ig_profile, "ig_mobile_get", new=AsyncMock(side_effect=IgAuthError("login_required"))),
        patch.object(ig_profile, "ig_get", new=AsyncMock(side_effect=AssertionError("no web"))),
    ):
        with pytest.raises(IgAuthError):
            await ig_profile.get_profile("tienda", user_id="42", mobile=True, strict_auth=True)


def test_bio_phone_and_email_extraction():
    from backend.scraper.ig_profile import _extract_email, _extract_phone

    assert _extract_phone({"biography": "📞 Reservas: 961 234 567"}) == ("961234567", "bio_regex")
    assert _extract_phone({"biography": "Pedidos +34 612-345-678 🍕"}) == ("+34612345678", "bio_regex")
    assert _extract_phone({"biography": "WhatsApp 👉 wa.me/5491122334455"}) == ("+5491122334455", "whatsapp_link")
    # Numbers without prefix or phone hint are not trusted (counters, dates, ids…).
    assert _extract_phone({"biography": "Desde 2015 · 123456789 seguidores felices"}) == (None, None)
    assert _extract_phone({"biography": "Temporadas 2019-2024"}) == (None, None)

    assert _extract_email({"biography": "info [at] estudio (dot) com"}) == ("info@estudio.com", "bio_regex")
    assert _extract_email({"public_email": "", "biography": "sin contacto"}) == (None, None)


@pytest.mark.asyncio
async def test_mobile_throttle_does_not_hit_web():
    from backend.scraper import ig_profile

    with (
        patch.object(ig_profile, "get_enrichment_session", return_value=_Sess()),
        patch.object(ig_profile, "ig_mobile_get",
                     new=AsyncMock(return_value={"error": "max_retries_exceeded", "status_code": 429})),
        patch.object(ig_profile, "ig_get", new=AsyncMock(side_effect=AssertionError("no web"))),
    ):
        assert await ig_profile.get_profile("tienda", user_id="42", mobile=True) is None
