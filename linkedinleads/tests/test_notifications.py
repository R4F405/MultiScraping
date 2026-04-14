"""
Tests del módulo notifications: Telegram.
No hace peticiones reales a la API de Telegram — se mockea urllib.request.urlopen.
"""
import json
from unittest.mock import MagicMock, patch

import pytest


def _make_mock_response(status: int = 200):
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ── is_enabled ────────────────────────────────────────────────────────────────

def test_is_enabled_sin_token():
    import notifications as n
    with patch.object(n, "_BOT_TOKEN", ""):
        with patch.object(n, "_CHAT_ID", "123"):
            with patch.object(n, "_ENABLED", False):
                assert n.is_enabled() is False


def test_is_enabled_con_token_y_chat():
    import notifications as n
    with patch.object(n, "_BOT_TOKEN", "tok"):
        with patch.object(n, "_CHAT_ID", "123"):
            with patch.object(n, "_ENABLED", True):
                assert n.is_enabled() is True


# ── _send ────────────────────────────────────────────────────────────────────

def test_send_deshabilitado_no_hace_peticion():
    import notifications as n
    with patch.object(n, "_ENABLED", False):
        with patch("urllib.request.urlopen") as mock_open:
            result = n._send("hola")
    assert result is False
    mock_open.assert_not_called()


def test_send_habilitado_envia_peticion():
    import notifications as n
    with patch.object(n, "_ENABLED", True):
        with patch.object(n, "_BOT_TOKEN", "TOKEN"):
            with patch.object(n, "_CHAT_ID", "123"):
                mock_resp = _make_mock_response(200)
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    result = n._send("mensaje de prueba")
    assert result is True


def test_send_error_no_lanza_excepcion():
    """_send nunca debe lanzar excepciones hacia el llamador."""
    import notifications as n
    with patch.object(n, "_ENABLED", True):
        with patch.object(n, "_BOT_TOKEN", "TOKEN"):
            with patch.object(n, "_CHAT_ID", "123"):
                with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
                    result = n._send("fallo de red")
    assert result is False


# ── notify_session_expired ────────────────────────────────────────────────────

def test_notify_session_expired_incluye_username():
    import notifications as n
    sent_texts = []
    with patch.object(n, "_send", side_effect=lambda t: sent_texts.append(t) or True):
        n.notify_session_expired("miquel-roca")
    assert len(sent_texts) == 1
    assert "miquel-roca" in sent_texts[0]


def test_notify_session_expired_sin_cuenta():
    import notifications as n
    sent_texts = []
    with patch.object(n, "_send", side_effect=lambda t: sent_texts.append(t) or True):
        n.notify_session_expired(None)
    assert "cuenta principal" in sent_texts[0]


# ── notify_block ──────────────────────────────────────────────────────────────

def test_notify_block_menciona_cooldown():
    import notifications as n
    sent_texts = []
    with patch.object(n, "_send", side_effect=lambda t: sent_texts.append(t) or True):
        n.notify_block("miquel-roca", cooldown_hours=48)
    assert "48" in sent_texts[0]
    assert "miquel-roca" in sent_texts[0]


# ── notify_daily_summary ──────────────────────────────────────────────────────

def test_notify_daily_summary_con_actividad():
    import notifications as n
    sent_texts = []
    with patch.object(n, "_send", side_effect=lambda t: sent_texts.append(t) or True):
        n.notify_daily_summary("miquel", 10, 5, 3, 0, 120)
    assert len(sent_texts) == 1
    assert "10" in sent_texts[0]  # nuevos
    assert "120" in sent_texts[0]  # pendientes


def test_notify_daily_summary_sin_actividad_no_envia():
    """Si no se procesó nada, no enviar para evitar spam."""
    import notifications as n
    with patch.object(n, "_send") as mock_send:
        n.notify_daily_summary("miquel", 0, 0, 5, 0, 120)
    mock_send.assert_not_called()


# ── notify_index_complete ─────────────────────────────────────────────────────

def test_notify_index_complete():
    import notifications as n
    sent_texts = []
    with patch.object(n, "_send", side_effect=lambda t: sent_texts.append(t) or True):
        n.notify_index_complete("miquel", total_slugs=250, new_queued=18)
    assert "250" in sent_texts[0]
    assert "18" in sent_texts[0]
