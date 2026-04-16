import json
import logging
import unicodedata
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "maps_categories.json"
_META_PATH = Path(__file__).resolve().parent.parent / "data" / "maps_categories.meta.json"


def _normalize(text: str) -> str:
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.split())


@lru_cache(maxsize=1)
def load_categories() -> list[dict]:
    with _DATA_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        logger.warning("maps_categories: invalid dataset format (expected list)")
        return []
    return data


@lru_cache(maxsize=1)
def load_categories_meta() -> dict:
    """
    Devuelve metadata generada por `mapleads/scripts/update_maps_categories.py`.
    Si no existe el fichero, devuelve un dict vacío.
    """
    if not _META_PATH.exists():
        return {}
    try:
        raw = _META_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def _score_entry(query_norm: str, entry: dict) -> int | None:
    label_es = _normalize(str(entry.get("label_es", "")))
    label_en = _normalize(str(entry.get("label_en", "")))
    aliases = [_normalize(str(a)) for a in entry.get("aliases", []) if isinstance(a, str)]

    if not query_norm:
        return 100

    if label_es.startswith(query_norm) or label_en.startswith(query_norm):
        return 0
    if query_norm in label_es or query_norm in label_en:
        return 1
    if any(alias.startswith(query_norm) for alias in aliases):
        return 2
    if any(query_norm in alias for alias in aliases):
        return 3
    return None


def search_categories(query: str = "", limit: int = 20) -> list[dict]:
    query_norm = _normalize(query)
    records = load_categories()
    scored: list[tuple[int, str, dict]] = []

    for entry in records:
        score = _score_entry(query_norm, entry)
        if score is None:
            continue
        label_key = _normalize(str(entry.get("label_es") or entry.get("label_en") or ""))
        scored.append((score, label_key, entry))

    scored.sort(key=lambda x: (x[0], x[1]))
    seen_types: set[str] = set()
    out: list[dict] = []
    for _, _, entry in scored:
        type_key = str(entry.get("type", "")).strip()
        if not type_key or type_key in seen_types:
            continue
        seen_types.add(type_key)
        out.append(
            {
                "type": type_key,
                "label_es": str(entry.get("label_es", "")).strip(),
                "label_en": str(entry.get("label_en", "")).strip(),
            }
        )
        if len(out) >= max(1, min(limit, 100)):
            break

    logger.debug("maps_categories: query='%s' results=%d", query_norm, len(out))
    return out
