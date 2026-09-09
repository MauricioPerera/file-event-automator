from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("file_event_automator.stabilizer")


def wait_for_file_ready(
    filepath: str | Path,
    timeout: float = 15.0,
    check_interval: float = 0.5
) -> bool:
    """
    Comprueba si un archivo ha terminado de escribirse y ya no está bloqueado
    por otro proceso (útil en descargas o copias de archivos grandes).
    """
    path = Path(filepath)
    start_time = time.time()
    last_size = -1

    while time.time() - start_time < timeout:
        if not path.exists():
            return False

        try:
            current_size = path.stat().st_size

            # En Windows y Linux intentamos abrir el archivo con descriptor exclusivo/append
            with open(path, "rb") as f:
                # Comprobamos que podemos leer al menos un byte o llegar al final
                f.seek(0, os.SEEK_END)

            # Si el tamaño se mantiene igual durante dos comprobaciones consecutivas
            if current_size == last_size:
                return True

            last_size = current_size
        except (PermissionError, OSError) as e:
            logger.debug(f"Archivo aún bloqueado o en escritura ({path.name}): {e}")

        time.sleep(check_interval)

    # Última comprobación al agotar el tiempo
    try:
        if path.exists():
            with open(path, "rb"):
                return True
    except (PermissionError, OSError):
        pass

    logger.warning(f"Timeout ({timeout}s) esperando estabilización de archivo: {path}")
    return False
