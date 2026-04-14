#!/usr/bin/env python
"""
MapLeads Frontend Launcher
Inicia el servidor y abre el navegador automáticamente en Windows.
"""

import os
import sys
import webbrowser
import time
import threading
import uvicorn

# Cambiar al directorio de la aplicación
try:
    app_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    app_dir = os.getcwd()

os.chdir(app_dir)

if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from main import app  # noqa: E402 — import after chdir so templates/static resolve

# Variables
HOST = "127.0.0.1"
PORT = int(os.getenv("PORT", 8081))
URL = f"http://{HOST}:{PORT}"


def open_browser():
    """Abre el navegador después de un breve retraso."""
    time.sleep(2)  # Esperar a que el servidor inicie
    try:
        webbrowser.open(URL)
        print(f"\n✓ Navegador abierto: {URL}\n")
    except Exception as e:
        print(f"\n⚠ No se pudo abrir el navegador automáticamente: {e}")
        print(f"   Abre manualmente: {URL}\n")


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  MapLeads - Frontend")
    print("=" * 50 + "\n")

    print(f"Iniciando servidor en {URL}...\n")
    print("Presiona CTRL+C para detener.\n")

    # Abrir navegador en un thread separado
    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()

    # Iniciar uvicorn
    try:
        uvicorn.run(
            app,
            host=HOST,
            port=PORT,
            log_level="info",
            access_log=True,
        )
    except KeyboardInterrupt:
        print("\n\n✓ Servidor detenido.")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)
