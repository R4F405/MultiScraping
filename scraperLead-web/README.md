# MapLeads - Frontend

Frontend de Scraper Lead — construido con **FastAPI + Jinja2**.

## Instalación

```bash
pip install -r requirements.txt
cp .env.example .env    # Mac/Linux
copy .env.example .env  # Windows
```

## Ejecutar

```bash
python launcher.py
```

El navegador se abrirá automáticamente en `http://localhost:8081`

---

## Para crear un .exe en Windows

Ver [WINDOWS_EXECUTABLE.md](../WINDOWS_EXECUTABLE.md) en la raíz del proyecto.

## Estructura

```
├── main.py              # App FastAPI
├── launcher.py          # Script para iniciar (abre navegador automáticamente)
├── requirements.txt
├── .env                 # Variables de entorno
├── templates/           # HTML con Jinja2
├── static/js/           # Módulos JavaScript
└── .env.example         # Plantilla de configuración
```
