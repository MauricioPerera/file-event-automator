from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def build_context(
    filepath: str,
    event_type: str = "created",
    event_id: str = "",
    action_index: int = 0,
    src_path: Optional[str] = None
) -> Dict[str, Any]:
    path = Path(filepath)
    now = datetime.now()

    filesize = 0
    if path.exists() and path.is_file():
        try:
            filesize = path.stat().st_size
        except OSError:
            pass

    resolved_src = str(Path(src_path).resolve()) if src_path else str(path.resolve())

    return {
        "filepath": str(path.resolve()),
        "filename": path.name,
        "stem": path.stem,
        "ext": path.suffix,
        "dir": str(path.parent.resolve()),
        "src_path": resolved_src,
        "src_filename": Path(resolved_src).name,
        "filesize": filesize,
        "event_type": event_type,
        "event_id": event_id,
        "action_index": action_index,
        "timestamp": now.strftime("%Y%m%d_%H%M%S"),
        "iso_timestamp": now.isoformat(),
    }



def interpolate_template(template: Any, context: Dict[str, Any]) -> Any:
    """Reemplaza de forma recursiva placeholders como {filename} en strings, dicts o listas."""
    if isinstance(template, str):
        # Reemplaza claves conocidas
        formatted = template
        for k, v in context.items():
            placeholder = f"{{{k}}}"
            if placeholder in formatted:
                formatted = formatted.replace(placeholder, str(v))
        return formatted
    elif isinstance(template, dict):
        return {k: interpolate_template(v, context) for k, v in template.items()}
    elif isinstance(template, list):
        return [interpolate_template(v, context) for v in template]
    return template


class BaseAction(ABC):
    """Clase base para todos los ejecutores de acción."""

    @abstractmethod
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta la acción con el contexto proporcionado.
        Devuelve el contexto (posiblemente actualizado, ej: nueva ruta si se movió).
        """
        pass
