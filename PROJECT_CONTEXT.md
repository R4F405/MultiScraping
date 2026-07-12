# Project Context

## Snapshot

- **Purpose:** Plataforma de generación de leads B2B con 3 scrapers independientes (Instagram, Google Maps, LinkedIn) orquestados por una UI web centralizada.
- **Main stack:** Python 3.11+, FastAPI, aiosqlite (SQLite), httpx, curl_cffi, instagrapi, playwright, Jinja2, Tailwind CSS
- **Current maturity:** Producción funcional y estable. Login LinkedIn con CAPTCHA/VNC operativo. Tests parcialmente rotos (ver sección Tests).
- **Desplegado en:** VPS OVH (`vps-d6ea56ba.vps.ovh.net`) con HTTPS Let's Encrypt en puerto 9090.

---

## Architecture

- **4 servicios independientes** que se comunican HTTP (scraperLead-web los proxea todos).
- **scraperLead-web** (8081) actúa como BFF/proxy: gestiona auth, UI, y reenvía llamadas API a los backends.
- Cada scraper es autónomo con su propia SQLite, venv, y API REST.
- No hay mensaje broker ni cola compartida — los jobs son in-process (asyncio o threading según el módulo).

```
Usuario → Nginx:9090 (HTTPS)
              └─→ scraperLead-web:8081
                      ├── proxy → mapleads:8001      (Google Maps)
                      ├── proxy → instaleads:8002    (Instagram)
                      └── proxy → linkedinleads:8003 (LinkedIn)
```

**Entry points:**
- `scraperLead-web/main.py` — FastAPI app con auth middleware + proxy routes
- `instaleads/backend/main.py` — FastAPI app factory
- `mapleads/backend/main.py` — FastAPI app factory
- `linkedinleads/backend/main.py` — FastAPI app factory
- `start_all.sh` — arranca los 4 servicios en orden con health checks

---

## Key Modules

### scraperLead-web
- `scraperLead-web/main.py` — toda la lógica: proxy, auth middleware, rutas de página, Jinja2 filters, proxy a los 3 scrapers
- `scraperLead-web/auth.py` — utilidades auth: `parse_users()`, `verify_password()` (bcrypt), `parse_ip_whitelist()`, `get_current_user()`

### instaleads
- `instaleads/backend/api/routes.py` — endpoints REST (health, session, jobs, search, accounts, stats, leads, export)
- `instaleads/backend/scraper/ig_rate_limiter.py` — `RateLimiter(mode)`: delay aleatorio + límite diario + backoff exponencial con lock asyncio
- `instaleads/backend/scraper/ig_deduplicator.py` — `Deduplicator`: set en RAM cargado desde BD; skip duro por `username`, skip blando con ventana 3 días para `ig_skipped`
- `instaleads/backend/scraper/ig_proxy_manager.py` — `IgProxyManager`: round-robin con cooldown 5min por proxy fallido
- `instaleads/backend/scraper/ig_dorking.py` — modo sin sesión (Modo A): búsqueda Startpage/DuckDuckGo → extrae perfiles públicos
- `instaleads/backend/scraper/ig_session.py` — sesión autenticada: carga `sessionid` desde `IG_SESSIONID`/`IG_SESSION_FILE`; deriva `ds_user_id`
- `instaleads/backend/scraper/ig_followers.py` — modo autenticado (Modo B): seguidores de cuenta objetivo vía API privada `friendships/{id}/followers/` con paginación por `max_id` (supera el límite de ~50 de la web de escritorio)
- `instaleads/backend/scraper/ig_client.py` — `ig_get` (guest, adjunta sesión si existe) y `ig_get_authenticated` (API privada; lanza `IgAuthError` si falta/expira sesión)
- `instaleads/backend/scraper/ig_health.py` — healthcheck con caché 2min; basa estado en contadores de BD (no consume cuota)
- `instaleads/backend/storage/database.py` — aiosqlite, tablas: `ig_leads`, `ig_skipped`, `ig_scrape_jobs`, `ig_daily_stats`, `ig_health_log`

### mapleads
- `mapleads/backend/api/routes.py` — endpoints REST (health, proxy, search, jobs, stats, leads, export, categories, email probe)
- `mapleads/backend/scraper/maps_client.py` — curl_cffi con impersonación TLS Chrome + paginación automática
- `mapleads/backend/scraper/maps_parser.py` — BeautifulSoup4/lxml: extrae nombre, dirección, tel, web, coords, rating
- `mapleads/backend/scraper/maps_categories.py` — sincronización catálogo de categorías con Google Maps
- `mapleads/backend/scraper/email_finder.py` / `email_verifier.py` — enrichment: visita la web del negocio y extrae/verifica email
- `mapleads/backend/storage/database.py` — aiosqlite, tablas: `leads`, `scrape_jobs`, `job_locations`
- `mapleads/backend/storage/exporter.py` — exportación CSV

### linkedinleads
- `linkedinleads/backend/api/routes.py` — endpoints REST; gestiona login concurrente con `_login_status` dict + `threading.Lock`; cancelación real de hilos via `threading.Event` por intento
- `linkedinleads/backend/scraper.py` — Playwright + playwright_stealth; modos `index` y `enrich`; `login_with_credentials()` con soporte CAPTCHA/VNC y cancelación via `cancel_event`
- `linkedinleads/backend/linkedin_main.py` — orquesta `run_index()` y `run_enrich()`; watchdog 90s por perfil
- `linkedinleads/backend/db.py` — sqlite3 síncrono; tablas: `contacts`, `contact_queue`, `runs`, `accounts`, `trigger_log`
- `linkedinleads/backend/email_enrichment.py` — enriquecimiento email via Hunter.io (`email-finder` endpoint, con nombre+apellido+score threshold)

---

## Data and Integrations

**Stores por servicio:**
| Servicio | Tablas clave |
|---|---|
| instaleads | ig_leads, ig_scrape_jobs, ig_daily_stats, ig_skipped, ig_health_log |
| mapleads | leads, scrape_jobs, job_locations |
| linkedinleads | contacts, contact_queue, runs, accounts, trigger_log |

**Servicios externos:**
- **Instagram** (instagrapi) — requiere sesión autenticada para modo `followers`; modo `dorking` usa Google CSE sin sesión
- **Google Maps** — curl_cffi con TLS fingerprinting; sin auth oficial
- **LinkedIn** — Playwright stealth; sesión en `.pkl`; overlay contact-info via SPA click; CAPTCHA via noVNC
- **Hunter.io** — enriquecimiento email LinkedIn: endpoint `email-finder` (no `domain-search`) con nombre + apellido + score ≥50 (`HUNTER_API_KEY`)

**Config env clave (no secrets):**
- `HTTPS_ONLY` — `true` en producción
- `SESSIONS_DIR` — `/app/sessions` en Docker
- `DB_PATH` — path a SQLite por servicio
- `LINKEDIN_INDEX_MIN_INTERVAL_SECONDS` — cadencia entre index runs (default 3600s)

---

## Flows

**Flow LinkedIn — Login con CAPTCHA:**
1. UI → `POST /api/linkedin/accounts` con `{email, password}`
2. Backend: check `_login_status` (guarda de 409); si hay entrada con status bloqueante Y no es stale (>15min), lanza 409
3. Si stale o libre: crea `threading.Event cancel_event`, registra en `_login_cancel_events[account_key]`, lanza `_do_add_account` en hilo separado
4. Hilo: `login_with_credentials(..., use_xvfb=True, cancel_event=cancel_event)`
5. Arranca Xvfb (:99), Chrome no-headless sobre Xvfb
6. LinkedIn → si CAPTCHA detectado (`_has_captcha_iframe()`): `_start_vnc_session()` (x11vnc + websockify:6080), `on_status_change("waiting_captcha", ..., vnc_token=token)`, deadline extendido a 600s
7. UI: polling cada 3s → estado `waiting_captcha` → muestra iframe noVNC (nginx reescribe `/novnc/` → `linkedinleads:6080`)
8. Usuario resuelve CAPTCHA en el iframe → LinkedIn redirige al feed → `_is_logged_in()` detecta → sesión guardada como `.pkl`
9. Estado `success` → UI muestra confirmación, oculta panel VNC
10. "Cancelar login anterior": `DELETE /api/linkedin/accounts/login-status?account=X` → señala `cancel_event.set()` → hilo detecta en ≤2s en el `time.sleep(2)` del poll loop → retorna `cancelled` → `_do_add_account` lo trata como "failed"

**Flow LinkedIn — Enrich:**
1. UI → `POST /api/linkedin/search` con `{mode: "enrich", account, max_contacts}`
2. Toma contactos `pending` de `contact_queue`, procesa hasta `max_contacts`
3. Por cada contacto:
   a. Navega a `linkedin.com/in/{slug}` con `domcontentloaded` + sleep(2.5)
   b. Hace `element.click()` en el enlace SPA `a[href*='overlay/contact-info']` (NO goto directo)
   c. Espera `wait_for_url("**/overlay/contact-info/**")` para confirmar navegación SPA
   d. Si URL confirmada: `wait_for_selector("a[href^='mailto:']", 6s)` para esperar render del email
   e. Lee DOM del modal: emails, teléfonos, URLs sociales
   f. Second-chance fallback si `_clicked_link` o hay teléfonos: scan mailto en DOM completo
   g. Watchdog 90s por perfil; 2 reintentos por contacto
4. Hunter.io enrich: `email-finder` con first_name + last_name + domain, score ≥50
5. Actualiza `contacts` + marca `contact_queue` como `done`

**Flow Auth (scraperLead-web):**
1. Request → `SessionMiddleware` → `auth_middleware`
2. Ruta pública → pasa directo
3. Check IP whitelist → 403 si IP no autorizada
4. Check `session["user"]` → 401 JSON para `/api/*`, redirect `?next=` para HTML
5. POST `/auth/login` → `verify_password()` bcrypt → `session["user"] = username`

---

## LinkedIn Overlay — Decisiones Técnicas Críticas

### Por qué NO se puede navegar directamente a la URL overlay
`driver.goto("linkedin.com/in/{slug}/overlay/contact-info/")` causa redirect de LinkedIn a la página de perfil. LinkedIn intercepta la navegación directa al overlay.

**Solución:** `element.click()` en el enlace SPA después de cargar el perfil.

### Por qué `a[href^='mailto:']` NO está en `CONTACT_OVERLAY_WAIT_SELECTOR`
Algunos perfiles tienen links mailto inline fuera del modal. Incluirlo causaría falsos positivos antes de abrir el overlay.

**Solución:** Esperar mailto **solo** después de `wait_for_url("**/overlay/contact-info/**")` confirmada.

### Cancelación del login
`DELETE /api/linkedin/accounts/login-status?account=X` señala el `threading.Event` del hilo activo. El polling loop del scraper comprueba `cancel_event.is_set()` en cada iteración (cada 2s). El hilo sale limpiamente, el status queda como "failed" (no bloqueante), y el slot queda libre para un nuevo intento.

### CAPTCHA y VNC
LinkedIn bloquea IPs de datacenter (OVH) con reCAPTCHA en `/login`. La solución usa Xvfb + Chrome no-headless + x11vnc + websockify (puerto 6080) + iframe noVNC en la UI. Solo funciona en contenedor Docker Linux (Xvfb no disponible en macOS).

---

## Tests and Quality

| Módulo | Tests | Estado |
|---|---|---|
| instaleads | 24 | **3 fallan** |
| mapleads | 117 | **1 falla** |
| linkedinleads | 173 | Todos pasan |
| scraperLead-web | 0 | Sin cobertura |

**Tests rotos conocidos:**
- `instaleads/tests/test_ig_health.py` (2): mockean `ig_get` que ya no existe.
- `instaleads/tests/test_ig_profile.py::test_get_profile_extracts_business_email`: `example.com` filtrado por `_is_junk_email()`.
- `mapleads/tests/test_routes.py::test_leads_all_dedupes_by_place_id_keeps_most_recent`: asume deduplicación eliminada por diseño.

---

## Active Decisions

- **`_login_status` in-memory con TTL 15min:** si un hilo muere sin limpiar, el siguiente intento después de 15min limpia automáticamente la entrada stale.
- **`threading.Event` por intento (no por cuenta):** evita race condition donde cancelar y reintentar mataría el nuevo hilo en lugar del viejo.
- **Hunter.io `email-finder` (no `domain-search`):** `domain-search` devolvía emails de cualquier empleado de la empresa, no de la persona concreta. `email-finder` con nombre+apellido es persona-específico.
- **`leads.place_id` sin UNIQUE (mapleads):** permite guardar visitas repetidas en distintos jobs. Deduplicación a nivel de job, no global.
- **linkedinleads usa sqlite3 síncrono:** el scraper corre en hilo separado (Playwright no es async-nativo).
- **TikTok como stub:** templates y rutas existen pero no hay módulo backend.
- **Session `.pkl` se borra al eliminar cuenta:** `DELETE /api/linkedin/accounts/{username}` también borra el archivo `.pkl` de sesión, forzando login limpio en el siguiente intento.

---

## Recent Changes Log

- **2026-07-12:** Instagram — modo Seguidores (Modo B) restaurado y funcionando. Causa raíz del fallo: `web_profile_info` y la API privada devuelven 429/`require_login` sin sesión desde IPs de datacenter. Añadido `ig_session.py` (sesión vía `IG_SESSIONID`), `ig_get_authenticated` en `ig_client.py`, y `ig_followers.py` que pagina la lista completa de seguidores vía el cursor `max_id` de `friendships/{id}/followers/` (supera el límite de ~50 de la web de escritorio). Nueva UI "Modo B — Seguidores" con gate por sesión. Health check reporta `session_active`/`followers_available`.

- **2026-07-12:** Google Maps — reparado el scraper con búsqueda `pb` paginada (PR #1). Ver historial de `mapleads`.

- **2026-05-08:** Fix raíz: `initLinkedInForm()` se llamaba dos veces (app.js + linkedin.html). Cada click enviaba 2 POSTs → el primero 200, el segundo 409 → dos mensajes contradictorios en UI. Eliminada la llamada duplicada del template. Commit: `fd2018e`

- **2026-05-08:** Cancelación real de login via `threading.Event` por intento de login. `_cancelled_logins` set (roto, race condition) reemplazado por `_login_cancel_events: dict[str, threading.Event]`. El scraper comprueba `cancel_event.is_set()` tras cada `sleep(2)`. Cancelar = hilo sale en ≤2s. Commits: `650b2d7`, `695cfe5`

- **2026-05-08:** TTL de 15 min en guarda de 409 (`_is_login_status_stale`). Statuses bloqueantes con más de 15 min se auto-limpian en el siguiente intento. Commit: `650b2d7`

- **2026-05-08:** `DELETE /api/linkedin/accounts/{username}` borra también el `.pkl` de sesión. Commit: `08926a0`

- **2026-05-08:** Fix Hunter.io: cambiado de `domain-search` (devuelve cualquier empleado) a `email-finder` (persona-específico con first_name + last_name + score ≥50). Commit: `40d50f4`

- **2026-05-07:** Fix extracción email overlay LinkedIn: `wait_for_selector("a[href^='mailto:']", 6s)` después de confirmar URL overlay. Fix para Raquel Romero (`raqueeel.rg18@gmail.com`). Commit: `5ac99d0`

- **2026-05-07:** Añadido noVNC para resolución visual de CAPTCHA en login LinkedIn. Xvfb + x11vnc + websockify + iframe en UI. Commits: `894bce6`, `422817a`, `7e64e37`

- **2026-04-29:** Fix crítico linkedinleads: Playwright en macOS ARM64 crasheaba; overlay exclusivo (Voyager API 410 Gone); selectores DOM actualizados.

- **2026-04-29:** Despliegue HTTPS VPS OVH con Let's Encrypt. Nginx Docker en puerto 9090.

- **2026-04-27:** Sistema de autenticación session-based en scraperLead-web. bcrypt + IP whitelist.

---

## Next Recommended Checks

- **Arreglar 3 tests rotos de instaleads:** `test_ig_health.py` (mocks desactualizados), `test_ig_profile.py` (dominio junk).
- **Arreglar 1 test roto de mapleads:** `test_leads_all_dedupes_by_place_id_keeps_most_recent`.
- **Verificar VNC en producción con CAPTCHA real:** el flujo VNC no se ha podido validar end-to-end porque LinkedIn no siempre muestra CAPTCHA (depende de heurísticas de IP).
- **Rate limiting en `/auth/login`:** sin protección contra fuerza bruta.
- **TikTok:** decidir entre implementar o eliminar los stubs.
- **Tests para scraperLead-web:** mínimo middleware auth y lógica proxy.
