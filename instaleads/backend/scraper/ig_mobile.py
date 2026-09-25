"""
Android-app identity for Instagram's mobile private API (``i.instagram.com``).

Why this exists (Sept 2026): the web GraphQL ``query_hash`` followers query now
answers with the right ``count`` but always-empty ``edges``, and
``web_profile_info`` returns 400/429 for datacenter IPs and no longer carries
the contact fields. The mobile API is what still works for both the followers
list and per-profile contact info (``public_email``, ``public_phone_number``…).

The same ``sessionid`` cookie the panel already stores is reused: the mobile
API accepts it inside a ``Bearer IGT:2:<base64 json>`` Authorization header —
exactly what instagrapi's ``login_by_sessionid`` does. Headers and app version
mirror instagrapi 3.0.14 (released 2026-09-24).

Device ids are derived deterministically from the account id so the same
account always looks like the same phone across restarts (a device that
changes on every request is itself a ban signal).
"""

import base64
import hashlib
import json
import random
import time
import uuid

from backend.config.settings import Settings

MOBILE_API_BASE = "https://i.instagram.com/api/v1/"
MOBILE_GRAPHQL_URL = "https://i.instagram.com/graphql/query"

# Pixel 8 Pro / Android 14 — instagrapi's default device profile.
_DEVICE = {
    "android_version": 34,
    "android_release": "14",
    "dpi": "480dpi",
    "resolution": "1344x2992",
    "manufacturer": "Google/google",
    "model": "Pixel 8 Pro",
    "device": "husky",
    "cpu": "husky",
}


def user_agent() -> str:
    d = _DEVICE
    return (
        f"Instagram {Settings.IG_MOBILE_APP_VERSION} "
        f"Android ({d['android_version']}/{d['android_release']}; {d['dpi']}; {d['resolution']}; "
        f"{d['manufacturer']}; {d['model']}; {d['device']}; {d['cpu']}; "
        f"{Settings.IG_MOBILE_LOCALE}; {Settings.IG_MOBILE_VERSION_CODE})"
    )


class MobileDevice:
    """Stable per-account device identifiers."""

    def __init__(self, seed: str) -> None:
        seed = seed or "anonymous"
        self.uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ig-device:{seed}"))
        self.phone_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ig-phone:{seed}"))
        self.android_id = "android-" + hashlib.sha256(f"ig-android:{seed}".encode()).hexdigest()[:16]


def _device_for(session) -> MobileDevice:
    seed = getattr(session, "ds_user_id", None) or getattr(session, "sessionid", "") or ""
    return MobileDevice(seed)


def authorization(session) -> str:
    """``Bearer IGT:2:…`` header built from the web sessionid."""
    if session is None or not getattr(session, "authenticated", False):
        return ""
    payload = {
        "ds_user_id": str(session.ds_user_id or ""),
        "sessionid": session.sessionid,
        "should_use_header_over_cookies": True,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return "Bearer IGT:2:" + base64.b64encode(raw).decode()


def rank_token(session) -> str:
    return f"{getattr(session, 'ds_user_id', None) or 0}_{_device_for(session).uuid}"


def mobile_headers(session) -> dict:
    """Headers the Android app sends on every private API call."""
    device = _device_for(session)
    locale = Settings.IG_MOBILE_LOCALE
    lang = locale.replace("_", "-")
    country = locale.split("_")[-1].upper() if "_" in locale else "US"
    user_id = str(getattr(session, "ds_user_id", None) or 0)
    headers = {
        "User-Agent": user_agent(),
        "X-IG-App-ID": Settings.IG_MOBILE_APP_ID,
        "X-IG-App-Locale": locale,
        "X-IG-Device-Locale": locale,
        "X-IG-Mapped-Locale": locale,
        "X-IG-App-Startup-Country": country,
        "X-Pigeon-Session-Id": f"UFS-{device.uuid}-1",
        "X-Pigeon-Rawclienttime": f"{time.time():.3f}",
        "X-IG-Bandwidth-Speed-KBPS": f"{random.randint(2500000, 3000000) / 1000:.3f}",
        "X-IG-Bandwidth-TotalBytes-B": str(random.randint(5000000, 90000000)),
        "X-IG-Bandwidth-TotalTime-MS": str(random.randint(2000, 9000)),
        "X-Bloks-Version-Id": Settings.IG_MOBILE_BLOKS_VERSION_ID,
        "X-Bloks-Is-Layout-RTL": "false",
        "X-IG-WWW-Claim": "0",
        "X-IG-Device-ID": device.uuid,
        "X-IG-Family-Device-ID": device.phone_id,
        "X-IG-Android-ID": device.android_id,
        "X-IG-Timezone-Offset": str(Settings.IG_MOBILE_TIMEZONE_OFFSET),
        "X-IG-Connection-Type": "WIFI",
        "X-IG-Capabilities": "3brTv10=",
        "X-FB-HTTP-Engine": "Tigon/MNS/TCP",
        "X-FB-Client-IP": "True",
        "X-FB-Server-Cluster": "True",
        "Priority": "u=3",
        "Accept-Language": f"{lang}, en-US" if lang != "en-US" else "en-US",
        "Accept-Encoding": "gzip, deflate",
        "IG-INTENDED-USER-ID": user_id,
    }
    if user_id != "0":
        headers["IG-U-DS-USER-ID"] = user_id
    auth = authorization(session)
    if auth:
        headers["Authorization"] = auth
    mid = getattr(session, "mid", None)
    if mid:
        headers["X-MID"] = mid
    return headers


def graphql_form(friendly_name: str, variables: dict, doc_id: str) -> dict:
    """Form body for ``POST i.instagram.com/graphql/query`` (private GraphQL)."""
    return {
        "method": "post",
        "pretty": "false",
        "format": "json",
        "server_timestamps": "true",
        "locale": "user",
        "fb_api_req_friendly_name": friendly_name,
        "enable_canonical_naming": "true",
        "enable_canonical_variable_overrides": "true",
        "enable_canonical_naming_ambiguous_type_prefixing": "true",
        "variables": json.dumps(variables, separators=(",", ":")),
        "client_doc_id": str(doc_id),
    }


def graphql_headers(session, friendly_name: str, root_field: str, doc_id: str) -> dict:
    headers = mobile_headers(session)
    headers.update({
        "X-FB-Friendly-Name": friendly_name,
        "X-Root-Field-Name": root_field,
        "X-Client-Doc-Id": str(doc_id),
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Priority": "u=3, i",
    })
    return headers
