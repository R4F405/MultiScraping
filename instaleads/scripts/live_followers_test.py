"""
Prueba REAL del modo Seguidores contra Instagram (no usa mocks).

Comprueba que:
  1. se listan MÁS de 50 seguidores de la cuenta objetivo (el límite que
     tenía la web y en el que se quedaba el scraper roto);
  2. el enriquecimiento (Fase 2) devuelve email / teléfono de algunos perfiles.

Uso (desde la carpeta instaleads/, con la sesión configurada en el panel o en
IG_SESSIONID):

    python -m scripts.live_followers_test <cuenta_objetivo> [--max 300] [--enrich 10]

Por defecto trabaja sobre una base de datos TEMPORAL: no toca tus leads, ni el
cursor guardado de esa cuenta, ni los contadores diarios. Usa --use-real-db
para ejecutarlo contra data/instaleads.db.

Aviso: usa tu cuenta de Instagram. Ejecútalo desde la misma red/proxy que usa
normalmente el scraper; una IP nueva de datacenter puede provocar un
"challenge" de Instagram en la cuenta.
"""

import argparse
import asyncio
import logging
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config.settings import Settings  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="cuenta objetivo (con o sin @); debe tener más de 50 seguidores")
    parser.add_argument("--max", type=int, default=300, help="seguidores a listar (default 300)")
    parser.add_argument("--enrich", type=int, default=10, help="perfiles a enriquecer con email/teléfono (default 10)")
    parser.add_argument("--use-real-db", action="store_true", help="usar data/instaleads.db en vez de una BD temporal")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not args.use_real_db:
        Settings.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="ig_live_"), "live.db")

    from backend.scraper.ig_client import IgAuthError
    from backend.scraper.ig_followers import (
        SOURCE_LABELS, FollowersError, describe_report, resolve_user, scrape_followers,
    )
    from backend.scraper.ig_profile import get_profile
    from backend.scraper.ig_session import get_enrichment_session, get_session
    from backend.storage import database as db

    await db.init_db()
    session = get_session()
    if session is None or not session.authenticated:
        print("✗ No hay sesión: configura IG_SESSIONID (panel de configuración o variable de entorno).")
        return 2
    target = args.target.strip().lstrip("@")
    print(f"Sesión: ds_user_id={session.ds_user_id} · objetivo: @{target} · BD: {Settings.DB_PATH}")

    # ── 1. Listado de seguidores ─────────────────────────────────────────────
    t0 = time.monotonic()
    followers: list[dict] = []
    report: dict = {}
    try:
        info = await resolve_user(target)
        print(f"@{target} → {info}")
        async for f in scrape_followers(target, amount=args.max, reset_cursor=True, report=report):
            followers.append(f)
            if len(followers) % 50 == 0:
                print(f"   … {len(followers)} seguidores")
    except IgAuthError as exc:
        print(f"✗ Sesión rechazada por Instagram: {exc}")
        return 3
    except FollowersError as exc:
        print(f"✗ Error listando seguidores: {exc}")
    unique = {f["instagram_id"] for f in followers if f.get("instagram_id")}
    ok_list = len(unique) > 50
    print(
        f"\n{'✓' if ok_list else '✗'} Seguidores únicos listados: {len(unique)} "
        f"(objetivo >50) en {time.monotonic() - t0:.0f}s · parada: {report.get('stop') or '-'}"
    )
    for e in report.get("sources", []):
        extra = f", {e['queries']} búsquedas" if e.get("queries") else ""
        print(f"   {SOURCE_LABELS.get(e['source'], e['source']):<20} +{e['new']:<6} "
              f"peticiones={e['requests']:<5} fin={e['end'] or '-'}{extra}")
    detail = describe_report(report)
    if detail:
        print(f"   → {detail}")
    for f in followers[:5]:
        print(f"   @{f['username']}  {'(privada)' if f['is_private'] else ''}")

    # ── 2. Enriquecimiento email / teléfono ──────────────────────────────────
    public = [f for f in followers if not f.get("is_private")][: max(0, args.enrich)]
    if not public:
        return 0 if ok_list else 1
    enrich_session = get_enrichment_session()
    print(f"\nEnriqueciendo {len(public)} perfiles públicos (sesión ds_user_id={enrich_session.ds_user_id}); "
          f"pausa {Settings.IG_ENRICH_DELAY_MIN:.0f}-{Settings.IG_ENRICH_DELAY_MAX:.0f}s entre perfiles…")
    with_email = with_phone = failed = 0
    for f in public:
        try:
            p = await get_profile(f["username"], user_id=f["instagram_id"], mobile=True, strict_auth=True)
        except IgAuthError as exc:
            print(f"✗ Sesión de enriquecimiento rechazada: {exc}")
            break
        if p is None:
            failed += 1
            print(f"   @{f['username']:<30} (sin respuesta — throttling/red)")
            continue
        with_email += bool(p.get("email"))
        with_phone += bool(p.get("phone"))
        print(
            f"   @{f['username']:<30} email={p.get('email') or '—'} ({p.get('email_source') or '-'})  "
            f"tel={p.get('phone') or '—'} ({p.get('phone_source') or '-'})  "
            f"{'business' if p.get('is_business') else 'personal'}"
        )
    print(f"\nResumen enriquecimiento: {with_email} con email, {with_phone} con teléfono, {failed} sin respuesta "
          f"de {len(public)}. (Solo las cuentas business/creator publican email/teléfono.)")
    return 0 if ok_list else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
