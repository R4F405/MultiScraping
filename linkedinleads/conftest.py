"""
Configuración de pytest para linkedinleads.
Agrega backend/ al sys.path para que los tests puedan hacer
imports planos como `import db`, `import scraper`, etc.
"""
import sys
from pathlib import Path

# Agregar linkedinleads/backend/ al path para imports planos
BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# También agregar linkedinleads/ para imports de backend.*
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
