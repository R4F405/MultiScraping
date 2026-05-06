# LiteSpeed Configuration para multiScraping /scraper/

## El Problema

El servidor VPS tiene **LiteSpeed** en puerto 80/443 sirviendo n8n. Para añadir multiScraping bajo `/scraper/`, necesitamos que LiteSpeed proxee esa ruta a Docker Nginx (que ahora escucha en `127.0.0.1:9080`).

## Solución: Opción A — Usar .htaccess (Más simple)

### 1. En el servidor VPS, editar o crear el archivo:

```bash
nano /home/vps-d6ea56ba.vps.ovh.net/public_html/.htaccess
```

### 2. Añadir estas líneas (copiar del archivo `.htaccess-for-litespeed` en este repo):

```apache
<IfModule LiteSpeed>
  RewriteEngine On
  RewriteBase /

  RewriteRule ^scraper/(.*) http://127.0.0.1:9080/scraper/$1 [P,L]
</IfModule>
```

### 3. Guardar y salir (Ctrl+X, Y, Enter en nano)

### 4. Reiniciar LiteSpeed:

```bash
systemctl restart lsws
# O: /usr/local/lsws/bin/lsws restart
```

---

## Solución: Opción B — Editar vhost.conf (Si .htaccess no funciona)

### 1. En el servidor VPS, editar:

```bash
nano /usr/local/lsws/conf/vhosts/vps-d6ea56ba.vps.ovh.net/vhost.conf
```

### 2. Localizar la sección `<context />`

### 3. **ANTES de esa sección**, añadir un nuevo `<context /scraper/>`:

```apache
<context /scraper/>
  <allowBrowse>0</allowBrowse>
  <enableExpires>1</enableExpires>
  <externalAddress>
    address: 127.0.0.1:9080
  </externalAddress>
</context>
```

### 4. Guardar y graceful restart:

```bash
/usr/local/lsws/bin/lsws -t  # Verificar sintaxis
systemctl restart lsws       # Reiniciar
```

---

## Pasos en el Cliente (Local)

Estos cambios en `docker-compose.yml` YA están hechos:

```yaml
nginx:
  ports:
    - "127.0.0.1:9080:80"  # Cambio de 8443 a 9080
```

En el servidor, solo hacer:

```bash
cd /path/to/multiScraping
git pull
docker compose down
docker compose up -d
```

---

## Verificación

### Desde el servidor:

```bash
# Test local - debe conectar a Docker Nginx
curl -v http://127.0.0.1:9080/scraper/

# Test vía LiteSpeed - debe proxear correctamente
curl -v http://localhost/scraper/
```

### Desde el navegador:

```
https://vps-d6ea56ba.vps.ovh.net/scraper/
# Debe redirigir a login de multiScraping
```

---

## Troubleshooting

### Si ves "Connection refused" en puerto 9080:

```bash
# Verificar que Docker Nginx está corriendo
docker compose ps | grep nginx

# Ver logs
docker compose logs nginx | tail -20
```

### Si LiteSpeed sigue mostrando 404:

- Opción A: Verificar que el `.htaccess` está en el directorio correcto
- Opción B: Verificar que `vhost.conf` tiene sintaxis válida (`/usr/local/lsws/bin/lsws -t`)
- Último: Reiniciar LiteSpeed manualmente

```bash
systemctl restart lsws
```
