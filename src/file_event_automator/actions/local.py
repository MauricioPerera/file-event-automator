from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from .base import BaseAction, interpolate_template

logger = logging.getLogger("file_event_automator.actions.local")


def _verify_path_jailing(path: Path, allowed_roots: Optional[List[str]]) -> None:
    if not allowed_roots:
        return
    resolved = path.resolve()
    for root in allowed_roots:
        resolved_root = Path(root).resolve()
        try:
            resolved.relative_to(resolved_root)
            return
        except ValueError:
            continue
    raise PermissionError(
        f"Acceso denegado: La ruta '{resolved}' no está contenida en ninguna de las raíces permitidas: {allowed_roots}"
    )


class LocalMoveAction(BaseAction):
    def __init__(
        self,
        destination_template: str,
        overwrite: bool = True,
        allow_dir_overwrite: bool = False,
        allowed_roots: Optional[List[str]] = None
    ):
        self.destination_template = destination_template
        self.overwrite = overwrite
        self.allow_dir_overwrite = allow_dir_overwrite
        self.allowed_roots = allowed_roots

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        src = Path(context["filepath"]).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Archivo origen no encontrado para mover: {src}")

        dest_str = interpolate_template(self.destination_template, context)
        dest = Path(dest_str).resolve()

        # Determinar la ruta destino final y el directorio padre a crear antes de tocar el disco
        if dest.is_dir() or dest_str.endswith(("/", "\\")):
            final_dest = dest / src.name
            parent_to_create = dest
        else:
            final_dest = dest
            parent_to_create = dest.parent

        # 1. Validar jailing ANTES de cualquier creación de directorios
        _verify_path_jailing(final_dest, self.allowed_roots)
        _verify_path_jailing(parent_to_create, self.allowed_roots)

        # 2. Crear directorios necesarios solo una vez validada la seguridad
        parent_to_create.mkdir(parents=True, exist_ok=True)
        dest = final_dest

        if dest.exists():
            if self.overwrite:
                if dest.is_file():
                    dest.unlink()
                elif dest.is_dir():
                    if not self.allow_dir_overwrite:
                        raise IsADirectoryError(
                            f"El destino '{dest}' es un directorio existente. "
                            "Para permitir sobreescribir directorios completos active 'allow_dir_overwrite: true'."
                        )
                    shutil.rmtree(dest)
            else:
                raise FileExistsError(f"El archivo destino ya existe y overwrite=False: {dest}")

        shutil.move(str(src), str(dest))
        _verify_path_jailing(dest, self.allowed_roots)
        logger.info(f"Movido: {src} -> {dest}")

        # Actualizar contexto con la nueva ruta
        context["filepath"] = str(dest)
        context["filename"] = dest.name
        context["stem"] = dest.stem
        context["ext"] = dest.suffix
        context["dir"] = str(dest.parent)
        return context


class LocalCopyAction(BaseAction):
    def __init__(
        self,
        destination_template: str,
        overwrite: bool = True,
        allow_dir_overwrite: bool = False,
        allowed_roots: Optional[List[str]] = None
    ):
        self.destination_template = destination_template
        self.overwrite = overwrite
        self.allow_dir_overwrite = allow_dir_overwrite
        self.allowed_roots = allowed_roots

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        src = Path(context["filepath"]).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Archivo origen no encontrado para copiar: {src}")

        dest_str = interpolate_template(self.destination_template, context)
        dest = Path(dest_str).resolve()

        # Determinar la ruta destino final y el directorio padre a crear antes de tocar el disco
        if dest.is_dir() or dest_str.endswith(("/", "\\")):
            final_dest = dest / src.name
            parent_to_create = dest
        else:
            final_dest = dest
            parent_to_create = dest.parent

        # 1. Validar jailing ANTES de cualquier creación de directorios
        _verify_path_jailing(final_dest, self.allowed_roots)
        _verify_path_jailing(parent_to_create, self.allowed_roots)

        # 2. Crear directorios necesarios solo una vez validada la seguridad
        parent_to_create.mkdir(parents=True, exist_ok=True)
        dest = final_dest

        if dest.exists():
            if not self.overwrite:
                raise FileExistsError(f"El destino ya existe y overwrite=False: {dest}")
            if dest.is_dir() and not self.allow_dir_overwrite:
                raise IsADirectoryError(
                    f"El destino '{dest}' es un directorio. "
                    "Para permitir sobreescritura de directorios active 'allow_dir_overwrite: true'."
                )

        if src.is_dir():
            shutil.copytree(str(src), str(dest), dirs_exist_ok=self.overwrite)
        else:
            shutil.copy2(str(src), str(dest))

        _verify_path_jailing(dest, self.allowed_roots)
        logger.info(f"Copiado: {src} -> {dest}")
        return context


class LocalDeleteAction(BaseAction):
    def __init__(
        self,
        missing_ok: bool = True,
        allow_dir_deletion: bool = False,
        allowed_roots: Optional[List[str]] = None
    ):
        self.missing_ok = missing_ok
        self.allow_dir_deletion = allow_dir_deletion
        self.allowed_roots = allowed_roots

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        target = Path(context["filepath"]).resolve()
        _verify_path_jailing(target, self.allowed_roots)

        if not target.exists():
            if self.missing_ok:
                logger.warning(f"Archivo ya no existía al intentar borrar: {target}")
                return context
            raise FileNotFoundError(f"Archivo no encontrado para borrar: {target}")

        if target.is_file():
            target.unlink()
        elif target.is_dir():
            if not self.allow_dir_deletion:
                raise IsADirectoryError(
                    f"El objetivo a borrar '{target}' es un directorio. "
                    "Para permitir borrado de directorios active 'allow_dir_deletion: true'."
                )
            shutil.rmtree(target)

        logger.info(f"Eliminado: {target}")
        return context

