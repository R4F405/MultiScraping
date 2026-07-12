import json

from backend.scraper.maps_parser import (
    extract_viewport_from_maps_response,
    hex_cid_to_decimal,
    parse_business_block,
    parse_cids_from_maps_response,
    parse_maps_response,
    parse_place_from_preview_json,
    safe_get,
)


def _make_block(
    name="Clínica Dental Ejemplo",
    address="Calle Mayor 1, 46001 València, Valencia",
    categories=("Clínica dental", "Dentista"),
    rating=4.8,
    website="https://clinicaejemplo.com/",
    phone="963 00 00 00",
    hex_cid="0xd6048b0b0d501ad:0x143a6297c9e67326",
    place_id="ChIJ_test_place_id",
    lat=39.4726762,
    lng=-0.3722695,
):
    """Business data block mirroring the real positional schema (index 178 = phone)."""
    block = [None] * 179
    block[2] = address.split(", ") if address else None
    block[4] = [None, None, None, None, None, None, None, rating]
    block[7] = [website] if website else None
    block[9] = [None, None, lat, lng]
    block[10] = hex_cid
    block[11] = name
    block[13] = list(categories) if categories else None
    block[39] = address
    block[78] = place_id
    block[178] = [[phone]] if phone else None
    return block


def _make_search_response(blocks):
    """pb-based tbm=map response: results at data[0][1], entry[14] = block."""
    entries = [[None] * 11]  # header row without business block
    for block in blocks:
        entry = [None] * 15
        entry[14] = block
        entries.append(entry)
    data = [[None, entries]]
    return json.dumps(data)


# ---- safe_get ----

def test_safe_get_nested_list():
    data = [[1, [2, 3]], [4, 5]]
    assert safe_get(data, 0, 1, 0) == 2


def test_safe_get_missing_key_returns_default():
    data = [[1, 2]]
    assert safe_get(data, 5) is None
    assert safe_get(data, 5, default="x") == "x"


def test_safe_get_empty_structure():
    assert safe_get([], 0) is None
    assert safe_get(None, 0) is None


# ---- hex_cid_to_decimal ----

def test_hex_cid_to_decimal():
    assert hex_cid_to_decimal("0x0:0x143a6297c9e67326") == "1457585833474683686"
    assert hex_cid_to_decimal("0xd6048b0b0d501ad:0x143a6297c9e67326") == "1457585833474683686"
    assert hex_cid_to_decimal("not_a_cid") is None
    assert hex_cid_to_decimal("") is None


# ---- parse_business_block ----

def test_parse_business_block_full():
    business = parse_business_block(_make_block())
    assert business["business_name"] == "Clínica Dental Ejemplo"
    assert business["address"] == "Calle Mayor 1, 46001 València, Valencia"
    assert business["category"] == "Clínica dental"
    assert business["rating"] == 4.8
    assert business["phone"] == "963 00 00 00"
    assert business["website"] == "https://clinicaejemplo.com/"
    assert business["place_id"] == "0xd6048b0b0d501ad:0x143a6297c9e67326"
    assert business["maps_url"] == "https://www.google.com/maps?cid=1457585833474683686"
    assert business["latitude"] == 39.4726762
    assert business["longitude"] == -0.3722695


def test_parse_business_block_without_name_returns_none():
    block = _make_block()
    block[11] = None
    assert parse_business_block(block) is None
    assert parse_business_block(None) is None
    assert parse_business_block("not a list") is None


def test_parse_business_block_address_fallback_to_parts():
    block = _make_block()
    block[39] = None
    business = parse_business_block(block)
    assert business["address"] == "Calle Mayor 1, 46001 València, Valencia"


def test_parse_business_block_optional_fields_missing():
    block = _make_block(rating=None, website=None, phone=None)
    block[4] = None
    block[7] = None
    block[178] = None
    business = parse_business_block(block)
    assert business["business_name"] == "Clínica Dental Ejemplo"
    assert business["rating"] is None
    assert business["website"] is None
    assert business["phone"] is None


# ---- parse_maps_response (pb search format) ----

def test_parse_invalid_json_returns_empty():
    assert parse_maps_response("not valid json") == []


def test_parse_empty_json_array_returns_empty():
    assert parse_maps_response("[]") == []


def test_parse_search_response_extracts_all_businesses():
    raw = _make_search_response([
        _make_block(name="Negocio A", hex_cid="0x0:0x1"),
        _make_block(name="Negocio B", hex_cid="0x0:0x2"),
        _make_block(name="Negocio C", hex_cid="0x0:0x3"),
    ])
    results = parse_maps_response(raw)
    assert [b["business_name"] for b in results] == ["Negocio A", "Negocio B", "Negocio C"]
    assert all(b["phone"] for b in results)


def test_parse_search_response_skips_header_and_nameless_entries():
    nameless = _make_block()
    nameless[11] = None
    raw = _make_search_response([_make_block(name="Solo Yo"), nameless])
    results = parse_maps_response(raw)
    assert len(results) == 1
    assert results[0]["business_name"] == "Solo Yo"


def test_parse_search_response_structure_scan_fallback():
    """If Google moves the result list away from data[0][1], the scan finds it."""
    entry = [None] * 15
    entry[14] = _make_block(name="Reubicado")
    data = [None, [None, None, [[None] * 11, entry]]]  # results at data[1][2]
    results = parse_maps_response(json.dumps(data))
    assert len(results) == 1
    assert results[0]["business_name"] == "Reubicado"


# ---- bootstrap (plain q=) document ----

def _make_bootstrap_response(cids, lat=39.4737506, lng=-0.3703627, altitude=24638.4):
    data = [None] * 17
    data[0] = ["dentistas Valencia"]
    data[1] = [[altitude, lng, lat], [0, 0, 0], [1024, 768], 13.1]
    cid_entries = [[None, None, cid] for cid in cids]
    spotlight = [None] * 12
    spotlight[11] = ["dentistas", ["105", ""], cid_entries]
    block7 = [None] * 12
    block7[11] = spotlight[11]
    inner = [None] * 8
    inner[7] = block7
    data[16] = [[inner]]
    return json.dumps(data)


def test_extract_viewport_from_bootstrap():
    raw = _make_bootstrap_response(["0x0:0x1"])
    viewport = extract_viewport_from_maps_response(raw)
    assert viewport is not None
    lat, lng, altitude = viewport
    assert lat == 39.4737506
    assert lng == -0.3703627
    assert altitude == 24638.4


def test_extract_viewport_missing_returns_none():
    assert extract_viewport_from_maps_response("[]") is None
    assert extract_viewport_from_maps_response("not json") is None
    assert extract_viewport_from_maps_response(json.dumps([None, [["x", "y", "z"]]])) is None


def test_parse_cids_known_path():
    cids = ["0x0:0x143a6297c9e67326", "0x0:0xe8d03a5ba32a46f9"]
    raw = _make_bootstrap_response(cids)
    assert parse_cids_from_maps_response(raw) == cids


def test_parse_cids_regex_fallback_when_path_moves():
    raw = json.dumps([None, ["0x0:0xabc123", ["nested", "0x0:0xdef456", "0x0:0xabc123"]]])
    cids = parse_cids_from_maps_response(raw)
    assert cids == ["0x0:0xabc123", "0x0:0xdef456"]  # dedupe, order preserved


def test_parse_cids_invalid_json_returns_empty():
    assert parse_cids_from_maps_response("not json") == []


# ---- preview endpoint (fallback flow) ----

def test_parse_place_from_preview_json():
    data = [None] * 7
    data[6] = _make_block(name="Preview Negocio")
    business = parse_place_from_preview_json(json.dumps(data))
    assert business["business_name"] == "Preview Negocio"
    assert business["phone"] == "963 00 00 00"


def test_parse_place_from_preview_json_uses_fallback_cid():
    block = _make_block(name="Sin CID", hex_cid=None)
    data = [None] * 7
    data[6] = block
    business = parse_place_from_preview_json(json.dumps(data), hex_cid="0x0:0x2a")
    assert business["place_id"] == "0x0:0x2a"
    assert business["maps_url"] == "https://www.google.com/maps?cid=42"


def test_parse_place_from_preview_json_bad_input():
    assert parse_place_from_preview_json("not json") is None
    assert parse_place_from_preview_json("[]") is None
