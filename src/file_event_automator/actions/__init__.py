from __future__ import annotations

from typing import TYPE_CHECKING
from .base import BaseAction, build_context, interpolate_template
from .local import LocalMoveAction, LocalCopyAction, LocalDeleteAction
from .network import WebhookAction
from .command import CommandAction

if TYPE_CHECKING:
    from ..config import ActionConfig


def create_action(cfg: ActionConfig) -> BaseAction:
    if cfg.type == "local_move":
        return LocalMoveAction(destination_template=cfg.destination or "", overwrite=cfg.overwrite)
    elif cfg.type == "local_copy":
        return LocalCopyAction(destination_template=cfg.destination or "", overwrite=cfg.overwrite)
    elif cfg.type == "local_delete":
        return LocalDeleteAction(missing_ok=cfg.missing_ok)
    elif cfg.type == "webhook":
        return WebhookAction(
            url_template=cfg.url or "",
            method=cfg.method,
            headers=cfg.headers,
            json_payload=cfg.json_payload,
            timeout=cfg.timeout
        )
    elif cfg.type == "command":
        return CommandAction(
            cmd_template=cfg.cmd or "",
            shell=cfg.shell,
            check_returncode=cfg.check_returncode,
            timeout=cfg.timeout
        )
    else:
        raise ValueError(f"Tipo de acción desconocido: {cfg.type}")


__all__ = [
    "BaseAction",
    "build_context",
    "interpolate_template",
    "LocalMoveAction",
    "LocalCopyAction",
    "LocalDeleteAction",
    "WebhookAction",
    "CommandAction",
    "create_action",
]
