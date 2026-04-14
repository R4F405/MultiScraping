# notifications.py
# Notificaciones por Telegram para el scraper de LinkedIn.
#
# Eventos notificados:
#   · Sesión caducada (la cuenta necesita re-login)
#   · Bloqueo de LinkedIn (on_block / 429 → cooldown activado)
#   · Resumen diario de scraping (contactos nuevos, actualizados, errores)
#
# Configuración (.env):
#   TELEGRAM_BOT_TOKEN  → token del bot (@BotFather)
#   TELEGRAM_CHAT_ID    → ID del chat/canal donde enviar los mensajes
#
# Si alguna de las dos variables está vacía, las notificaciones se deshabilitan
# silenciosamente (nunca lanza excepciones hacia el scraper).

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()

_ENABLED = bool(_BOT_TOKEN and _CHAT_ID)


def _send(text: str) -> bool:
    """
    Envía un mensaje de texto a Telegram.
    Devuelve True si se envió correctamente, False en cualquier error.
    Nunca lanza excepciones.
    """
    if not _ENABLED:
        return False
    try:
        import urllib.request
        import urllib.parse
        import json as _json

        payload = _json.dumps({
            "chat_id": _CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode("utf-8")
        url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = resp.status == 200
        if ok:
            logger.debug("Telegram: mensaje enviado correctamente")
        else:
            logger.warning("Telegram: respuesta inesperada %s", resp.status)
        return ok
    except Exception as e:
        logger.warning("Telegram: error al enviar mensaje: %s", e)
        return False


# ── Eventos específicos ────────────────────────────────────────────────────────

def notify_session_expired(account: Optional[str] = None, auto_retry: bool = False) -> None:
    """
    Notifica que la sesión de una cuenta ha caducado.
    Si auto_retry=True indica que el sistema intentará re-login automáticamente.
    """
    label = f"<b>{account}</b>" if account else "la cuenta principal"
    if auto_retry:
        _send(
            f"⚠️ <b>LinkedIn Scraper — Sesión caducada</b>\n\n"
            f"La sesión de {label} ha expirado.\n"
            f"🔄 Intentando re-login automático con las credenciales guardadas…"
        )
    else:
        _send(
            f"⚠️ <b>LinkedIn Scraper — Sesión caducada</b>\n\n"
            f"La sesión de {label} ha expirado.\n"
            f"No hay credenciales guardadas para hacer login automático.\n"
            f"Inicia sesión manualmente desde la vista del scraper."
        )


def notify_auto_login_ok(account: Optional[str] = None) -> None:
    """Re-login automático completado con éxito — no hace falta intervención manual."""
    label = f"<b>{account}</b>" if account else "la cuenta principal"
    _send(
        f"✅ <b>LinkedIn Scraper — Re-login automático OK</b>\n\n"
        f"La sesión de {label} se ha renovado automáticamente.\n"
        f"El scraper continuará en el próximo ciclo programado."
    )


def notify_auto_login_needs_verification(
    account: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    """
    Re-login automático interrumpido por 2FA / captcha / verificación de email.
    El usuario debe completarlo manualmente.
    """
    label = f"<b>{account}</b>" if account else "la cuenta principal"
    extra = f"\n\n<i>{detail}</i>" if detail else ""
    _send(
        f"🔐 <b>LinkedIn Scraper — Verificación requerida</b>\n\n"
        f"El re-login automático de {label} se detuvo porque LinkedIn\n"
        f"pide verificación adicional (código por email, teléfono o captcha).{extra}\n\n"
        f"Inicia sesión manualmente desde la vista del scraper."
    )


def notify_auto_login_failed(
    account: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Re-login automático fallido (credenciales incorrectas u otro error)."""
    label = f"<b>{account}</b>" if account else "la cuenta principal"
    extra = f"\n\nMotivo: <i>{reason}</i>" if reason else ""
    _send(
        f"❌ <b>LinkedIn Scraper — Re-login automático fallido</b>\n\n"
        f"No se pudo renovar la sesión de {label} automáticamente.{extra}\n\n"
        f"Inicia sesión manualmente desde la vista del scraper."
    )


def notify_block(account: Optional[str] = None, cooldown_hours: int = 48) -> None:
    """
    Notifica que LinkedIn ha bloqueado temporalmente la sesión (429 / on_block).
    """
    label = f"<b>{account}</b>" if account else "la cuenta principal"
    _send(
        f"🚫 <b>LinkedIn Scraper — Cuenta bloqueada</b>\n\n"
        f"LinkedIn ha limitado {label} (error 429 / demasiadas peticiones).\n"
        f"Cooldown activado: <b>{cooldown_hours} horas</b> sin hacer peticiones.\n"
        f"El scraper reanudará automáticamente cuando pase el cooldown."
    )


def notify_daily_summary(
    account: Optional[str],
    new_count: int,
    updated_count: int,
    skipped_count: int,
    error_count: int,
    queue_pending: int,
) -> None:
    """
    Resumen del enriquecimiento: cuántos contactos se procesaron, cuántos quedan.
    Solo se envía si se procesó al menos un contacto (evita spam en días sin actividad).
    """
    if new_count + updated_count + error_count == 0:
        return
    label = account or "cuenta principal"
    total = new_count + updated_count
    _send(
        f"📊 <b>LinkedIn Scraper — Resumen [{label}]</b>\n\n"
        f"✅ Nuevos: <b>{new_count}</b>\n"
        f"🔄 Actualizados: <b>{updated_count}</b>\n"
        f"⏭ Saltados (frescos): <b>{skipped_count}</b>\n"
        f"❌ Errores: <b>{error_count}</b>\n"
        f"📋 Pendientes en cola: <b>{queue_pending}</b>"
    )


def notify_index_complete(
    account: Optional[str],
    total_slugs: int,
    new_queued: int,
) -> None:
    """
    Notifica que el reindexado de slugs ha finalizado.
    """
    label = account or "cuenta principal"
    _send(
        f"🗂 <b>LinkedIn Scraper — Índice actualizado [{label}]</b>\n\n"
        f"Conexiones encontradas: <b>{total_slugs}</b>\n"
        f"Nuevas encoladas: <b>{new_queued}</b>"
    )


def is_enabled() -> bool:
    """True si las notificaciones Telegram están configuradas y activas."""
    return _ENABLED
