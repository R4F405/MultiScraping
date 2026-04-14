# Multi Scraper

Suite para captar leads desde Google Maps, Instagram y LinkedIn, con panel web unificado.

## Instalacion rapida

#### macOS / Linux

```bash
git clone <URL_DEL_REPO>
cd leads
./start_all.sh
```

#### Windows (manual rapido)

```cmd
git clone <URL_DEL_REPO>
cd leads
```

Luego abre 4 terminales (una por modulo), activa su `venv` y arranca:

- `mapleads`: `uvicorn backend.main:app --host 0.0.0.0 --port 8001`
- `instaleads`: `uvicorn backend.main:app --host 0.0.0.0 --port 8002`
- `linkedinleads`: `uvicorn backend.main:app --host 0.0.0.0 --port 8003`
- `scraperLead-web`: `python main.py`

URL final:

- `http://localhost:8081`

Si es tu primera vez, sigue la instalacion completa de abajo (entornos virtuales + `.env`).

### Instalacion completa (macOS)

#### 1) Clonar y entrar al repo

```bash
git clone <URL_DEL_REPO>
cd leads
```

#### 2) Crear entorno e instalar dependencias por modulo

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

### Instalacion completa (Windows - CMD)

#### 1) Clonar y entrar al repo

```cmd
git clone <URL_DEL_REPO>
cd leads
```

#### 2) Crear entorno e instalar dependencias por modulo

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

## Como arrancar todo

### Opcion A (recomendada en macOS/Linux): todo con un comando

```bash
./start_all.sh
```

URLs:

- Frontend: `http://localhost:8081`
- MapLeads API: `http://localhost:8001`
- InstaLeads API: `http://localhost:8002`
- LinkedIn API: `http://localhost:8003`

### Opcion B (manual, sirve en macOS y Windows)

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

### Opcion C (solo MapLeads + frontend)

- macOS/Linux: `./start_mapleads.sh`
- Windows: doble clic en `start_mapleads.bat` (requiere compilar frontend con `scraperLead-web/build_exe.bat`).

## Que es, como funciona y para que sirve

- **Que es:** una plataforma con 3 backends (`mapleads`, `instaleads`, `linkedinleads`) y 1 frontend (`scraperLead-web`).
- **Como funciona:** el frontend llama a cada API, lanza jobs de scraping en segundo plano y muestra progreso/resultados.
- **Para que sirve:** generar bases de datos de contactos (emails, telefonos y metadatos) y exportarlas a CSV.

## Estructura del proyecto

- `mapleads`: backend FastAPI para scraping de Google Maps + busqueda/verificacion de emails.
- `instaleads`: backend FastAPI para scraping de Instagram (modo dorking y modo followers con login).
- `linkedinleads`: backend FastAPI para scraping de LinkedIn con gestion de cuentas/sesiones.
- `scraperLead-web`: frontend FastAPI + Jinja + JS (dashboard, formularios y vistas de datos).
- `start_all.sh`: inicia todo en macOS/Linux (`mapleads` + `instaleads` + `linkedinleads` + frontend).
- `start_mapleads.sh` y `start_mapleads.bat`: arranque rapido de MapLeads + frontend.

## Requisitos minimos

- Python 3.11+ (recomendado en todos los modulos).
- `pip` actualizado.
- En LinkedIn: instalar navegador de Playwright (Chromium).

## Variables de entorno (.env)

Importante: nunca subas claves reales a GitHub. Usa valores propios.

### `mapleads/.env`

- `WEBSHARE_PROXY_USER`: usuario del proveedor proxy.
- `WEBSHARE_PROXY_PASS`: password del proveedor proxy.
- `WEBSHARE_PROXY_HOST`: host del proxy (ej. `proxy.webshare.io`).
- `WEBSHARE_PROXY_PORT`: puerto del proxy.
- `PROXY_LIST`: lista de proxies CSV en formato URL.
- `DB_PATH`: ruta SQLite (por defecto `./data/mapleads.db`).
- `LOG_LEVEL`: nivel de logs.
- `MAX_REQUESTS_PER_PROXY_BEFORE_COOLDOWN`: requests por proxy antes de enfriarlo.
- `PROXY_COOLDOWN_SECONDS`: segundos de cooldown por proxy.
- `MAX_CONCURRENT_REQUESTS`: concurrencia total.
- `REQUEST_DELAY_MIN_SECONDS`: delay minimo entre requests.
- `REQUEST_DELAY_MAX_SECONDS`: delay maximo entre requests.
- `ERROR_RATE_THRESHOLD`: umbral de error por proxy.
- `HIGH_ERROR_COOLDOWN_SECONDS`: cooldown largo por alta tasa de error.
- `MAX_REQUESTS_PER_DAY`: limite duro diario.
- `API_KEY`: clave opcional para proteger la API por header `X-API-Key`.

### `instaleads/.env`

- `DB_PATH`: ruta SQLite (por defecto `./data/instaleads.db`).
- `SESSION_FILE`: archivo de sesion principal.
- `SESSION_KEY`: clave para cifrado de sesion.
- `LOG_LEVEL`: nivel de logs.
- `ENRICHMENT_MAX_FETCHES_PER_HOUR`: limite horario de fetch web.
- `ENRICHMENT_HTTP_TIMEOUT_SEC`: timeout HTTP para enrichment.
- `ENRICHMENT_FOLLOW_CONTACT_PAGES`: seguir subpaginas de contacto (0/1).
- `ENRICHMENT_MAX_SUBPAGES`: subpaginas maximas por dominio.
- `FOLLOWERS_AUTO_RESUME_ENABLED`: auto reintentos en modo followers (0/1).
- `FOLLOWERS_MAX_RESUMES_PER_DAY`: maximo de reanudaciones al dia.
- `MAX_UNAUTH_DAILY`: limite diario modo dorking (sin login).
- `DELAY_UNAUTH_MIN`: delay minimo modo dorking.
- `DELAY_UNAUTH_MAX`: delay maximo modo dorking.
- `MAX_CONCURRENT_UNAUTH`: concurrencia modo dorking.
- `MAX_AUTH_DAILY`: limite diario modo autenticado.
- `MAX_AUTH_HOURLY`: limite por hora modo autenticado.
- `DELAY_AUTH_MIN`: delay minimo modo autenticado.
- `DELAY_AUTH_MAX`: delay maximo modo autenticado.
- `RETRY_MAX_ATTEMPTS`: numero de reintentos.
- `RETRY_BASE_DELAY`: delay base de reintentos.
- `RETRY_MAX_DELAY`: delay maximo de reintentos.
- `SESSIONS_DIR`: carpeta de sesiones multi-cuenta.
- `CROSS_PLATFORM_ENABLED`: activa enrichment externo (0/1).
- `HUNTER_API_KEY`: API key de Hunter (opcional).
- `SNOV_CLIENT_ID`: client id de Snov (opcional).
- `SNOV_CLIENT_SECRET`: client secret de Snov (opcional).
- `IG_PROXY_URL`: proxy para Instagram (opcional).
- `GOOGLE_API_KEY`: API key de Google CSE (opcional, recomendado para dorking).
- `GOOGLE_CSE_ID`: CSE ID de Google (opcional, recomendado para dorking).
- `PORT`: puerto del backend InstaLeads (por defecto `8002`).

### `linkedinleads/.env`

- `LINKEDIN_API_PORT`: puerto del backend LinkedIn (por defecto `8003`).
- `MAX_CONTACTS_PER_RUN`: maximo de contactos por ejecucion.
- `MAX_CONTACTS_PER_DAY`: maximo diario por cuenta.
- `MAX_CONTACTS_CAP`: tope de seguridad para formularios/jobs.
- `SCRAPE_WINDOW_START`: hora inicio permitida para scraping.
- `SCRAPE_WINDOW_END`: hora fin permitida para scraping.
- `MIN_HOURS_BETWEEN_RUNS`: horas minimas entre ejecuciones.
- `COOLDOWN_HOURS_AFTER_429`: cooldown tras bloqueo/rate-limit.
- `SCHEDULED_RANDOM_DELAY_MINUTES`: retraso aleatorio de scheduler.
- `CONTACT_REFRESH_DAYS`: cada cuantos dias refrescar contactos.
- `BROWSER_PROFILE_WAIT`: espera para perfil de navegador.
- `SLEEP_BETWEEN_CONNECTIONS`: pausa entre perfiles.
- `HEADLESS`: `true/false` para modo sin UI.
- `CHROME_BINARY`: ruta manual al binario de Chrome (opcional).
- `SESSIONS_DIR`: carpeta de sesiones.
- `LINKEDIN_PROFILE_URL`: perfil objetivo opcional.
- `DRIVER_RESTART_EVERY`: reinicio del driver cada N ciclos.
- `TELEGRAM_BOT_TOKEN`: token Telegram para alertas (opcional).
- `TELEGRAM_CHAT_ID`: chat id Telegram para alertas (opcional).
- `CREDENTIAL_KEY`: clave Fernet para cifrar credenciales.
- `DB_PATH`: ruta SQLite (por defecto `linkedinleads/backend/data/contacts.db`).
- `HUNTER_API_KEY`: API key de Hunter (opcional).
- `SNOV_CLIENT_ID`: client id de Snov (opcional).
- `SNOV_CLIENT_SECRET`: client secret de Snov (opcional).
- `EMAIL_ENRICHMENT_ENABLED`: activa enrichment por proveedores externos (0/1).

### `scraperLead-web/.env`

- `MAPLEADS_API_URL`: URL del backend MapLeads.
- `INSTALEADS_API_URL`: URL del backend InstaLeads.
- `LINKEDINLEADS_API_URL`: URL del backend LinkedIn.
- `PORT`: puerto del frontend (por defecto `8081`).

## Comprobacion rapida

- Abrir `http://localhost:8081`
- Salud APIs:
  - `http://localhost:8001/api/health`
  - `http://localhost:8002/api/instagram/health`
  - `http://localhost:8003/health`

## Tests basicos

Cada modulo backend incluye tests con `pytest`:

```bash
cd mapleads && pytest
cd ../instaleads && pytest
cd ../linkedinleads && pytest
```
