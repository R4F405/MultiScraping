import pytest

from backend.scraper import ig_session
from backend.scraper.ig_session import IgSession, load_session


def test_parse_ds_user_id_from_plain_sessionid():
    s = IgSession("12345678:abcdefLONGTOKEN:9")
    assert s.ds_user_id == "12345678"
    assert s.authenticated is True


def test_parse_ds_user_id_from_urlencoded_sessionid():
    # Browsers store sessionid url-encoded: ':' → '%3A'
    s = IgSession("987654321%3AsomeToken%3A17%3AAbCd")
    assert s.ds_user_id == "987654321"


def test_session_cookies_and_headers():
    s = IgSession("111%3Atok", csrftoken="csrf123", mid="MIDVAL")
    cookies = s.cookies()
    assert cookies["sessionid"] == "111%3Atok"
    assert cookies["csrftoken"] == "csrf123"
    assert cookies["ds_user_id"] == "111"
    assert cookies["mid"] == "MIDVAL"

    headers = s.headers()
    assert headers["x-csrftoken"] == "csrf123"
    assert headers["x-ig-app-id"]
    assert "instagram.com" in headers["Referer"]


def test_missing_csrftoken_defaults():
    s = IgSession("111%3Atok")
    assert s.csrftoken == "missing"
    assert s.cookies()["csrftoken"] == "missing"


def test_load_session_from_env(monkeypatch):
    monkeypatch.setenv("IG_SESSIONID", "42%3Atoken%3A1")
    monkeypatch.setenv("IG_CSRFTOKEN", "envcsrf")
    monkeypatch.delenv("IG_SESSION_FILE", raising=False)
    s = load_session()
    assert s is not None
    assert s.ds_user_id == "42"
    assert s.csrftoken == "envcsrf"


def test_load_session_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("IG_SESSIONID", raising=False)
    monkeypatch.delenv("IG_SESSION_FILE", raising=False)
    assert load_session() is None


def test_load_session_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("IG_SESSIONID", raising=False)
    monkeypatch.delenv("IG_CSRFTOKEN", raising=False)
    monkeypatch.delenv("IG_DS_USER_ID", raising=False)
    session_file = tmp_path / "sess.json"
    session_file.write_text('{"sessionid": "55%3Aabc%3A2", "csrftoken": "filecsrf"}')
    monkeypatch.setenv("IG_SESSION_FILE", str(session_file))
    s = load_session()
    assert s is not None
    assert s.ds_user_id == "55"
    assert s.csrftoken == "filecsrf"


def test_reload_session_refreshes_singleton(monkeypatch):
    monkeypatch.delenv("IG_SESSIONID", raising=False)
    monkeypatch.delenv("IG_SESSION_FILE", raising=False)
    ig_session._session = None
    ig_session._loaded = False
    assert ig_session.get_session() is None

    monkeypatch.setenv("IG_SESSIONID", "77%3Atok%3A3")
    # get_session is cached; reload picks up the new env
    assert ig_session.get_session() is None
    s = ig_session.reload_session()
    assert s is not None and s.ds_user_id == "77"
