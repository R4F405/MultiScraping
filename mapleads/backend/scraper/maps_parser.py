"""
Parser for Google Maps internal search API responses.

Google Maps returns data as deeply nested positional arrays (not named keys).

The API now has two distinct response formats:

  FORMAT A (old, full detail) — data[0][1][0][14] → list of business blocks.
    Each business block has name, address, phone, website, rating inline.

  FORMAT B (current, CID-only) — data[16][0][0][7][11][2] → list of hex CIDs.
    Business details are NOT inline; each place must be fetched separately.
    Hex CID format: "0x0:0xHHHHHHHHHHHHHHHH"

When no businesses are found, the raw response is saved to /tmp/mapleads_debug_*.json
so you can inspect the actual structure and update the candidate paths.
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


def _looks_like_business_entry(item) -> bool:
    """
    Heuristic: does this list item look like a Maps business entry?

    A business entry is a list where index 14 contains the business data block,
    and block[0][0] is the business name (a non-empty string).
    """
    if not isinstance(item, list):
        return False
    block = safe_get(item, 14, 0)
    if isinstance(block, list):
        name = safe_get(block, 0, 0)
        if isinstance(name, str) and len(name) > 1:
            return True
    return False


def _scan_for_business_list(data: list) -> tuple[list, str]:
    """
    Scan the top levels of the Maps response to find the list of business entries.

    Tries all children of the top 3 nesting levels. Returns (result_list, path_str)
    where path_str describes the path found (for logging). Returns ([], "") if not found.
    """
    if not isinstance(data, list):
        return [], ""

    def _check_candidate(node, path: str) -> tuple[list, str]:
        if not isinstance(node, list) or len(node) == 0:
            return [], ""
        hits = sum(1 for item in node[:5] if _looks_like_business_entry(item))
        if hits >= 1:
            return node, path
        return [], ""

    # BFS up to depth 4
    queue: list[tuple] = [(data, "data", 0)]
    while queue:
        node, path, depth = queue.pop(0)
        if not isinstance(node, list) or depth > 4:
            continue

        found, found_path = _check_candidate(node, path)
        if found:
            return found, found_path

        if depth < 4:
            for i, child in enumerate(node):
                if isinstance(child, list):
                    queue.append((child, f"{path}[{i}]", depth + 1))

    return [], ""


def _save_debug_dump(raw_json: str) -> None:
    """Save raw response to a temp file for manual inspection."""
    try:
        fd, path = tempfile.mkstemp(prefix="mapleads_debug_", suffix=".json", dir="/tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw_json)
        logger.warning("maps_parser: raw response saved for inspection → %s", path)
    except Exception as exc:
        logger.debug("maps_parser: could not save debug dump: %s", exc)


def _extract_business(entry: list) -> dict | None:
    """
    Extract normalized business data from a single Maps result entry.

    The positional structure observed from Google Maps responses:
      entry[14][0]  → business data block
    Within the data block:
      [0][0]        → name
      [0][1]        → maps_url (google.com/maps/place/...)
      [0][2]        → address
      [0][13][0]    → category
      [0][4][7]     → rating (float)
      [0][178][0][0]→ phone (varies by version)
      [0][7][0]     → website
      [0][78]       → place_id
    """
    try:
        block = safe_get(entry, 14, 0)
        if not isinstance(block, list):
            return None

        name = safe_get(block, 0, 0)
        if not name or not isinstance(name, str):
            return None

        place_id = safe_get(block, 0, 78)
        maps_url = safe_get(block, 0, 1)
        address = safe_get(block, 0, 2)
        category = safe_get(block, 0, 13, 0)
        rating = safe_get(block, 0, 4, 7)
        phone = safe_get(block, 0, 178, 0, 0) or safe_get(block, 0, 3, 0)
        website = safe_get(block, 0, 7, 0) or safe_get(block, 0, 183)

        if isinstance(phone, list):
            phone = phone[0] if phone else None

        return {
            "place_id": str(place_id) if place_id else None,
            "business_name": name,
            "address": address,
            "category": category,
            "rating": float(rating) if rating is not None else None,
            "phone": str(phone) if phone else None,
            "website": str(website) if website else None,
            "maps_url": str(maps_url) if maps_url else None,
        }

    except Exception as exc:
        logger.debug("Failed to extract business from entry: %s", exc)
        return None


def parse_maps_response(raw_json: str) -> list[dict]:
    """
    Parse Google Maps search response JSON into a list of business dicts.

    Returns empty list on any parsing error.
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

    logger.debug("maps_parser: top-level list len=%d", len(data))

    # --- Fast path: known candidate paths from observed response structures ---
    known_paths: list[tuple[str, list | None]] = [
        ("data[0][1][0][14]", safe_get(data, 0, 1, 0, 14)),
        ("data[0][1][0][1]",  safe_get(data, 0, 1, 0, 1)),
        ("data[0][0][1][0][14]", safe_get(data, 0, 0, 1, 0, 14)),
        ("data[0][0][0]",     safe_get(data, 0, 0, 0)),
        ("data[0][2]",        safe_get(data, 0, 2)),
        ("data[0][3]",        safe_get(data, 0, 3)),
        ("data[1][0][14]",    safe_get(data, 1, 0, 14)),
        ("data[1]",           safe_get(data, 1)),
    ]

    result_sets: list = []
    found_path = ""

    for path_name, candidate in known_paths:
        if isinstance(candidate, list) and len(candidate) > 0:
            if _looks_like_business_entry(candidate[0]):
                result_sets = candidate
                found_path = path_name
                logger.debug("maps_parser: fast-path hit at %s (%d entries)", path_name, len(result_sets))
                break

    # --- Fallback: scan the entire structure heuristically ---
    if not result_sets:
        logger.debug("maps_parser: fast-path missed — running structure scan")
        result_sets, found_path = _scan_for_business_list(data)
        if result_sets:
            logger.info(
                "maps_parser: structure scan found business list at %s (%d entries) — "
                "consider adding this path to known_paths",
                found_path, len(result_sets),
            )

    if not result_sets:
        logger.warning("maps_parser: could not locate business list in response")
        logger.debug("maps_parser: top-level structure: %s", _describe_structure(data, depth=3))
        _save_debug_dump(raw_json)
        return []

    businesses = []
    for entry in result_sets:
        if not isinstance(entry, list):
            continue
        business = _extract_business(entry)
        if business and business.get("business_name"):
            businesses.append(business)

    logger.debug("maps_parser: extracted %d businesses from path %s", len(businesses), found_path)
    return businesses


# ---------------------------------------------------------------------------
# FORMAT B — CID extraction and place-page parsing
# ---------------------------------------------------------------------------

def parse_cids_from_maps_response(raw_json: str) -> list[str]:
    """
    Extract hex CIDs from the new tbm=map response format (FORMAT B).

    Known path: data[16][0][0][7][11][2] → list of [None, None, hex_cid_str] entries.
    Returns a list of strings like "0x0:0xHHHHHHHHHHHHHHHH".
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return []

    entries = safe_get(data, 16, 0, 0, 7, 11, 2)
    if not isinstance(entries, list):
        logger.debug("parse_cids_from_maps_response: CID path not found")
        return []

    cids = []
    for entry in entries:
        cid = safe_get(entry, 2)
        if isinstance(cid, str) and cid.startswith("0x"):
            cids.append(cid)

    logger.debug("parse_cids_from_maps_response: found %d CIDs", len(cids))
    return cids


def hex_cid_to_decimal(hex_cid: str) -> str | None:
    """
    Convert a Google Maps hex CID "0x0:0xHHHH" to a decimal string for URL use.

    The decimal value is the high part (after the colon) as a base-10 integer.
    """
    try:
        parts = hex_cid.split(":")
        if len(parts) == 2:
            return str(int(parts[1], 16))
    except Exception:
        pass
    return None


def extract_preview_url_from_html(html: str) -> str | None:
    """
    Extract the /maps/preview/place URL from a Google Maps place page HTML.

    Google Maps place pages (fetched via /maps?cid=) embed a <link> element in the
    <head> that points to the JSON data API endpoint with all the required parameters
    including the exact business coordinates needed for the preview API.

    Returns the full URL string, or None if not found.
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
    Parse a Google Maps /maps/preview/place JSON response into a business dict.

    The preview endpoint returns a XSSI-protected JSON array. Strip the leading
    ``)]}'\\n`` prefix before calling this function. Business data lives at
    ``data[6]`` with the following positional structure:

        data[6][4][7]   → rating (float)
        data[6][7][0]   → website URL
        data[6][10]     → full hex CID (0xHIGH:0xLOW)
        data[6][11]     → business name
        data[6][13][0]  → primary category
        data[6][39]     → full address string
        data[6][178][0][0] → phone number

    Returns a normalized business dict, or None on failure.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.debug("parse_place_from_preview_json: JSON error: %s", exc)
        return None

    block = safe_get(data, 6)
    if not isinstance(block, list) or len(block) < 15:
        logger.debug("parse_place_from_preview_json: data[6] missing or too small (len=%s)",
                     len(block) if isinstance(block, list) else "N/A")
        return None

    name = safe_get(block, 11)
    if not name or not isinstance(name, str):
        logger.debug("parse_place_from_preview_json: no name at data[6][11]")
        return None

    address = safe_get(block, 39)
    if not address:
        parts = safe_get(block, 2)
        address = ", ".join(p for p in (parts or []) if p) or None

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

    cid = safe_get(block, 10) or hex_cid or None
    decimal = hex_cid_to_decimal(cid) if cid else None
    maps_url = f"https://www.google.com/maps?cid={decimal}" if decimal else None

    logger.debug("parse_place_from_preview_json: extracted '%s'", name)
    return {
        "place_id": cid,
        "business_name": name,
        "address": str(address) if address else None,
        "category": str(category) if category else None,
        "rating": rating,
        "phone": str(phone) if phone else None,
        "website": str(website) if website else None,
        "maps_url": maps_url,
    }


def parse_place_from_html(html: str, hex_cid: str = "") -> dict | None:
    """
    Legacy fallback: extract basic business data from a Google Maps place HTML.

    Google Maps is a JavaScript SPA; the initial HTML response does not contain
    business data inline. This function only returns the business name (from
    ``<title>``) as a last-resort fallback. For full data use the two-step approach:
    extract_preview_url_from_html() → fetch → parse_place_from_preview_json().
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
