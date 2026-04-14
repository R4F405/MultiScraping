@echo off
REM Script para compilar el ejecutable de MapLeads
REM Ejecutar este archivo para generar MapLeads.exe

echo.
echo ============================================
echo    Compilando MapLeads Executable
echo ============================================
echo.

REM Copiar .env.example a .env si no existe
if not exist ".env" (
    echo Creando .env desde .env.example...
    copy .env.example .env
    echo ✓ .env creado
    echo.
)

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
echo Para ejecutar MapLeads:
echo   1. Haz doble clic en: run_mapleads.bat
echo   O navega a dist\MapLeads-Frontend\ y haz doble clic en MapLeads-Frontend.exe
echo.
pause
