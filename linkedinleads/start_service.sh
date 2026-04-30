#!/bin/bash
# Script para iniciar el servicio de LinkedIn con acceso completo al sistema
# Ejecutar desde Terminal.app o iTerm, NO desde Cursor IDE

cd "$(dirname "$0")"

# Matar servicio anterior si existe
pkill -f "uvicorn.*backend.main:app.*8003" 2>/dev/null || true
sleep 1

echo "🚀 Iniciando servicio linkedinleads en puerto 8003..."
echo "   Logs en: /tmp/linkedinleads.log"

# Iniciar servicio con nohup para que siga corriendo
nohup uvicorn backend.main:app --host 0.0.0.0 --port 8003 --reload > /tmp/linkedinleads.log 2>&1 &

sleep 3

# Verificar que está corriendo
if curl -s http://localhost:8003/api/linkedin/health > /dev/null 2>&1; then
    echo "✅ Servicio iniciado correctamente"
    curl -s http://localhost:8003/api/linkedin/health | python3 -m json.tool
else
    echo "❌ Error al iniciar el servicio"
    echo "Ver logs: tail -f /tmp/linkedinleads.log"
    exit 1
fi
