"""
Parser for Google Maps internal search API responses (tbm=map).

Two request styles hit ``https://www.google.com/search?tbm=map``:

  1. ``pb``-based search (primary, current): passing the protobuf-style ``pb``
     parameter returns a rich JSON document where the result list lives at
     ``data[0][1]``. Each result entry is a list whose index 14 holds the
     business data block, with real pagination via the ``8i{offset}`` segment.

  2. Plain ``q=`` search (bootstrap/fallback): returns a light "spotlight"
     document with no inline business data — only the geocoded viewport
     (``data[1]``) and a list of hex CIDs (``data[16][0][0][7][11][2]``).
     Google ignores ``start``/``num`` here, so this style cannot paginate.

The business data block schema is shared between the ``pb`` search results
(``entry[14]``) and the ``/maps/preview/place`` endpoint (``data[6]``):

    block[11]        → name
    block[39]        → full address string
    block[2]         → address parts (list of str)
    block[13]        → categories (list of str)
    block[4][7]      → rating (float)
    block[7][0]      → website URL
    block[178][0][0] → phone number
    block[10]        → hex CID "0xHIGH:0xLOW"
    block[78]        → Google place_id ("ChIJ…")
    block[9][2..3]   → latitude, longitude

When no businesses are found, the raw response is saved to
/tmp/mapleads_debug_*.json so you can inspect the actual structure.
"""

import json
import logging
import os
import re
import tempfile

logger = logging.getLogger(__name__)


def safe_get(obj, *keys, default=None):
    """Safely traverse nested lists/dicts without raising."""
    for key in keys:
        try:
            obj = obj[key]
        except (IndexError, KeyError, TypeError):
            return default
    return obj


def _save_debug_dump(raw_json: str) -> None:
    """Save raw response to a temp file for manual inspection."""
    try:
        fd, path = tempfile.mkstemp(prefix="mapleads_debug_", suffix=".json", dir="/tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw_json)
        logger.warning("maps_parser: raw response saved for inspection → %s", path)
    except Exception as exc:
        logger.debug("maps_parser: could not save debug dump: %s", exc)


def hex_cid_to_decimal(hex_cid: str) -> str | None:
    """
    Convert a Google Maps hex CID "0x…:0xHHHH" to a decimal string for URL use.

    The decimal value is the low part (after the colon) as a base-10 integer.
    """
    try:
        parts = hex_cid.split(":")
        if len(parts) == 2:
            return str(int(parts[1], 16))
    except Exception:
        pass
    return None


def parse_business_block(block, fallback_cid: str = "") -> dict | None:
    """
    Extract a normalized business dict from a Maps business data block.

    Works for both ``pb`` search entries (``entry[14]``) and the
    ``/maps/preview/place`` endpoint (``data[6]``). Returns None when the
    block has no name.
    """
    if not isinstance(block, list):
        return None

    name = safe_get(block, 11)
    if not name or not isinstance(name, str):
        return None

    address = safe_get(block, 39)
    if not address:
        parts = safe_get(block, 2)
        if isinstance(parts, list):
            address = ", ".join(str(p) for p in parts if p) or None

    rating_raw = safe_get(block, 4, 7)
    try:
        rating = float(rating_raw) if rating_raw is not None else None
    except (ValueError, TypeError):
        rating = None

    website = safe_get(block, 7, 0)
    phone = safe_get(block, 178, 0, 0)
    if phone is None:
        phone = safe_get(block, 178, 0, 1, 0, 0)
    category = safe_get(block, 13, 0)

    cid = safe_get(block, 10) or fallback_cid or None
    if not (isinstance(cid, str) and cid.startswith("0x")):
        cid = fallback_cid or None
    decimal = hex_cid_to_decimal(cid) if cid else None
    maps_url = f"https://www.google.com/maps?cid={decimal}" if decimal else None

    # place_id: prefer the hex CID for continuity with historical data
    # (dedupe window compares against previously stored place_ids).
    place_id = cid or safe_get(block, 78)

    latitude = safe_get(block, 9, 2)
    longitude = safe_get(block, 9, 3)

    return {
        "place_id": str(place_id) if place_id else None,
        "business_name": name,
        "address": str(address) if address else None,
        "category": str(category) if category else None,
        "rating": rating,
        "phone": str(phone) if phone else None,
        "website": str(website) if website else None,
        "maps_url": maps_url,
        "latitude": latitude if isinstance(latitude, (int, float)) else None,
        "longitude": longitude if isinstance(longitude, (int, float)) else None,
    }


def _looks_like_business_entry(item) -> bool:
    """A result entry is a list whose index 14 holds a block with a name at [11]."""
    if not isinstance(item, list) or len(item) <= 14:
        return False
    block = safe_get(item, 14)
    return isinstance(block, list) and isinstance(safe_get(block, 11), str)


def _scan_for_business_list(data: list) -> tuple[list, str]:
    """
    Heuristic fallback: BFS over the top nesting levels of the response
    looking for a list that contains business entries. Guards against
    Google moving the result list away from data[0][1].
    """
    if not isinstance(data, list):
        return [], ""

    queue: list[tuple] = [(data, "data", 0)]
    while queue:
        node, path, depth = queue.pop(0)
        if not isinstance(node, list) or depth > 4:
            continue

        hits = sum(1 for item in node[:8] if _looks_like_business_entry(item))
        if hits >= 1:
            return node, path

        if depth < 4:
            for i, child in enumerate(node):
                if isinstance(child, list):
                    queue.append((child, f"{path}[{i}]", depth + 1))

    return [], ""


def parse_maps_response(raw_json: str) -> list[dict]:
    """
    Parse a ``pb``-based tbm=map search response into business dicts.

    The result list lives at ``data[0][1]``; the first element is usually a
    header row without a business block and is skipped. Falls back to a
    structure scan when the known path misses. Returns [] on any error.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Maps response JSON: %s", exc)
        logger.debug("Raw response snippet: %s", raw_json[:500])
        return []

    if not isinstance(data, list):
        logger.warning("maps_parser: unexpected top-level type %s", type(data).__name__)
        return []

    result_sets = safe_get(data, 0, 1)
    found_path = "data[0][1]"
    if not (isinstance(result_sets, list) and any(_looks_like_business_entry(e) for e in result_sets[:8])):
        logger.debug("maps_parser: data[0][1] missed — running structure scan")
        result_sets, found_path = _scan_for_business_list(data)
        if result_sets:
            logger.info(
                "maps_parser: structure scan found business list at %s (%d entries)",
                found_path, len(result_sets),
            )

    if not result_sets:
        logger.debug("maps_parser: could not locate business list in response")
        return []

    businesses = []
    for entry in result_sets:
        if not _looks_like_business_entry(entry):
            continue
        business = parse_business_block(entry[14])
        if business and business.get("business_name"):
            businesses.append(business)

    logger.debug("maps_parser: extracted %d businesses from %s", len(businesses), found_path)
    return businesses


# ---------------------------------------------------------------------------
# Bootstrap (plain q=) document — viewport + CID extraction
# ---------------------------------------------------------------------------

_HEX_CID_RE = re.compile(r'"(0x[0-9a-f]+:0x[0-9a-f]+)"')


def extract_viewport_from_maps_response(raw_json: str) -> tuple[float, float, float] | None:
    """
    Extract the geocoded viewport from a plain ``q=`` tbm=map response.

    ``data[1][0]`` is ``[altitude_m, longitude, latitude]`` — the viewport
    Google chose for the query. Returns (lat, lng, altitude) or None.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return None

    vp = safe_get(data, 1, 0)
    if not (isinstance(vp, list) and len(vp) >= 3):
        return None
    altitude, lng, lat = vp[0], vp[1], vp[2]
    if not all(isinstance(v, (int, float)) for v in (altitude, lng, lat)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180 and altitude > 0):
        return None
    return float(lat), float(lng), float(altitude)


def parse_cids_from_maps_response(raw_json: str) -> list[str]:
    """
    Extract hex CIDs from a plain ``q=`` tbm=map response (bootstrap format).

    Known path: data[16][0][0][7][11][2] → list of [None, None, hex_cid]
    entries. Falls back to a regex scan over the raw document when the path
    moves. Returns strings like "0x0:0xHHHHHHHHHHHHHHHH", order preserved.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return []

    entries = safe_get(data, 16, 0, 0, 7, 11, 2)
    cids: list[str] = []
    if isinstance(entries, list):
        for entry in entries:
            cid = safe_get(entry, 2)
            if isinstance(cid, str) and cid.startswith("0x"):
                cids.append(cid)

    if not cids:
        # Path moved — regex scan keeps the fallback CID flow alive.
        seen: set[str] = set()
        for m in _HEX_CID_RE.finditer(raw_json):
            cid = m.group(1)
            if cid not in seen:
                seen.add(cid)
                cids.append(cid)
        if cids:
            logger.debug("parse_cids_from_maps_response: known path missed, regex found %d CIDs", len(cids))

    logger.debug("parse_cids_from_maps_response: found %d CIDs", len(cids))
    return cids


# ---------------------------------------------------------------------------
# Per-place detail endpoints (fallback flow)
# ---------------------------------------------------------------------------

def extract_preview_url_from_html(html: str) -> str | None:
    """
    Extract the /maps/preview/place URL from a Google Maps place page HTML.

    Place pages (fetched via /maps?cid=) embed a <link> element in <head>
    pointing to the JSON data API endpoint with all required parameters.
    """
    m = re.search(r'<link[^>]+href="(/maps/preview/place[^"]+)"', html)
    if m:
        url = "https://www.google.com" + m.group(1).replace("&amp;", "&")
        logger.debug("extract_preview_url_from_html: found preview URL")
        return url
    logger.debug("extract_preview_url_from_html: no preview link found in HTML")
    return None


def parse_place_from_preview_json(raw_json: str, hex_cid: str = "") -> dict | None:
    """
    Parse a /maps/preview/place JSON response into a business dict.

    The endpoint returns an XSSI-protected JSON array (strip the leading
    ``)]}'`` before calling). The business block lives at ``data[6]`` and
    uses the shared block schema (see module docstring).
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.debug("parse_place_from_preview_json: JSON error: %s", exc)
        return None

    block = safe_get(data, 6)
    business = parse_business_block(block, fallback_cid=hex_cid)
    if business:
        logger.debug("parse_place_from_preview_json: extracted '%s'", business["business_name"])
    else:
        logger.debug("parse_place_from_preview_json: no business block at data[6]")
    return business


def parse_place_from_html(html: str, hex_cid: str = "") -> dict | None:
    """
    Legacy fallback: extract basic business data from a Google Maps place HTML.

    The initial HTML of the Maps SPA only reliably exposes the business name
    (via <title>). Used as a last resort when the preview endpoint fails.
    """
    title_m = re.search(r"<title>(.*?)(?:\s*[-–]\s*Google Maps)?</title>", html, re.IGNORECASE)
    if title_m:
        name = title_m.group(1).strip()
        if name and name.lower() not in ("google maps", ""):
            decimal = hex_cid_to_decimal(hex_cid) if hex_cid else None
            logger.debug("parse_place_from_html: title fallback '%s'", name)
            return {
                "place_id": hex_cid or None,
                "business_name": name,
                "address": None,
                "category": None,
                "rating": None,
                "phone": None,
                "website": None,
                "maps_url": f"https://www.google.com/maps?cid={decimal}" if decimal else None,
                "latitude": None,
                "longitude": None,
            }

    logger.debug("parse_place_from_html: could not extract business data")
    return None


def _describe_structure(obj, depth: int = 2, _current: int = 0) -> str:
    """Recursive helper to describe JSON structure for debugging."""
    if _current >= depth:
        return "..."
    if isinstance(obj, list):
        if not obj:
            return "[]"
        inner = _describe_structure(obj[0], depth, _current + 1)
        return f"[{inner}, ...] (len={len(obj)})"
    if isinstance(obj, dict):
        keys = list(obj.keys())[:5]
        return f"{{keys: {keys}}}"
    return type(obj).__name__
