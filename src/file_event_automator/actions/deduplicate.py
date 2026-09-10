from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, List

from .base import BaseAction, interpolate_template
from .local import _verify_path_jailing


class DeduplicateAction(BaseAction):
    """Registra el hash de un archivo y gestiona duplicados por contenido."""

    def __init__(
        self,
        database,
        hash_algorithm: str = "sha256",
        on_duplicate: str = "ignore",
        duplicate_destination: Optional[str] = None,
        allowed_roots: Optional[List[str]] = None,
        allow_symlinks: bool = False,
    ):
        self.database = database
        self.hash_algorithm = hash_algorithm
        self.on_duplicate = on_duplicate
        self.duplicate_destination = duplicate_destination
        self.allowed_roots = allowed_roots
        self.allow_symlinks = allow_symlinks

    def _hash_file(self, path: Path) -> str:
        digest = hashlib.new(self.hash_algorithm)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        raw_path = Path(context["filepath"])
        _verify_path_jailing(raw_path, self.allowed_roots, self.allow_symlinks)
        path = raw_path.resolve()
        _verify_path_jailing(path, self.allowed_roots, self.allow_symlinks)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Archivo no encontrado para deduplicar: {path}")

        digest = self._hash_file(path)
        size = path.stat().st_size
        duplicate, existing_path = self.database.register_file_hash(
            digest, self.hash_algorithm, size, str(path)
        )
        context["file_hash"] = digest
        context["hash_algorithm"] = self.hash_algorithm
        context["duplicate"] = duplicate
        context["duplicate_of"] = existing_path

        if not duplicate or self.on_duplicate == "ignore":
            return context

        if self.on_duplicate == "delete":
            path.unlink()
            return context

        destination = interpolate_template(self.duplicate_destination, context)
        raw_destination = Path(destination)
        _verify_path_jailing(raw_destination, self.allowed_roots, self.allow_symlinks)
        target = raw_destination.resolve()
        _verify_path_jailing(target, self.allowed_roots, self.allow_symlinks)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"El destino de duplicados ya existe: {target}")
        shutil.move(str(path), str(target))
        context["filepath"] = str(target)
        context["filename"] = target.name
        context["stem"] = target.stem
        context["ext"] = target.suffix
        context["dir"] = str(target.parent)
        return context
