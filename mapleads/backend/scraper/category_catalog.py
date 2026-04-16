import json
import logging
import unicodedata
from functools import lru_cache
from pathlib import Path

from backend.scraper.maps_categories import load_categories

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_GBP_PATH = _DATA_DIR / "gbp_categories.json"
_GBP_MAP_PATH = _DATA_DIR / "category_mapping_gbp_place_types.json"

_DEFAULT_TOP_IDS = (
    "nutritionist",
    "gym",
    "dentist",
    "lawyer",
    "accountant",
    "restaurant",
    "coffee_shop",
)


def _normalize(text: str) -> str:
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.split())


@lru_cache(maxsize=1)
def load_gbp_categories() -> list[dict]:
    if not _GBP_PATH.exists():
        return []
    try:
        raw = json.loads(_GBP_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except Exception as exc:
        logger.warning("category_catalog: failed loading gbp_categories.json: %s", exc)
        return []


@lru_cache(maxsize=1)
def load_gbp_mapping() -> dict[str, list[str]]:
    if not _GBP_MAP_PATH.exists():
        return {}
    try:
        raw = json.loads(_GBP_MAP_PATH.read_text(encoding="utf-8"))
        out: dict[str, list[str]] = {}
        if isinstance(raw, dict):
            for key, values in raw.items():
                if not isinstance(key, str) or not isinstance(values, list):
                    continue
                out[key] = [str(v).strip() for v in values if str(v).strip()]
        return out
    except Exception as exc:
        logger.warning("category_catalog: failed loading mapping JSON: %s", exc)
        return {}


def _build_hybrid_catalog() -> list[dict]:
    place_types = load_categories()
    gbp = load_gbp_categories()
    mapping = load_gbp_mapping()

    items: list[dict] = []

    for entry in place_types:
        type_key = str(entry.get("type", "")).strip()
        if not type_key:
            continue
        label_es = str(entry.get("label_es", "")).strip()
        label_en = str(entry.get("label_en", "")).strip()
        aliases = []
        if label_es:
            aliases.append(label_es)
        if label_en:
            aliases.append(label_en)
        aliases.append(type_key)
        items.append(
            {
                "id": type_key,
                "type": type_key,
                "label_es": label_es or label_en or type_key,
                "label_en": label_en or label_es or type_key,
                "source": "place_type",
                "mapped_place_types": [type_key],
                "search_terms": [_normalize(a) for a in aliases if _normalize(a)],
            }
        )

    for entry in gbp:
        gbp_id = str(entry.get("id", "")).strip()
        if not gbp_id:
            continue
        label_es = str(entry.get("label_es", "")).strip()
        label_en = str(entry.get("label_en", "")).strip()
        aliases = [str(a).strip() for a in entry.get("aliases", []) if str(a).strip()]
        aliases.extend([label_es, label_en, gbp_id])
        mapped = mapping.get(gbp_id, [])
        if not mapped:
            mapped = [gbp_id]
        items.append(
            {
                "id": gbp_id,
                "type": gbp_id,
                "label_es": label_es or label_en or gbp_id,
                "label_en": label_en or label_es or gbp_id,
                "source": "gbp_category",
                "mapped_place_types": mapped,
                "search_terms": [_normalize(a) for a in aliases if _normalize(a)],
            }
        )

    return items


@lru_cache(maxsize=1)
def load_hybrid_catalog() -> list[dict]:
    return _build_hybrid_catalog()


def search_categories(query: str = "", limit: int = 20) -> list[dict]:
    q = _normalize(query)
    rows = load_hybrid_catalog()
    max_limit = max(1, min(limit, 100))

    scored: list[tuple[int, int, str, dict]] = []

    for row in rows:
        terms = row.get("search_terms", [])
        if not isinstance(terms, list):
            continue

        score = None
        if not q:
            priority = 0 if str(row.get("id", "")) in _DEFAULT_TOP_IDS else 1
            source_rank = 0 if row.get("source") == "gbp_category" else 1
            score = (100, priority, source_rank)
        else:
            if any(t.startswith(q) for t in terms):
                score = (0, 0, 0)
            elif any(q in t for t in terms):
                score = (1, 0, 0)
            elif any(t.replace("_", " ").startswith(q) for t in terms):
                score = (2, 0, 0)

        if score is None:
            continue

        label_key = _normalize(str(row.get("label_es") or row.get("label_en") or row.get("type") or ""))
        scored.append((score[0], score[1], label_key, row))

    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    seen: set[str] = set()
    out: list[dict] = []

    for _, _, _, row in scored:
        unique_key = f"{row.get('source')}::{row.get('id')}"
        if unique_key in seen:
            continue
        seen.add(unique_key)
        out.append(
            {
                "type": str(row.get("type", "")).strip(),
                "label_es": str(row.get("label_es", "")).strip(),
                "label_en": str(row.get("label_en", "")).strip(),
                "source": str(row.get("source", "")).strip(),
                "mapped_place_types": row.get("mapped_place_types", []),
            }
        )
        if len(out) >= max_limit:
            break

    return out


def clear_category_catalog_cache() -> None:
    load_gbp_categories.cache_clear()
    load_gbp_mapping.cache_clear()
    load_hybrid_catalog.cache_clear()

