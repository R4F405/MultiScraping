# Crear y Ejecutar el .exe en Windows

## Requisitos

1. **Instalación previa (obligatoria):** sigue primero la sección **Instalación completa (Windows - CMD)** del [`README.md`](README.md) del repositorio (crear `venv` en cada módulo, copiar `.env`, `pip install`, etc.). Sin eso, `start_multiscraping.bat` no podrá arrancar el backend de MapLeads aunque el `.exe` esté compilado. No te adelantes a construir el ejecutable sin haber completado ese paso.
2. Python 3.11+ instalado y accesible desde la terminal (`python` en el PATH).
3. Para compilar, trabajar desde la carpeta `scraperLead-web` (ver Paso 1).

---

## Paso 1: Generar el .exe (Una sola vez)

1. **Abre PowerShell o CMD** en la carpeta `scraperLead-web`
2. **Ejecuta:**
   ```
   build_exe.bat
   ```
3. **Espera 2-5 minutos** (verás mensajes de compilación)
4. Cuando termine, verás: `scraperLead-web/dist/MapLeads-Frontend/MapLeads-Frontend.exe`

---

## Paso 2: Ejecutar la aplicación

Simplemente **haz doble clic en `start_multiscraping.bat`** (en la raíz del proyecto)

- Inicia el backend (scraper) y el frontend juntos
- Se abrirá automáticamente el navegador en `http://localhost:8081`
- Se abrirá una ventana con el backend (no la cierres mientras uses la app)
- Para cerrar: cierra ambas ventanas

---

## Configuración

Si tus backends están en otros puertos, edita `scraperLead-web/.env`:

```env
MAPLEADS_API_URL=http://localhost:8001
INSTALEADS_API_URL=http://localhost:8002
PORT=8081
```

Después reinicia la app con `start_multiscraping.bat`.

---

## Distribución

Puedes copiar todo el proyecto a otros ordenadores. Solo necesitan ejecutar `build_exe.bat` una vez y después usar `start_multiscraping.bat` siempre.
