# 🎵 TikTokLeads - Scraper de TikTok para Lead Generation

Microservicio backend para extraer emails de creadores de TikTok por hashtag/keyword/nicho.

## 📦 Stack

- **FastAPI** - Web framework async
- **Playwright** - Browser automation (Chromium)
- **SQLite** - Base de datos persistente (aiosqlite)
- **dnspython** - Verificación de MX records
- **curl_cffi** - Visita webs externas para extraer emails

## 🏗️ Arquitectura

```
tiktokleads/
├── backend/
│   ├── main.py                 # FastAPI lifespan + CORS + router
│   ├── config/settings.py      # Config desde .env
│   ├── api/
│   │   ├── routes.py           # Endpoints + job runner
│   │   └── schemas.py          # Pydantic models
│   ├── storage/
│   │   ├── database.py         # CRUD + migrations
│   │   └── exporter.py         # CSV export
│   └── tiktok/
│       ├── tt_browser.py       # Playwright session + XHR intercept
│       ├── tt_search.py        # Búsqueda de creadores
│       ├── tt_profile.py       # Extracción perfil + emails
│       ├── tt_rate_limiter.py  # Rate limiting
│       ├── tt_deduplicator.py  # Set en memoria
│       ├── tt_health.py        # Estado del servicio
│       ├── tt_retry.py         # Retry con backoff
│       ├── email_finder.py     # Visita bio links
│       └── email_verifier.py   # MX verification
├── tests/
│   ├── conftest.py
│   ├── test_database.py        # 11 tests
│   ├── test_routes.py          # 11 tests
│   └── test_tt_profile.py      # 6 tests (28/32 total)
├── data/                       # Creada en runtime (gitignored)
├── .env                        # Configuración (gitignored)
├── env.example                 # Plantilla .env
├── pytest.ini                  # Pytest config
└── requirements.txt
```

## 🚀 Instalación

### Requisitos previos
- Python 3.10+
- pip

### Setup

```bash
cd tiktokleads

# Copiar configuración
cp env.example .env

# Instalar dependencias
pip install -r requirements.txt

# Instalar Chromium para Playwright
playwright install chromium
```

### Variables de entorno

Ver `env.example`. Las principales:

```env
DB_PATH=./data/tiktokleads.db           # Ubicación de BD
PORT=8004                                # Puerto del servidor
TIKTOK_HEADLESS=true                    # Navegador headless
TIKTOK_PROXY_URL=                       # Proxy (opcional)
MAX_REQ_HOUR=40                         # Requests/hora
MAX_DAILY=200                           # Requests/día
DELAY_MIN=3.0                           # Delay mín entre requests
DELAY_MAX=8.0                           # Delay máx (aleatorio)
MAX_CONCURRENT_WORKERS=1                # Workers simultáneos (TikTok es sensible)
```

## ▶️ Ejecución

### Servidor individual (puerto 8004)

```bash
uvicorn backend.main:app --port 8004
```

### Con start_all.sh (recomendado - incluye MapLeads, InstaLeads, LinkedInLeads, Frontend)

```bash
cd /Users/miquelroca/Desktop/practicas/leads
./start_all.sh
```

## 📊 Tests

```bash
# Todos los tests (32)
pytest tests/ -v

# Test específico
pytest tests/test_database.py::test_create_job_returns_uuid -v

# Con coverage
pytest tests/ --cov=backend --cov-report=html
```

**Resultado esperado:** 32/32 ✅

## 🔌 API Endpoints

### Health & Status

```
GET /api/tiktok/health              → {status, requests_today, limits, ...}
GET /api/tiktok/stats               → {total_leads, total_skipped, running_jobs}
GET /api/tiktok/limits              → {requests_today, requests_this_hour, max_daily, ...}
GET /api/tiktok/debug/last          → {last_lead, stats}
```

### Jobs

```
POST /api/tiktok/search             → {job_id, status}
  Body: {
    target: "#fotógrafo",           # Hashtag o keyword
    email_goal: 20,                 # Objetivo de emails
    min_followers: 0                # Filtro de followers
  }

GET /api/tiktok/jobs                → [{job_id, target, status, ...}]
GET /api/tiktok/jobs/{job_id}       → {job_id, status, progress, ...}
```

### Results

```
GET /api/tiktok/leads               → [{username, email, ...}]
GET /api/tiktok/leads?job_id=XXX    → [{...}]  (resultados de un job)
GET /api/tiktok/export/{job_id}     → CSV (binary)
```

## 🔄 Job Lifecycle

1. **POST /search** → Crea job con estado `running`
2. **Polling** (GET /jobs/{id}) → `running` → observa progreso
3. **Completion** → `completed` o `completed_partial`
4. **Export** (GET /export/{id}) → CSV descargable
5. **History** (GET /jobs) → lista de todos

### Job States

| Estado | Significado |
|--------|------------|
| `running` | En curso |
| `completed` | Objetivo alcanzado |
| `completed_partial` | Objetivo no alcanzado pero se guardaron resultados |
| `rate_limited` | Pausado por límite de TikTok |
| `failed` | Error interno |

## 🔍 Cómo Funciona

### 1. Búsqueda de Creadores

- Navega a `https://www.tiktok.com/search/user?q={keyword}`
- **Intercepta XHR** a `/api/search/general/full/` (firmado por el navegador real)
- Captura JSON con `secUid`, `uniqueId`, `followerCount`, etc.
- Hace scroll para paginar si faltan resultados

### 2. Extracción de Perfil

Para cada creador:
1. Visita `https://www.tiktok.com/@{username}`
2. Extrae `__UNIVERSAL_DATA_FOR_REHYDRATION__` (JSON en HTML)
3. Parsea: nickname, bio, followers, bioLink, verificado

### 3. Extracción de Email

**Opción A: Bio**
- Regex en el campo `signature` (bio)
- Detecta emails como `foto@studio.es`
- Filtra patrones falsos (`@2x`, `example.com`, etc.)

**Opción B: Bio Link**
- Si no hay email en bio pero hay `bioLink` (ej: `instagram.com/perfil`)
- Visita el sitio web externo
- Busca emails en homepage y contacto
- Retorna emails encontrados

### 4. Verificación

- MX record check: `dnspython`
- Valida que el dominio del email existe
- Descarta emails inválidos

## 🛡️ Anti-Detección

Playwright es detectado como bot por TikTok, mitigamos:

```javascript
// En Playwright context:
- navigator.webdriver → undefined
- navigator.plugins → [1,2,3,4,5]
- navigator.languages → ['es-ES', 'es', 'en-US']
- window.chrome.runtime → {}
- User-Agent → Chrome 131 real
- viewport → 1280x800
- locale → es-ES
```

Aún así, TikTok puede bloquear después de ~40 requests/hora.

## ⏱️ Rate Limiting

### Ventana Horaria (Rolling Window)

- Máx **40 requests/hora** (configurable)
- Ventana deslizante de 60 minutos
- Si alcanza: espera hasta que salga del rango

### Límite Diario

- Máx **200 requests/día** (configurable)
- Se resetea a las 00:00 UTC
- Si alcanza: espera hasta mañana

### Estrategia

- Cada búsqueda = 1 request (crear job)
- Cada perfil procesado = ~0.5 request (XHR intercept compartida)
- Delays aleatorios 3-8s entre perfiles

## 🗄️ Base de Datos

SQLite con 4 tablas:

```sql
tt_scrape_jobs       -- Historial de búsquedas
tt_leads             -- Emails encontrados
tt_skipped           -- Perfiles omitidos (razón)
tt_daily_stats       -- Requests por día
```

Migraciones **additive** (ALTER TABLE ADD COLUMN):
- Compatible con versiones anteriores
- Ejecutadas en `init_db()`

## 📋 CSV Export

**Formato:** UTF-8 con BOM (Excel-compatible)

**Columnas:**
- usuario
- nickname
- email
- email_source (bio|biolink)
- followers_count
- verified (Sí/No)
- bio_link
- profile_url (https://www.tiktok.com/@...)
- bio_text
- scraped_at (fecha ISO)

## 🎯 Próximos Pasos (Futuros)

- [ ] Verificación 2FA para sesión directa
- [ ] Extracción de comentarios (más data)
- [ ] Webhook para notificaciones de job completado
- [ ] Dashboard con gráficas de leads/día
- [ ] Caché inteligente de creadores
- [ ] Integración con CRM (Pipedrive, HubSpot)

## 🐛 Troubleshooting

Ver `../TIKTOK_TROUBLESHOOTING.md` para:
- Errores de rate limiting
- Bot detection
- Errores de conexión
- Errores de BD
- Y mucho más con soluciones detalladas

## 📝 Logs

En desarrollo (uvicorn):
```bash
uvicorn backend.main:app --port 8004 --log-level debug
```

En producción:
```bash
uvicorn backend.main:app --port 8004 --log-level info
```

## 🔗 Integración Frontend

Proxy en `scraperLead-web/main.py`:

```python
@app.post("/api/tiktok/search")
@app.get("/api/tiktok/jobs")
@app.get("/api/tiktok/leads")
@app.get("/api/tiktok/export/{job_id}")
# ... todo ruteado a :8004
```

UI en `scraperLead-web/templates/tiktok.html`:
- 3 tabs: Extracción / Scrapeos / Leads
- Polling con barra de progreso
- Tabla interactiva
- Exportación CSV

## 📦 Dependencias Principales

| Package | Versión | Uso |
|---------|---------|-----|
| fastapi | ≥0.111.0 | Web framework |
| uvicorn | ≥0.29.0 | ASGI server |
| aiosqlite | ≥0.20.0 | SQLite async |
| playwright | ≥1.44.0 | Browser automation |
| curl_cffi | ≥0.7.0 | HTTP async (email finder) |
| dnspython | ≥2.6.0 | MX verification |
| pytest | ≥8.0.0 | Testing |

---

## 📄 Licencia

Uso personal. No redistribuir sin permiso.

---

**Última actualización:** Abril 2026

**Estado:** ✅ Producción lista
