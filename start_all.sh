#!/bin/bash
# Script principal: inicia MapLeads + InstaLeads + LinkedInLeads + Frontend (macOS/Linux)
set -e

echo ""
echo "=============================================================="
echo "   Leads Suite - Iniciando backend + scrapers + frontend..."
echo "=============================================================="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

require_venv() {
    local service_dir=$1
    local service_name=$2
    if [ ! -f "$SCRIPT_DIR/$service_dir/venv/bin/activate" ]; then
        echo "Error: no se encontró venv en $service_dir ($service_name)."
        echo ""
        echo "Configúralo primero:"
        echo "  cd $service_dir"
        echo "  python3 -m venv venv"
        echo "  source venv/bin/activate"
        echo "  pip install -r requirements.txt"
        echo ""
        exit 1
    fi
}

wait_port() {
    local port=$1
    local name=$2
    local retries=20
    local i=1
    while [ $i -le $retries ]; do
        if lsof -ti :"$port" >/dev/null 2>&1; then
            echo "$name listo en puerto $port"
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    echo "Error: $name no arrancó en puerto $port."
    exit 1
}

# Verificar entornos virtuales requeridos
require_venv "mapleads" "MapLeads"
require_venv "instaleads" "InstaLeads"
require_venv "linkedinleads" "LinkedInLeads"

# Frontend: opcional, puede reutilizar venv de mapleads
FRONTEND_VENV="$SCRIPT_DIR/scraperLead-web/venv/bin/activate"
FALLBACK_FRONTEND_VENV="$SCRIPT_DIR/mapleads/venv/bin/activate"

# Liberar puertos antes de arrancar
free_port 8001
free_port 8002
free_port 8003
free_port 8081

echo "Iniciando MapLeads backend en :8001..."
(
    cd "$SCRIPT_DIR/mapleads"
    source venv/bin/activate
    uvicorn backend.main:app --host 0.0.0.0 --port 8001
) &
MAPLEADS_PID=$!

echo "Iniciando InstaLeads backend en :8002..."
(
    cd "$SCRIPT_DIR/instaleads"
    source venv/bin/activate
    uvicorn backend.main:app --host 0.0.0.0 --port 8002
) &
INSTALEADS_PID=$!

echo "Iniciando LinkedInLeads backend en :8003..."
(
    cd "$SCRIPT_DIR/linkedinleads"
    source venv/bin/activate
    uvicorn backend.main:app --host 0.0.0.0 --port 8003
) &
LINKEDINLEADS_PID=$!

wait_port 8001 "MapLeads"
wait_port 8002 "InstaLeads"
wait_port 8003 "LinkedInLeads"

echo "Iniciando Frontend en :8081..."
(
    cd "$SCRIPT_DIR/scraperLead-web"
    if [ -f "$FRONTEND_VENV" ]; then
        source "$FRONTEND_VENV"
    else
        source "$FALLBACK_FRONTEND_VENV"
    fi
    python main.py
) &
FRONTEND_PID=$!

wait_port 8081 "Frontend"

echo ""
echo "Aplicación lista:"
echo "  - Frontend:      http://localhost:8081"
echo "  - MapLeads API:  http://localhost:8001"
echo "  - InstaLeads API: http://localhost:8002"
echo "  - LinkedIn API:  http://localhost:8003"
echo ""
echo "Presiona Ctrl+C para detener todo."
echo ""

trap "echo ''; echo 'Deteniendo servicios...'; kill $MAPLEADS_PID $INSTALEADS_PID $LINKEDINLEADS_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

wait
