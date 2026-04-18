# MultiScraper

Suite para captar leads desde Google Maps, Instagram y LinkedIn, con panel web unificado.

## Arranque rapido

Si es tu primera vez, pasa al siguiente punto y sigue la instalación completa (entornos virtuales + `.env`).

#### macOS / Linux

```bash
git clone <URL_DEL_REPO>
cd multiScraping
./start_all.sh
```

#### Windows (manual rápido)

```cmd
git clone <URL_DEL_REPO>
cd multiScraping
```

Luego abre 4 terminales (una por módulo), activa su `venv` y arranca:

- `mapleads`: `uvicorn backend.main:app --host 0.0.0.0 --port 8001`
- `instaleads`: `uvicorn backend.main:app --host 0.0.0.0 --port 8002`
- `linkedinleads`: `uvicorn backend.main:app --host 0.0.0.0 --port 8003`
- `scraperLead-web`: `python main.py`

URL del panel:

- `http://localhost:8081`

### Instalación completa (macOS)

#### 1) Clonar y entrar al repo

```bash
git clone <URL_DEL_REPO>
cd multiScraping
```

#### 2) Crear entorno e instalar dependencias por módulo

```bash
# mapleads
cd mapleads
python3 -m venv venv
source venv/bin/activate
cp .env.example .env
pip install -r requirements.txt
deactivate

# instaleads
cd ../instaleads
python3 -m venv venv
source venv/bin/activate
cp env.example .env
pip install -r requirements.txt
deactivate

# linkedinleads
cd ../linkedinleads
python3 -m venv venv
source venv/bin/activate
cp .env.example .env
pip install -r requirements.txt
python -m playwright install chromium
deactivate

# frontend
cd ../scraperLead-web
python3 -m venv venv
source venv/bin/activate
cp .env.example .env
pip install -r requirements.txt
deactivate
```

### Instalación completa (Windows - CMD)

#### 1) Clonar y entrar al repo

```cmd
git clone <URL_DEL_REPO>
cd multiScraping
```

#### 2) Crear entorno e instalar dependencias por módulo

```cmd
REM mapleads
cd mapleads
python -m venv venv
venv\Scripts\activate
copy .env.example .env
pip install -r requirements.txt
deactivate

REM instaleads
cd ..\instaleads
python -m venv venv
venv\Scripts\activate
copy env.example .env
pip install -r requirements.txt
deactivate

REM linkedinleads
cd ..\linkedinleads
python -m venv venv
venv\Scripts\activate
copy .env.example .env
pip install -r requirements.txt
python -m playwright install chromium
deactivate

REM frontend
cd ..\scraperLead-web
python -m venv venv
venv\Scripts\activate
copy .env.example .env
pip install -r requirements.txt
deactivate
```

## Cómo arrancar todo

### Opción A (recomendada en macOS/Linux): todo con un comando

```bash
./start_all.sh
```

URLs:

- Frontend: `http://localhost:8081`
- MapLeads API: `http://localhost:8001`
- InstaLeads API: `http://localhost:8002`
- LinkedIn API: `http://localhost:8003`

El script libera los puertos 8001–8003 y 8081 si estaban ocupados. El frontend usa el `venv` de `scraperLead-web` si existe; si no, reutiliza el de `mapleads`.

### Opción B (manual, sirve en macOS y Windows)

Arranca cada proceso en su terminal:

1. MapLeads:
   - `cd mapleads`
   - activar venv
   - `uvicorn backend.main:app --host 0.0.0.0 --port 8001`
2. InstaLeads:
   - `cd instaleads`
   - activar venv
   - `uvicorn backend.main:app --host 0.0.0.0 --port 8002`
3. LinkedInLeads:
   - `cd linkedinleads`
   - activar venv
   - `uvicorn backend.main:app --host 0.0.0.0 --port 8003`
4. Frontend:
   - `cd scraperLead-web`
   - activar venv
   - `python main.py`

### Opción C (solo MapLeads + frontend)

Útil para trabajar solo con Google Maps. Instagram/LinkedIn quedan desactivados en el panel (el script apunta sus URLs a un puerto vacío).

- macOS/Linux: `./start_mapleads.sh`
- Windows: doble clic en `start_mapleads.bat` (requiere haber compilado el frontend con `scraperLead-web/build_exe.bat`; el `.bat` lanza el `.exe` generado en `scraperLead-web/dist/MapLeads-Frontend/`).

## Qué es, cómo funciona y para qué sirve

- **Qué es:** una plataforma con 3 backends (`mapleads`, `instaleads`, `linkedinleads`) y 1 frontend (`scraperLead-web`).
- **Cómo funciona:** el frontend (FastAPI + Jinja + estáticos) llama a cada API, lanza jobs de scraping en segundo plano y muestra progreso y resultados.
- **Para qué sirve:** generar bases de datos de contactos (emails, teléfonos y metadatos) y exportarlas a CSV.

El frontend incluye rutas de interfaz para TikTok (`/tiktok`, etc.); en este repositorio no hay backend TikTok — en el arranque “solo MapLeads” esas secciones quedan sin servicio activo.

## Estructura del proyecto

- `mapleads`: backend FastAPI para scraping de Google Maps y verificación de emails.
- `instaleads`: backend FastAPI para captación de leads de Instagram (discovery interno, límites y enrichment HTTP).
- `linkedinleads`: backend FastAPI para scraping de LinkedIn con Playwright (cuentas, sesiones, colas).
- `scraperLead-web`: frontend FastAPI + Jinja + JS (dashboard, formularios y vistas de datos).
- `start_all.sh`: inicia en macOS/Linux los cuatro servicios.
- `start_mapleads.sh` y `start_mapleads.bat`: arranque rápido solo MapLeads + frontend.

## Requisitos mínimos

- Python 3.11+ (recomendado en todos los módulos).
- `pip` actualizado.
- LinkedIn: navegador de Playwright (Chromium) tras `pip install -r requirements.txt` en `linkedinleads`.
- MapLeads (opcional): si activas `EMAIL_SCRAPER_USE_PLAYWRIGHT=1` en `.env`, instala también `playwright` y `playwright install chromium` en ese módulo.

## Variables de entorno (.env)

Importante: no subas claves reales a GitHub. Usa valores propios. Los nombres siguientes coinciden con `backend/config` y `.env.example` / `env.example` de cada módulo.

### `mapleads/.env`

- `WEBSHARE_PROXY_USER`, `WEBSHARE_PROXY_PASS`, `WEBSHARE_PROXY_HOST`, `WEBSHARE_PROXY_PORT`: credenciales del proveedor proxy rotativo (o equivalente).
- `PROXY_LIST`: lista CSV de URLs `http://user:pass@host:port` (si está definida, tiene prioridad sobre host/puerto sueltos).
- `DB_PATH`: ruta SQLite (por defecto `./data/mapleads.db`).
- `LOG_LEVEL`: nivel de logs.
- `MAX_REQUESTS_PER_PROXY_BEFORE_COOLDOWN`, `PROXY_COOLDOWN_SECONDS`: límites por proxy.
- `MAX_CONCURRENT_REQUESTS`, `REQUEST_DELAY_MIN_SECONDS`, `REQUEST_DELAY_MAX_SECONDS`: concurrencia y delays.
- `ERROR_RATE_THRESHOLD`, `HIGH_ERROR_COOLDOWN_SECONDS`: circuit breaker por proxy.
- `MAX_REQUESTS_PER_DAY`: límite duro diario.
- `DEDUPE_DAYS`: ventana en días para no repetir negocios recientes.
- `EMAIL_DNS_ACCEPT_A`, `EMAIL_SCRAPER_USE_PLAYWRIGHT`, `EMAIL_SCRAPER_FORCE_DIRECT`: comportamiento del descubrimiento de emails en web (ver comentarios en `.env.example`).
- `API_KEY`: opcional; si está definida, el cliente debe enviar `X-API-Key` (el endpoint `GET /api/health` sigue siendo público).

### `instaleads/.env`

Plantilla: `env.example` (nombre distinto a `.env.example`). Variables usadas por `instaleads/backend/config/settings.py`:

- `PORT`: puerto del backend (por defecto `8002`).
- `DB_PATH`, `SESSION_FILE`, `LOG_LEVEL`.
- Límites de campaña: `MAX_UNAUTH_DAILY`, `MAX_AUTH_DAILY`, `MAX_AUTH_HOURLY`, `MAX_CONCURRENT_UNAUTH`, `MAX_CONCURRENT_AUTH`, `DELAY_UNAUTH_*`, `DELAY_AUTH_*`, `RETRY_*`.
- Discovery: `DISCOVERY_PROVIDER`, `DISCOVERY_MIN_COVERAGE_RATIO`, `DISCOVERY_LOGIN_ESCALATION_RATIO`.
- Enrichment: `ENRICHMENT_HTTP_TIMEOUT_SEC`, `ENRICHMENT_FOLLOW_CONTACT_PAGES`, `ENRICHMENT_MAX_SUBPAGES`.
- Proxy: `IG_PROXY_URL`, `PROXY_LIST`, `PROXY_OPEN_THRESHOLD`, `PROXY_HALF_OPEN_THRESHOLD`, `PROXY_COOLDOWN_SECONDS`.
- Otros: `INSTAGRAM_MAINTENANCE_MESSAGE`, `LIVE_SMOKE_ENABLED`.

El endpoint `GET /api/instagram/health` devuelve estado del servicio y métricas de discovery.

### `linkedinleads/.env`

Copia desde `linkedinleads/.env.example`. Entre otras, se usan:

- `LINKEDIN_API_PORT`: puerto FastAPI (por defecto `8003`).
- Límites y ventanas: `MAX_CONTACTS_PER_RUN`, `MAX_CONTACTS_PER_DAY`, `MAX_CONTACTS_CAP`, `SCRAPE_WINDOW_START`, `SCRAPE_WINDOW_END`, `MIN_HOURS_BETWEEN_RUNS`, `COOLDOWN_HOURS_AFTER_429`, `CONTACT_REFRESH_DAYS`.
- Navegador: `BROWSER_PROFILE_WAIT`, `SLEEP_BETWEEN_CONNECTIONS`, `HEADLESS`, `CHROME_BINARY`, `DRIVER_RESTART_EVERY`, `SESSIONS_DIR` (ruta de sesiones; por defecto carpeta `sessions` bajo el proyecto).
- Cuenta objetivo opcional: `LINKEDIN_PROFILE_URL`.
- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- Cifrado: `CREDENTIAL_KEY` (Fernet para credenciales guardadas).
- `DB_PATH`: SQLite; por defecto `linkedinleads/backend/data/contacts.db` si no se define.
- Enrichment opcional: `HUNTER_API_KEY`, `SNOV_CLIENT_ID`, `SNOV_CLIENT_SECRET`, `EMAIL_ENRICHMENT_ENABLED`.
- Lista de proxies opcional: `PROXY_LIST` (véase `backend/proxy_pool.py`).

### `scraperLead-web/.env`

- `MAPLEADS_API_URL`, `INSTALEADS_API_URL`, `LINKEDINLEADS_API_URL`: URLs base de los tres backends (por defecto `http://localhost:8001` … `8003`).
- `MAPLEADS_API_KEY`: opcional; si MapLeads tiene `API_KEY`, el frontend envía `X-API-Key` en las peticiones a MapLeads.
- `PORT`: puerto del panel (por defecto `8081`).

## Comprobación rápida

- Abrir `http://localhost:8081`.
- Salud de APIs (directo en cada backend):
  - MapLeads: `http://localhost:8001/api/health`
  - InstaLeads: `http://localhost:8002/api/instagram/health`
  - LinkedIn: `http://localhost:8003/health` o `http://localhost:8003/api/linkedin/health`

## Tests básicos

Cada backend incluye tests con `pytest` desde la raíz del repositorio:

```bash
cd mapleads && pytest
cd ../instaleads && pytest
cd ../linkedinleads && pytest
```
