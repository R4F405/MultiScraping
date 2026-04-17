#!/bin/bash
# Script principal: inicia el backend y el frontend juntos (macOS)
set -e

echo ""
echo "============================================"
echo "   MapLeads - Iniciando todo..."
echo "============================================"
echo ""
echo "Modo operativo: solo Google Maps (Instagram/TikTok deshabilitados)"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Función para liberar un puerto si está ocupado
free_port() {
    local port=$1
    local pid
    pid=$(lsof -ti :"$port" 2>/dev/null || true)
    if [ -n "$pid" ]; then
        echo "Puerto $port ocupado (PID $pid) — liberando..."
        kill "$pid" 2>/dev/null || true
        sleep 1
    fi
}

# Verificar entorno virtual del backend
if [ ! -f "$SCRIPT_DIR/mapleads/venv/bin/activate" ]; then
    echo "Error: No se encontró el entorno virtual del backend."
    echo ""
    echo "Configura el backend primero:"
    echo "  1. cd mapleads"
    echo "  2. python3 -m venv venv"
    echo "  3. source venv/bin/activate"
    echo "  4. pip install -r requirements.txt"
    echo ""
    exit 1
fi

# Liberar puertos antes de arrancar
free_port 8001  # MapLeads backend
free_port 8081  # Frontend

# Iniciar backend en background
echo "Iniciando backend (Google Maps scraper) en :8001..."
(
    cd "$SCRIPT_DIR/mapleads"
    source venv/bin/activate
    uvicorn backend.main:app --port 8001
) &
BACKEND_PID=$!

# Esperar a que el backend arranque
echo "Esperando a que el backend esté listo..."
sleep 3

# Verificar que el backend arrancó
if ! lsof -ti :8001 &>/dev/null; then
    echo "Error: El backend no pudo arrancar. Revisa los logs."
    exit 1
fi
echo "Backend listo en http://localhost:8001"

# Iniciar frontend
echo "Iniciando frontend en :8081..."
(
    cd "$SCRIPT_DIR/scraperLead-web"
    # Forzar servicios no funcionales como deshabilitados para este arranque.
    export INSTALEADS_API_URL="http://127.0.0.1:65535"
    export TIKTOKLEADS_API_URL="http://127.0.0.1:65535"
    # Reutilizar el venv del backend si no tiene el suyo propio
    if [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
    else
        source "$SCRIPT_DIR/mapleads/venv/bin/activate"
    fi
    python main.py
) &
FRONTEND_PID=$!

echo ""
echo "Aplicación lista en http://localhost:8081"
echo "Presiona Ctrl+C para detener todo."
echo ""

# Al hacer Ctrl+C, matar ambos procesos
trap "echo ''; echo 'Deteniendo...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

wait
