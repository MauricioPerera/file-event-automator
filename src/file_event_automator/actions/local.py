from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict
from .base import BaseAction, interpolate_template

logger = logging.getLogger("file_event_automator.actions.local")


class LocalMoveAction(BaseAction):
    def __init__(self, destination_template: str, overwrite: bool = True):
        self.destination_template = destination_template
        self.overwrite = overwrite

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        src = Path(context["filepath"])
        if not src.exists():
            raise FileNotFoundError(f"Archivo origen no encontrado para mover: {src}")

        dest_str = interpolate_template(self.destination_template, context)
        dest = Path(dest_str).resolve()

        # Si el destino es un directorio existente o termina en separador
        if dest.is_dir() or dest_str.endswith(("/", "\\")):
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest / src.name
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists():
            if self.overwrite:
                if dest.is_file():
                    dest.unlink()
                else:
                    shutil.rmtree(dest)
            else:
                raise FileExistsError(f"El archivo destino ya existe y overwrite=False: {dest}")

        shutil.move(str(src), str(dest))
        logger.info(f"Movido: {src} -> {dest}")

        # Actualizar contexto con la nueva ruta
        context["filepath"] = str(dest)
        context["filename"] = dest.name
        context["stem"] = dest.stem
        context["ext"] = dest.suffix
        context["dir"] = str(dest.parent)
        return context


class LocalCopyAction(BaseAction):
    def __init__(self, destination_template: str, overwrite: bool = True):
        self.destination_template = destination_template
        self.overwrite = overwrite

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        src = Path(context["filepath"])
        if not src.exists():
            raise FileNotFoundError(f"Archivo origen no encontrado para copiar: {src}")

        dest_str = interpolate_template(self.destination_template, context)
        dest = Path(dest_str).resolve()

        if dest.is_dir() or dest_str.endswith(("/", "\\")):
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest / src.name
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists() and not self.overwrite:
            raise FileExistsError(f"El destino ya existe y overwrite=False: {dest}")

        if src.is_dir():
            shutil.copytree(str(src), str(dest), dirs_exist_ok=self.overwrite)
        else:
            shutil.copy2(str(src), str(dest))

        logger.info(f"Copiado: {src} -> {dest}")
        return context


class LocalDeleteAction(BaseAction):
    def __init__(self, missing_ok: bool = True):
        self.missing_ok = missing_ok

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        target = Path(context["filepath"])
        if not target.exists():
            if self.missing_ok:
                logger.warning(f"Archivo ya no existía al intentar borrar: {target}")
                return context
            raise FileNotFoundError(f"Archivo no encontrado para borrar: {target}")

        if target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)

        logger.info(f"Eliminado: {target}")
        return context
