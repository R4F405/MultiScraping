<div align="center">

# 🕸️ MultiScraping

**Suite de captación de leads desde Google Maps, Instagram y LinkedIn con panel web unificado.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docker.com)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

[🚀 Inicio rápido](#-arranque-rápido) · [📦 Instalación completa](#-instalación-completa) · [🐳 Docker](#-despliegue-con-docker) · [⚙️ Variables de entorno](#️-variables-de-entorno)

</div>

---

## ¿Qué es MultiScraping?

MultiScraping es una plataforma modular de scraping con **3 backends independientes** y **1 panel web unificado** para generar bases de datos de contactos (emails, teléfonos y metadatos) exportables a CSV.

| Módulo | Puerto | Fuente |
|---|---|---|
| `mapleads` | `8001` | Google Maps |
| `instaleads` | `8002` | Instagram |
| `linkedinleads` | `8003` | LinkedIn |
| `scraperLead-web` | `8081` | Panel unificado |

---

## 🚀 Arranque rápido

> Si es tu primera vez, ve directamente a la [instalación completa](#-instalación-completa).

### macOS / Linux

```bash
git clone https://github.com/R4F405/MultiScraping
cd MultiScraping
./start_all.sh
```

### Windows (manual)

```cmd
git clone https://github.com/R4F405/MultiScraping
cd MultiScraping
```

Abre 4 terminales (una por módulo), activa su `venv` y arranca cada servicio:

```bash
# Terminal 1 — MapLeads
cd mapleads && uvicorn backend.main:app --host 0.0.0.0 --port 8001

# Terminal 2 — InstaLeads
cd instaleads && uvicorn backend.main:app --host 0.0.0.0 --port 8002

# Terminal 3 — LinkedInLeads
cd linkedinleads && uvicorn backend.main:app --host 0.0.0.0 --port 8003

# Terminal 4 — Frontend
cd scraperLead-web && python main.py
```

Panel disponible en → **`http://localhost:8081`**

---

## 📦 Instalación completa

### Paso 1 · Clonar el repositorio

```bash
git clone https://github.com/R4F405/MultiScraping
cd MultiScraping
```

### Paso 2 · Crear entornos e instalar dependencias

<details>
<summary><b>macOS / Linux</b></summary>

```bash
# mapleads
cd mapleads && python3 -m venv venv && source venv/bin/activate
cp .env.example .env && pip install -r requirements.txt && deactivate

# instaleads
cd ../instaleads && python3 -m venv venv && source venv/bin/activate
cp env.example .env && pip install -r requirements.txt && deactivate

# linkedinleads
cd ../linkedinleads && python3 -m venv venv && source venv/bin/activate
cp .env.example .env && pip install -r requirements.txt
python -m playwright install chromium && deactivate

# frontend
cd ../scraperLead-web && python3 -m venv venv && source venv/bin/activate
cp .env.example .env && pip install -r requirements.txt && deactivate
cd ..
```

</details>

<details>
<summary><b>Windows (CMD)</b></summary>

```cmd
REM mapleads
cd mapleads && python -m venv venv && venv\Scripts\activate
copy .env.example .env && pip install -r requirements.txt && deactivate

REM instaleads
cd ..\instaleads && python -m venv venv && venv\Scripts\activate
copy env.example .env && pip install -r requirements.txt && deactivate

REM linkedinleads
cd ..\linkedinleads && python -m venv venv && venv\Scripts\activate
copy .env.example .env && pip install -r requirements.txt
python -m playwright install chromium && deactivate

REM frontend
cd ..\scraperLead-web && python -m venv venv && venv\Scripts\activate
copy .env.example .env && pip install -r requirements.txt && deactivate
cd ..
```

</details>

---

## ▶️ Cómo arrancar todo

### Opción A — Script automático (macOS/Linux, recomendado)

```bash
./start_all.sh
```

> El script libera automáticamente los puertos 8001–8003 y 8081 si estuvieran ocupados.

### Opción B — Manual (macOS, Linux y Windows)

| # | Módulo | Comando |
|---|---|---|
| 1 | MapLeads | `cd mapleads` → activar venv → `uvicorn backend.main:app --host 0.0.0.0 --port 8001` |
| 2 | InstaLeads | `cd instaleads` → activar venv → `uvicorn backend.main:app --host 0.0.0.0 --port 8002` |
| 3 | LinkedInLeads | `cd linkedinleads` → activar venv → `uvicorn backend.main:app --host 0.0.0.0 --port 8003` |
| 4 | Frontend | `cd scraperLead-web` → activar venv → `python main.py` |

---

## 🐳 Despliegue con Docker

Opción recomendada para VPS. No requiere Python ni entornos virtuales.

### 1 · Configurar variables de entorno

```bash
cp docker-env.example .env
```

Edita `.env` con estos valores obligatorios:

| Variable | Descripción | Cómo generarla |
|---|---|---|
| `SESSION_SECRET` | Clave que firma las cookies de sesión | `openssl rand -hex 32` |
| `AUTH_USERS` | Usuarios y contraseñas del panel | `admin:password,user2:pass2` o hash bcrypt |
| `MAPLEADS_API_URL` | URL interna de MapLeads | `http://mapleads:8001` |
| `INSTALEADS_API_URL` | URL interna de InstaLeads | `http://instaleads:8002` |
| `LINKEDINLEADS_API_URL` | URL interna de LinkedInLeads | `http://linkedinleads:8003` |

> ⚠️ El `.env` **nunca se sube a GitHub** — está en el `.gitignore`.

### 2 · Construir y arrancar

```bash
docker compose build
docker compose up -d
```

Panel disponible en → **`http://localhost`** (puerto 80 vía Nginx)

### 3 · Gestión de contenedores

```bash
docker compose logs -f                 # Ver todos los logs
docker compose logs scraperleadweb    # Solo el frontend
docker compose down                    # Parar (los datos persisten)
docker compose restart                 # Reiniciar sin reconstruir
docker compose up -d --build          # Reconstruir y arrancar
```

### 4 · VPS con dominio y HTTPS

```bash
# 1. Obtener certificado SSL
certbot certonly --standalone -d tudominio.com

# 2. Copiar certificados
cp /etc/letsencrypt/live/tudominio.com/fullchain.pem nginx/certs/
cp /etc/letsencrypt/live/tudominio.com/privkey.pem   nginx/certs/

# 3. Descomentar bloque HTTPS en nginx/nginx.conf y configurar el dominio
# 4. Activar HTTPS en .env
echo "HTTPS_ONLY=true" >> .env

# 5. Reconstruir
docker compose up -d --build
```

---

## ⚙️ Variables de entorno

<details>
<summary><b>mapleads/.env</b></summary>

| Variable | Descripción |
|---|---|
| `WEBSHARE_PROXY_USER/PASS/HOST/PORT` | Credenciales del proxy rotativo |
| `PROXY_LIST` | Lista CSV de URLs `http://user:pass@host:port` (prioridad sobre variables sueltas) |
| `DB_PATH` | Ruta SQLite (por defecto `./data/mapleads.db`) |
| `MAX_CONCURRENT_REQUESTS` | Concurrencia máxima |
| `REQUEST_DELAY_MIN/MAX_SECONDS` | Delays entre peticiones |
| `MAX_REQUESTS_PER_DAY` | Límite duro diario |
| `DEDUPE_DAYS` | Ventana para no repetir negocios recientes |
| `API_KEY` | Si está definida, requiere `X-API-Key` en cabeceras |

</details>

<details>
<summary><b>instaleads/.env</b></summary>

| Variable | Descripción |
|---|---|
| `IG_PROXY_LIST` | Lista de proxies para Instagram |
| `IG_LIMIT_DAILY_UNAUTHENTICATED` | Límite diario sin autenticación |
| `IG_DELAY_UNAUTH_MIN/MAX` | Delays para peticiones sin autenticación |
| `IG_CONCURRENCY` | Concurrencia del scraper |
| `IG_MAX_RETRIES` | Reintentos máximos |
| `DB_PATH` | Ruta de la base de datos SQLite |

</details>

<details>
<summary><b>linkedinleads/.env</b></summary>

| Variable | Descripción |
|---|---|
| `LINKEDIN_API_PORT` | Puerto FastAPI (por defecto `8003`) |
| `MAX_CONTACTS_PER_DAY` | Límite diario de contactos |
| `SCRAPE_WINDOW_START/END` | Ventana horaria de scraping |
| `HEADLESS` | Modo headless para Playwright |
| `TELEGRAM_BOT_TOKEN/CHAT_ID` | Notificaciones vía Telegram |
| `CREDENTIAL_KEY` | Cifrado Fernet para credenciales |
| `HUNTER_API_KEY` | Enrichment de emails (Hunter.io) |
| `SNOV_CLIENT_ID/SECRET` | Enrichment de emails (Snov.io) |

</details>

<details>
<summary><b>scraperLead-web/.env</b></summary>

| Variable | Descripción |
|---|---|
| `MAPLEADS_API_URL` | URL base de MapLeads (por defecto `http://localhost:8001`) |
| `INSTALEADS_API_URL` | URL base de InstaLeads (por defecto `http://localhost:8002`) |
| `LINKEDINLEADS_API_URL` | URL base de LinkedInLeads (por defecto `http://localhost:8003`) |
| `MAPLEADS_API_KEY` | API key opcional para MapLeads |
| `PORT` | Puerto del panel (por defecto `8081`) |

</details>

---

## 🏗️ Estructura del proyecto

```
MultiScraping/
├── mapleads/          # Backend FastAPI — Google Maps + verificación de emails
├── instaleads/        # Backend FastAPI — Instagram (discovery + enrichment)
├── linkedinleads/     # Backend FastAPI — LinkedIn con Playwright
├── scraperLead-web/   # Frontend FastAPI + Jinja + JS (dashboard unificado)
├── nginx/             # Configuración Nginx para Docker/VPS
├── docker-compose.yml
└── start_all.sh       # Script de arranque (macOS/Linux)
```

---

## ✅ Comprobación rápida

Una vez arrancados todos los servicios, verifica que todo funciona:

```bash
# Panel principal
open http://localhost:8081

# Health checks de cada backend
curl http://localhost:8001/api/health            # MapLeads
curl http://localhost:8002/api/instagram/health  # InstaLeads
curl http://localhost:8003/api/linkedin/health   # LinkedInLeads
```

---

## 🧪 Tests

Cada backend incluye tests con `pytest`:

```bash
cd mapleads && pytest
cd ../instaleads && pytest
cd ../linkedinleads && pytest
```

---

## ⚠️ Aviso

> Las rutas de TikTok (`/tiktok`, etc.) están presentes en el frontend pero **no tienen backend activo** en este repositorio. Esas secciones quedan sin servicio salvo que integres uno por tu cuenta.

---

<div align="center">
Made with ☕ by <a href="https://github.com/R4F405">R4F405</a>
</div>
