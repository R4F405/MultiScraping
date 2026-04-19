@echo off
REM Script principal: inicia el backend y el frontend juntos
setlocal enabledelayedexpansion

echo.
echo ============================================
echo    MultiScraping - Iniciando panel y backend
echo ============================================
echo.
echo Modo: MapLeads (Google Maps) + panel web. Instagram/TikTok deshabilitados en este arranque.
echo.

REM Verificar que el venv del backend existe
if not exist "mapleads\venv\Scripts\activate.bat" (
    echo Error: No se encontro el entorno virtual del backend.
    echo.
    echo Debes configurar el backend primero:
    echo   1. Abre CMD en esta carpeta
    echo   2. Ejecuta: cd mapleads
    echo   3. Ejecuta: python -m venv venv
    echo   4. Ejecuta: venv\Scripts\activate
    echo   5. Ejecuta: pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM Verificar que el .exe del frontend existe
if not exist "scraperLead-web\dist\MapLeads-Frontend\MapLeads-Frontend.exe" (
    echo Error: No se encontro el ejecutable del frontend.
    echo.
    echo Debes compilar el frontend primero:
    echo   1. Entra en la carpeta: cd scraperLead-web
    echo   2. Ejecuta: build_exe.bat
    echo.
    pause
    exit /b 1
)

REM Iniciar el backend en una ventana separada
echo Iniciando backend (Google Maps scraper)...
start "MultiScraping - MapLeads API" cmd /k "cd /d %~dp0mapleads && venv\Scripts\activate && uvicorn backend.main:app --port 8001"

REM Esperar a que el backend arranque
echo Esperando a que el backend este listo...
timeout /t 4 /nobreak >nul

REM Iniciar el frontend
echo Iniciando frontend...
REM Forzar servicios no funcionales como deshabilitados para este arranque.
set "INSTALEADS_API_URL=http://127.0.0.1:65535"
set "TIKTOKLEADS_API_URL=http://127.0.0.1:65535"
echo.
echo La aplicacion se abrira en el navegador en http://localhost:8081
echo.
echo Para cerrar: cierra ambas ventanas.
echo.

"%~dp0scraperLead-web\dist\MapLeads-Frontend\MapLeads-Frontend.exe"
