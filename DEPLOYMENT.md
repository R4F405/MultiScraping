# Despliegue de multiScraping en VPS

## Configuración crítica: ROOT_PATH

Si estás desplegando multiScraping bajo un sub-path como `/scraper` (para evitar que Chrome bloquee puertos personalizados), **DEBES** configurar `ROOT_PATH` en tu archivo `.env`.

### ¿Por qué es crítico ROOT_PATH?

- **Sin ROOT_PATH**, FastAPI no sabe que está bajo `/scraper/`, generando URLs incorrectas
- `url_for()` generará `/auth/login` en lugar de `/scraper/auth/login`
- `window.__BASE__` en JavaScript será una cadena vacía `""`
- Los fetch() irán a `/api/...` en lugar de `/scraper/api/...`
- **Resultado**: Todos los botones, mapas, tabs y widgets fallarán en el servidor

### Cómo configurar ROOT_PATH

1. **En docker-compose.yml** (ya incluido):
   ```yaml
   scraperleadweb:
     environment:
       ROOT_PATH: /scraper
   ```

2. **En .env** (CRÍTICO para el servidor):
   ```env
   ROOT_PATH=/scraper
   ```

   Si ejecutas sin Docker en el servidor, DEBE estar en tu `.env`.

3. **Verificar que FastAPI lo detectó**:
   ```bash
   # Ver logs al arrancar
   docker compose logs scraperleadweb | grep "root_path"
   
   # O verificar manualmente
   curl -s http://localhost:9081/docs | grep -i "root_path"
   ```

## Despliegue local (desarrollo)

```bash
# 1. Clonar y configurar
git clone <repo>
cd multiScraping

# 2. Copiar .env template y rellenar
cp docker-env.example .env
# Editar .env con tus valores

# 3. Levantar todo
docker compose up -d --build

# 4. Probar acceso
http://localhost/scraper/
```

## Despliegue en VPS (con Nginx)

```bash
# 1. En el servidor
git clone <repo>
cd multiScraping

# 2. Crear .env con valores de producción
cat > .env <<EOF
SESSION_SECRET=$(openssl rand -hex 32)
AUTH_USERS=admin:<password_hash>
HTTPS_ONLY=true
ROOT_PATH=/scraper

MAPLEADS_API_URL=http://mapleads:8001
INSTALEADS_API_URL=http://instaleads:8002
LINKEDINLEADS_API_URL=http://linkedinleads:8003

# ... resto de variables
EOF

# 3. Levantar
docker compose up -d --build

# 4. Verificar
curl -vk https://tu-dominio.com/scraper/
# Debe redirigir a https://tu-dominio.com/scraper/auth/login
```

## Checklist de debugueo si JS no funciona en servidor

- [ ] ¿`ROOT_PATH=/scraper` está en `.env`?
- [ ] ¿Los logs muestran que FastAPI detectó `root_path`?
- [ ] ¿Abriendo DevTools → Console ve `window.__BASE__ = "/scraper"`?
- [ ] ¿Los fetch() van a `https://dominio/scraper/api/...` o a `/api/...`?
- [ ] ¿Nginx está ruteando `/scraper/` correctamente? `curl -vk https://dominio/scraper/`

## Problemas comunes

| Problema | Causa | Solución |
|----------|-------|----------|
| Botones no responden | `window.__BASE__ = ""` | Agrega `ROOT_PATH=/scraper` a `.env` |
| URLs rotas en templates | `url_for()` sin root_path | Verifica FastAPI recibió `ROOT_PATH` |
| Proxy widget no aparece | fetch() a `/api/proxy/status` falla | Verifica window.__BASE__ y fetch() prefijo |
| Mapa Leaflet no carga | fetch() a `/api/maps/categories` falla | Mismo que arriba |

## Referencias

- [Plan completo de sub-path deployment](./DEPLOYMENT-PLAN.md)
- [FastAPI root_path docs](https://fastapi.tiangolo.com/advanced/sub-applications/)
- [Arquitectura multiScraping](./CLAUDE.md)
