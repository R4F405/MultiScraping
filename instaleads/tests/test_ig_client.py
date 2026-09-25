"""Transport-level behaviour of ig_client: mobile identity and how
Instagram's error payloads are classified (Sept 2026 formats)."""
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config.settings import Settings
from backend.scraper import ig_client, ig_mobile
from backend.scraper.ig_client import IgAuthError, IgChallengeError
from backend.scraper.ig_session import IgSession, web_user_agent


def _resp(status: int, payload: dict | None):
    r = MagicMock()
    r.status_code = status
    if payload is None:
        r.json.side_effect = ValueError("not json")
    else:
        r.json.return_value = payload
    return r


@pytest.fixture
def limiter():
    lim = MagicMock()
    lim.check_and_wait = AsyncMock()
    lim.on_rate_limited = AsyncMock()
    return lim


async def _run(responses, limiter, require_auth=True):
    session = IgSession("111%3Atok%3A1")
    with patch.object(ig_client.curl_requests, "request", side_effect=responses):
        return await ig_client._ig_request(
            "https://i.instagram.com/api/v1/x/", session=session, max_retries=len(responses),
            require_auth=require_auth, headers={}, limiter=limiter,
        )


def test_mobile_headers_carry_bearer_from_sessionid():
    session = IgSession("123456%3AabcToken%3A9")
    headers = ig_mobile.mobile_headers(session)
    assert headers["X-IG-App-ID"] == Settings.IG_MOBILE_APP_ID
    assert headers["User-Agent"].startswith(f"Instagram {Settings.IG_MOBILE_APP_VERSION} Android (")
    assert headers["IG-U-DS-USER-ID"] == "123456"
    token = headers["Authorization"]
    assert token.startswith("Bearer IGT:2:")
    payload = json.loads(base64.b64decode(token.split(":", 2)[2]))
    assert payload == {"ds_user_id": "123456", "sessionid": "123456%3AabcToken%3A9",
                       "should_use_header_over_cookies": True}
    # Same account → same device ids across calls/restarts.
    assert headers["X-IG-Device-ID"] == ig_mobile.mobile_headers(session)["X-IG-Device-ID"]
    assert ig_mobile.rank_token(session) == f"123456_{headers['X-IG-Device-ID']}"


def test_web_user_agent_matches_tls_profile(monkeypatch):
    monkeypatch.setattr(Settings, "IG_IMPERSONATE", "chrome146")
    assert "Chrome/146.0.0.0" in web_user_agent()
    monkeypatch.setattr(Settings, "IG_IMPERSONATE", "none")
    assert "Chrome/" in web_user_agent()


@pytest.mark.asyncio
async def test_localized_wait_message_is_throttle_not_auth(limiter):
    """401 + require_login + "Espera unos minutos" is a rate limit: back off,
    don't declare the session dead."""
    wait = {"message": "Espera unos minutos antes de volver a intentarlo.",
            "require_login": True, "status": "fail"}
    ok = {"users": [], "status": "ok"}
    data = await _run([_resp(401, wait), _resp(200, ok)], limiter)
    assert data == ok
    limiter.on_rate_limited.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_required_raises_auth_error(limiter):
    with pytest.raises(IgAuthError):
        await _run([_resp(403, {"message": "login_required", "status": "fail"})], limiter)


@pytest.mark.asyncio
async def test_challenge_raises_challenge_error(limiter):
    payload = {"message": "challenge_required", "challenge": {"url": "https://i.instagram.com/challenge/x"},
               "status": "fail"}
    with pytest.raises(IgChallengeError):
        await _run([_resp(400, payload)], limiter)


@pytest.mark.asyncio
async def test_private_and_not_found(limiter):
    data = await _run([_resp(400, {"message": "Not authorized to view user", "status": "fail"})], limiter)
    assert data["error"] == "private"
    data = await _run([_resp(404, None)], limiter)
    assert data["error"] == "not_found"


@pytest.mark.asyncio
async def test_daily_limit_propagates(limiter):
    from backend.scraper.ig_rate_limiter import DailyLimitReached

    limiter.check_and_wait.side_effect = DailyLimitReached("cap")
    with pytest.raises(DailyLimitReached):
        await _run([_resp(200, {"status": "ok"})], limiter)


@pytest.mark.asyncio
async def test_mobile_get_builds_url_and_uses_enrich_limiter(monkeypatch):
    captured = {}

    async def fake_request(url, **kwargs):
        captured.update(kwargs, url=url)
        return {"status": "ok"}

    monkeypatch.setattr(ig_client, "_ig_request", fake_request)
    session = IgSession("111%3Atok%3A1")
    await ig_client.ig_mobile_get("users/42/info/", params={"a": 1}, session=session, purpose="enrich")
    assert captured["url"] == "https://i.instagram.com/api/v1/users/42/info/"
    assert captured["limiter"] is ig_client._enrich_rate_limiter
    assert captured["headers"]["Authorization"].startswith("Bearer IGT:2:")
    assert captured["require_auth"] is True
