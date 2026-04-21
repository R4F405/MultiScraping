import json
import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet


def _make_fernet_key() -> str:
    return Fernet.generate_key().decode()


def test_save_and_load_session_roundtrip():
    from backend.scraper import ig_session

    key = _make_fernet_key()
    fake_settings = {"uuids": ["abc"], "device_settings": {}}

    with tempfile.NamedTemporaryFile(suffix=".enc", delete=False) as tf:
        tmp_path = tf.name

    try:
        with (
            patch.object(ig_session, "SESSION_PATH", tmp_path),
            patch("backend.scraper.ig_session.Settings") as mock_cfg,
        ):
            mock_cfg.IG_SESSION_KEY = key

            cl_save = MagicMock()
            cl_save.get_settings.return_value = fake_settings
            ig_session._save_session(cl_save)

            cl_load = MagicMock()
            cl_load.get_timeline_feed.return_value = True
            result = ig_session._load_session(cl_load)

        assert result is True
        cl_load.set_settings.assert_called_once_with(fake_settings)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_load_session_returns_false_when_file_missing():
    from backend.scraper import ig_session

    with patch.object(ig_session, "SESSION_PATH", "/nonexistent/path.enc"):
        cl = MagicMock()
        result = ig_session._load_session(cl)
    assert result is False


def test_clear_session_removes_file():
    from backend.scraper import ig_session

    with tempfile.NamedTemporaryFile(suffix=".enc", delete=False) as tf:
        tmp_path = tf.name
        tf.write(b"data")

    with patch.object(ig_session, "SESSION_PATH", tmp_path):
        ig_session._client = MagicMock()
        ig_session.clear_session()
        assert not os.path.exists(tmp_path)
        assert ig_session._client is None
