# log_config.py
# Configuración centralizada de logging: archivo logs/scraper.log y consola.
# Usado por main.py y por scraper.py (getLogger(__name__) hereda del root).

import logging
import os
from pathlib import Path

_LOG_FILE_HANDLER: logging.FileHandler | None = None


def setup_logging() -> None:
    """Añade un FileHandler a logs/scraper.log (una sola vez). Nivel y ruta por env."""
    global _LOG_FILE_HANDLER
    if _LOG_FILE_HANDLER is not None:
        return

    log_dir = os.environ.get("LOG_DIR", "logs")
    log_file = os.environ.get("LOG_FILE", str(Path(log_dir) / "scraper.log"))
    level_name = os.environ.get("SCRAPER_LOG_LEVEL", os.environ.get("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )

    root = logging.getLogger()
    root.addHandler(handler)
    if level < root.level:
        root.setLevel(level)
    _LOG_FILE_HANDLER = handler
