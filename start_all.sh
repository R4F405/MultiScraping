#!/bin/bash
# Script principal: inicia MapLeads + InstaLeads + LinkedInLeads + TikTokLeads + Frontend (macOS/Linux)
set -e

echo ""
echo "=============================================================="
echo "   Leads Suite - Iniciando backend + scrapers + frontend..."
echo "=============================================================="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

free_port() {
    local port=$1
    local pids
    local attempts=0
    local max_attempts=8

    pids=$(lsof -ti :"$port" 2>/dev/null || true)
    if [ -z "$pids" ]; then
        return 0
    fi

    echo "Puerto $port ocupado (PID $pids) — liberando..."
    kill $pids 2>/dev/null || true

    while [ $attempts -lt $max_attempts ]; do
        sleep 1
        pids=$(lsof -ti :"$port" 2>/dev/null || true)
        if [ -z "$pids" ]; then
            return 0
        fi
        attempts=$((attempts + 1))
    done

    echo "Puerto $port sigue ocupado — forzando cierre (SIGKILL) de PID $pids..."
    kill -9 $pids 2>/dev/null || true
    sleep 1

    pids=$(lsof -ti :"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "Error: no se pudo liberar el puerto $port (PID $pids)."
        exit 1
    fi
}

require_venv() {
    local service_dir=$1
    local service_name=$2
    if [ ! -x "$SCRIPT_DIR/$service_dir/venv/bin/python" ]; then
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
    local retries=${3:-40}  # Default 40s, but allow override
    local i=1
    while [ $i -le $retries ]; do
        if lsof -ti :"$port" >/dev/null 2>&1; then
            echo "$name listo en puerto $port"
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    echo "Error: $name no arrancó en puerto $port después de ${retries}s."
    exit 1
}

# Verificar entornos virtuales requeridos
require_venv "mapleads" "MapLeads"
require_venv "instaleads" "InstaLeads"
require_venv "linkedinleads" "LinkedInLeads"

# Frontend: opcional, puede reutilizar venv de mapleads
FRONTEND_VENV="$SCRIPT_DIR/scraperLead-web/venv/bin/activate"
FALLBACK_FRONTEND_VENV="$SCRIPT_DIR/mapleads/venv/bin/activate"

# TikTokLeads: utiliza conda/miniforge environment (python ejecutable directo)
# Verificar que requirements.txt está instalado
if ! python -c "import fastapi" 2>/dev/null; then
    echo "Error: dependencias de TikTokLeads no encontradas."
    echo "Instálalas primero:"
    echo "  cd tiktokleads"
    echo "  pip install -r requirements.txt"
    echo "  playwright install chromium"
    echo ""
    exit 1
fi

# Liberar puertos antes de arrancar
free_port 8001
free_port 8002
free_port 8003
free_port 8004
free_port 8081

echo "Iniciando MapLeads backend en :8001..."
(
    cd "$SCRIPT_DIR/mapleads"
    "$SCRIPT_DIR/mapleads/venv/bin/python" -m uvicorn backend.main:app --host 0.0.0.0 --port 8001
) &
MAPLEADS_PID=$!

echo "Iniciando InstaLeads backend en :8002..."
(
    cd "$SCRIPT_DIR/instaleads"
    "$SCRIPT_DIR/instaleads/venv/bin/python" -m uvicorn backend.main:app --host 0.0.0.0 --port 8002
) &
INSTALEADS_PID=$!

echo "Iniciando LinkedInLeads backend en :8003..."
(
    cd "$SCRIPT_DIR/linkedinleads"
    "$SCRIPT_DIR/linkedinleads/venv/bin/python" -m uvicorn backend.main:app --host 0.0.0.0 --port 8003
) &
LINKEDINLEADS_PID=$!

echo "Iniciando TikTokLeads backend en :8004..."
(
    cd "$SCRIPT_DIR/tiktokleads"
    python -m uvicorn backend.main:app --host 0.0.0.0 --port 8004
) &
TIKTOKLEADS_PID=$!

wait_port 8001 "MapLeads" 20
wait_port 8002 "InstaLeads" 60  # InstaLeads toma más tiempo (carga pool, Google CSE, etc)
wait_port 8003 "LinkedInLeads" 20
wait_port 8004 "TikTokLeads" 20

echo "Iniciando Frontend en :8081..."
(
    cd "$SCRIPT_DIR/scraperLead-web"
    if [ -f "$FRONTEND_VENV" ]; then
        "$SCRIPT_DIR/scraperLead-web/venv/bin/python" main.py
    else
        "$SCRIPT_DIR/mapleads/venv/bin/python" main.py
    fi
) &
FRONTEND_PID=$!

wait_port 8081 "Frontend"

echo ""
echo "Aplicación lista:"
echo "  - Frontend:       http://localhost:8081"
echo "  - MapLeads API:   http://localhost:8001"
echo "  - InstaLeads API: http://localhost:8002"
echo "  - LinkedIn API:   http://localhost:8003"
echo "  - TikTok API:     http://localhost:8004"
echo ""
echo "Presiona Ctrl+C para detener todo."
echo ""

trap "echo ''; echo 'Deteniendo servicios...'; kill $MAPLEADS_PID $INSTALEADS_PID $LINKEDINLEADS_PID $TIKTOKLEADS_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

wait
