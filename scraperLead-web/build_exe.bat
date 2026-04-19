@echo off
REM Script para compilar el ejecutable de MapLeads
REM Ejecutar este archivo para generar MapLeads.exe

echo.
echo ============================================
echo    Compilando MapLeads Executable
echo ============================================
echo.

REM Crear .env de build si no existe (solo Google Maps operativo)
if not exist ".env" (
    echo Creando .env para build de MapLeads-only...
    > ".env" (
        echo MAPLEADS_API_URL=http://localhost:8001
        echo MAPLEADS_API_KEY=
        echo INSTALEADS_API_URL=http://127.0.0.1:65535
        echo TIKTOKLEADS_API_URL=http://127.0.0.1:65535
        echo LINKEDINLEADS_API_URL=http://localhost:8003
    )
    echo ✓ .env creado con Instagram/TikTok deshabilitados
    echo.
)

REM Asegurar que el proceso de build usa servicios no funcionales deshabilitados
set "INSTALEADS_API_URL=http://127.0.0.1:65535"
set "TIKTOKLEADS_API_URL=http://127.0.0.1:65535"

REM Verificar que Python está instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python no está instalado o no está en PATH
    pause
    exit /b 1
)

echo Instalando dependencias...
pip install -r requirements.txt
if errorlevel 1 (
    echo Error: No se pudieron instalar las dependencias
    pause
    exit /b 1
)

echo.
echo Compilando ejecutable con PyInstaller...
echo (Este proceso puede tardar varios minutos)
echo.

pyinstaller build_mapleads.spec
if errorlevel 1 (
    echo Error: No se pudo compilar el ejecutable
    pause
    exit /b 1
)

echo.
echo ============================================
echo   ¡Compilación completada!
echo ============================================
echo.
echo El ejecutable se encuentra en: dist\MapLeads-Frontend\MapLeads-Frontend.exe
echo.
echo Para ejecutar MultiScraping (panel + backend):
echo   1. Haz doble clic en: start_multiscraping.bat (en la raiz del repo)
echo   O navega a dist\MapLeads-Frontend\ y haz doble clic en MapLeads-Frontend.exe
echo.
pause
