from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, model_validator
import yaml


class SettingsConfig(BaseModel):
    debounce_seconds: float = Field(default=1.0, ge=0.0, description="Tiempo mínimo entre eventos sobre el mismo archivo")
    stability_timeout: float = Field(default=15.0, ge=0.1, description="Tiempo máximo para esperar que un archivo termine de escribirse")
    stability_check_interval: float = Field(default=0.5, ge=0.05, description="Intervalo entre comprobaciones de tamaño/bloqueo")
    max_workers: int = Field(default=4, ge=1, le=32, description="Número de hilos de trabajo simultáneos")
    db_path: str = Field(default="automator.db", description="Ruta a la base de datos SQLite")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO", description="Nivel de logging")


class WatchConfig(BaseModel):
    path: str = Field(..., description="Ruta del directorio a monitorizar")
    recursive: bool = Field(default=False, description="Monitorizar subdirectorios recursivamente")


class ActionConfig(BaseModel):
    type: Literal["local_move", "local_copy", "local_delete", "webhook", "command"]

    # Acciones locales
    destination: Optional[str] = Field(default=None, description="Ruta de destino (soporta plantillas como {filename})")
    overwrite: bool = Field(default=True, description="Sobrescribir si el archivo destino existe")
    missing_ok: bool = Field(default=True, description="Ignorar si el archivo a borrar ya no existe")

    # Webhook
    url: Optional[str] = Field(default=None, description="URL del webhook HTTP")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = Field(default="POST")
    headers: Dict[str, str] = Field(default_factory=dict, description="Encabezados HTTP")
    json_payload: Optional[Any] = Field(default=None, alias="json", description="Carga útil JSON (soporta variables)")
    timeout: float = Field(default=10.0, ge=0.5, description="Timeout en segundos para la petición")

    # Comando de sistema
    cmd: Optional[str] = Field(default=None, description="Comando de consola a ejecutar (soporta variables)")
    check_returncode: bool = Field(default=True, description="Lanzar error si el código de retorno != 0")
    shell: bool = Field(default=True, description="Ejecutar en contexto shell")

    @model_validator(mode="after")
    def validate_action_fields(self) -> ActionConfig:
        if self.type in ("local_move", "local_copy") and not self.destination:
            raise ValueError(f"La acción '{self.type}' requiere el campo 'destination'.")
        if self.type == "webhook" and not self.url:
            raise ValueError("La acción 'webhook' requiere el campo 'url'.")
        if self.type == "command" and not self.cmd:
            raise ValueError("La acción 'command' requiere el campo 'cmd'.")
        return self


class RuleConfig(BaseModel):
    name: str = Field(..., description="Nombre identificador de la regla")
    events: List[Literal["created", "modified", "deleted", "moved"]] = Field(
        default_factory=lambda: ["created"],
        description="Eventos que disparan la regla"
    )
    patterns: List[str] = Field(default_factory=lambda: ["*"], description="Patrones glob a incluir (ej: ['*.csv'])")
    ignore_patterns: List[str] = Field(default_factory=list, description="Patrones glob a ignorar (ej: ['*.tmp'])")
    max_retries: int = Field(default=3, ge=0, description="Número máximo de reintentos en caso de fallo")
    actions: List[ActionConfig] = Field(default_factory=list, description="Secuencia de acciones a ejecutar")


class AutomatorConfig(BaseModel):
    settings: SettingsConfig = Field(default_factory=SettingsConfig)
    watches: List[WatchConfig] = Field(default_factory=list)
    rules: List[RuleConfig] = Field(default_factory=list)


def load_config(file_path: str | Path) -> AutomatorConfig:
    """Carga y valida un archivo de configuración YAML."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Archivo de configuración no encontrado: {path.resolve()}")

    with open(path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f) or {}

    return AutomatorConfig.model_validate(raw_data)


def save_config(config: AutomatorConfig, file_path: str | Path) -> None:
    """Guarda un AutomatorConfig validado en un archivo YAML."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(by_alias=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

