# Despliegue en Coolify (con auto-deploy en cada push)

Se usa `docker-compose.coolify.yml`, que levanta los 5 servicios + un nginx
interno. Coolify (Traefik) pone el dominio y el SSL; no hace falta certbot ni
abrir puertos.

```
Internet ──HTTPS──> Traefik (Coolify) ──HTTP──> nginx:80
                                                  ├─ /        → scraperleadweb:8081
                                                  └─ /novnc/  → linkedinleads:6080
scraperleadweb → mapleads:8001 · instaleads:8002 · linkedinleads:8003 · tiktokleads:8004
```

## 1. Conectar GitHub (necesario para el auto-deploy)

Coolify → **Sources** → **+ Add** → **GitHub App** → crea la app e instálala
solo en el repo `R4F405/MultiScraping`. Con la GitHub App, Coolify recibe un
webhook en cada push sin configurar nada más.

## 2. Crear el recurso

1. **Projects** → tu proyecto → **+ New** → **Private Repository (with GitHub App)**.
2. Repo: `R4F405/MultiScraping` · Branch: `main`.
3. Build Pack: **Docker Compose**.
4. Base Directory: `/` · Docker Compose Location: `/docker-compose.coolify.yml`.
5. **Continue**.

## 3. Dominio

En la lista de servicios, solo el servicio **nginx** lleva dominio:
`https://scraper.tudominio.com` (el DNS del dominio apuntando a la IP del
servidor Coolify). Deja el resto de servicios sin dominio.

## 4. Variables de entorno

**Environment Variables** → **Developer view** y pega el contenido de tu
`.env` de producción (usa `.env.example` como plantilla). Mínimo:

```env
SESSION_SECRET=<openssl rand -hex 32>
AUTH_USERS=admin:<password>
HTTPS_ONLY=true
ROOT_PATH=
```

Las URLs internas (`*_API_URL`) y las rutas de las BBDD ya vienen fijadas en
el compose. Deja `ROOT_PATH` vacío si el panel va en la raíz del dominio.

## 5. Auto-deploy

**Advanced** → activa **Auto Deploy** (viene activado por defecto con GitHub
App). Desde ahora cada push/merge a `main` reconstruye y redespliega.

Pulsa **Deploy** la primera vez.

## Datos persistentes

Las BBDD SQLite, sesiones de LinkedIn, logs y salida viven en volúmenes de
Docker (`mapleads_data`, `instaleads_data`, `linkedin_*`, `tiktokleads_data`),
así que sobreviven a los redeploys. Para migrar datos del VPS actual, copia
los `.db` dentro de esos volúmenes (Coolify → servicio → **Storages**).
