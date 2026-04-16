#!/usr/bin/env python3
"""
Regenera el catálogo local de categorías para el dropdown (maps_categories.json)
extrayendo los "place types" publicados por Google Maps/Places.

Notas:
- Este proyecto usa el catálogo como heurística local para búsqueda/ranking.
- Google puede cambiar la taxonomía; por eso mantenemos metadata del origen.
- Por defecto NO escribe; usa `--write` para actualizar el JSON real.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import tempfile
from pathlib import Path

import httpx

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    BeautifulSoup = None  # type: ignore[assignment]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "backend" / "data" / "maps_categories.json"
META_PATH = PROJECT_ROOT / "backend" / "data" / "maps_categories.meta.json"
GBP_PATH = PROJECT_ROOT / "backend" / "data" / "gbp_categories.json"
GBP_MAP_PATH = PROJECT_ROOT / "backend" / "data" / "category_mapping_gbp_place_types.json"

# URLs de documentación pública (place types / supported types).
# Si Google cambia el HTML, el script sigue funcionando parcialmente por extracción
# basada en `code` tags que parecen "types" canónicos.
SOURCE_URLS = [
    "https://developers.google.com/maps/documentation/places/web-service/legacy/supported_types",
    "https://developers.google.com/maps/documentation/places/web-service/place-types",
]

TYPE_RE = re.compile(r"^[a-z][a-z0-9_]+$")


def _normalize_for_alias(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _type_to_label_en(type_key: str) -> str:
    # e.g. "dental_clinic" -> "Dental Clinic"
    return str(type_key).replace("_", " ").strip().title()


def _extract_types_from_html(html: str) -> set[str]:
    types: set[str] = set()

    # Parser robusto con BeautifulSoup si está disponible
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "lxml")
        # Heurística: muchos tipos canónicos aparecen como <code>type_key</code>
        for code in soup.find_all("code"):
            txt = code.get_text(strip=True)
            if TYPE_RE.match(txt or ""):
                types.add(txt)

        # Extra: algunos docs renderizan en <td> con texto tipo "dentist"
        for td in soup.find_all("td"):
            txt = td.get_text(" ", strip=True)
            if TYPE_RE.match(txt or ""):
                types.add(txt)

    # Fallback sin dependencias: extrae contenidos de <code>...</code>
    # y de celdas simples tipo <td>dentist</td>.
    if not types:
        for txt in re.findall(r"<code[^>]*>\s*([a-z][a-z0-9_]+)\s*</code>", html, flags=re.IGNORECASE):
            if TYPE_RE.match(txt):
                types.add(txt)
        for txt in re.findall(r"<td[^>]*>\s*([a-z][a-z0-9_]+)\s*</td>", html, flags=re.IGNORECASE):
            if TYPE_RE.match(txt):
                types.add(txt)

    return types


def _fetch_text(url: str) -> str:
    headers = {
        # Evitamos un User-Agent muy "robot" para que el HTML sea lo más completo posible.
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        "Accept": "text/html,application/xhtml+xml",
    }
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        r = client.get(url, headers=headers)
        r.raise_for_status()
        return r.text


def _load_existing_catalog() -> dict[str, dict]:
    if not DATA_PATH.exists():
        return {}
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return {}
    out: dict[str, dict] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        t = str(entry.get("type", "")).strip()
        if not t:
            continue
        out[t] = entry
    return out


def _build_new_catalog(extracted_types: set[str], existing_by_type: dict[str, dict]) -> list[dict]:
    extracted_sorted = sorted(extracted_types)
    out: list[dict] = []

    # Mantenemos "union" para no perder manuales ya presentes en el repo.
    # Si Google elimina tipos, estos sobrevivirán hasta que decidáis regenerar "desde cero".
    union_types = sorted(set(existing_by_type.keys()) | set(extracted_types))

    for type_key in union_types:
        if type_key in existing_by_type:
            out.append(existing_by_type[type_key])
            continue

        label_en = _type_to_label_en(type_key)
        label_alias = _normalize_for_alias(label_en)
        entry = {
            "type": type_key,
            "label_en": label_en,
            # Placeholder: para búsqueda ES/EN, el score usa también type/aliases.
            # Si quieres, luego puedes enriquecer labels con un diccionario.
            "label_es": label_en,
            "aliases": list(
                {
                    type_key,
                    label_alias,
                    label_alias.replace(" ", "_"),
                    label_en,
                }
            ),
        }
        out.append(entry)

    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Actualiza maps_categories.json y meta JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Alias de no-write (solo reporta).")
    parser.add_argument("--limit-preview", type=int, default=20, help="Cuántos tipos mostrar en preview.")
    args = parser.parse_args()

    write = bool(args.write) and not args.dry_run

    existing_by_type = _load_existing_catalog()

    extracted_types: set[str] = set()
    fetched_urls: list[str] = []
    fetch_errors: list[str] = []

    for url in SOURCE_URLS:
        try:
            html = _fetch_text(url)
            fetched_urls.append(url)
            extracted_types |= _extract_types_from_html(html)
        except Exception as exc:
            fetch_errors.append(f"{url} => {exc}")

    extracted_list = sorted(extracted_types)
    new_catalog = _build_new_catalog(extracted_types, existing_by_type)

    # ---- Hybrid validation (GBP + mapping) ---------------------------------
    gbp_categories = []
    gbp_mapping = {}
    hybrid_warnings: list[str] = []
    try:
        if GBP_PATH.exists():
            raw = json.loads(GBP_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                gbp_categories = raw
    except Exception as exc:
        hybrid_warnings.append(f"gbp_categories.json parse error: {exc}")

    try:
        if GBP_MAP_PATH.exists():
            raw = json.loads(GBP_MAP_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                gbp_mapping = raw
    except Exception as exc:
        hybrid_warnings.append(f"category_mapping_gbp_place_types.json parse error: {exc}")

    gbp_ids = {
        str(entry.get("id", "")).strip()
        for entry in gbp_categories
        if isinstance(entry, dict) and str(entry.get("id", "")).strip()
    }
    mapping_ids = {str(k).strip() for k in gbp_mapping.keys() if str(k).strip()}
    mapping_missing_gbp = sorted(mapping_ids - gbp_ids)
    gbp_missing_mapping = sorted(gbp_ids - mapping_ids)
    if mapping_missing_gbp:
        hybrid_warnings.append(f"mapping entries without gbp id: {len(mapping_missing_gbp)}")
    if gbp_missing_mapping:
        hybrid_warnings.append(f"gbp entries without mapping: {len(gbp_missing_mapping)}")

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    catalog_version = f"local-derived-places-{now[:10]}"
    meta = {
        "catalog_version": catalog_version,
        "catalog_path": str(DATA_PATH),
        "meta_path": str(META_PATH),
        "updated_at": now,
        "source_urls": fetched_urls or SOURCE_URLS,
        "extracted_types_count": len(extracted_types),
        "existing_types_count": len(existing_by_type),
        "catalog_types_count": len(new_catalog),
        "preview_types": extracted_list[: max(0, int(args.limit_preview))],
        "fetch_errors": fetch_errors,
        "hybrid_summary": {
            "gbp_categories_count": len(gbp_ids),
            "mapping_entries_count": len(mapping_ids),
            "mapping_missing_gbp_count": len(mapping_missing_gbp),
            "gbp_missing_mapping_count": len(gbp_missing_mapping),
            "warnings": hybrid_warnings,
        },
    }

    print(
        "maps_categories sync report\n"
        f"- extracted_types_count: {meta['extracted_types_count']}\n"
        f"- existing_types_count: {meta['existing_types_count']}\n"
        f"- catalog_types_count: {meta['catalog_types_count']}\n"
    )
    if meta["preview_types"]:
        print("- preview:", ", ".join(meta["preview_types"]))
    if meta["fetch_errors"]:
        print("- fetch_errors (truncated):")
        for line in meta["fetch_errors"][:3]:
            print("  ", line)

    if not write:
        print("\nModo dry-run: no se escribió ningún archivo. Ejecuta con `--write` para aplicar cambios.")
        return

    # Escritura atómica: escribe a temp y mueve.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(DATA_PATH.parent)) as tmp:
        tmp.write(json.dumps(new_catalog, ensure_ascii=False, indent=2))
        tmp_path = Path(tmp.name)

    tmp_meta_path = None
    try:
        tmp_meta = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(META_PATH.parent))
        tmp_meta.write(json.dumps(meta, ensure_ascii=False, indent=2))
        tmp_meta.close()
        tmp_meta_path = Path(tmp_meta.name)

        tmp_path.replace(DATA_PATH)
        if tmp_meta_path:
            tmp_meta_path.replace(META_PATH)
    finally:
        # Limpieza en caso de fallos.
        if tmp_meta_path and tmp_meta_path.exists():
            try:
                tmp_meta_path.unlink()
            except Exception:
                pass
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

    print(f"\nEscrito OK:\n- {DATA_PATH}\n- {META_PATH}")


if __name__ == "__main__":
    main()

