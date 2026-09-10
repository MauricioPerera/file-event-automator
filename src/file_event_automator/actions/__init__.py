from __future__ import annotations

from typing import TYPE_CHECKING
from .base import BaseAction, build_context, interpolate_template
from .local import LocalMoveAction, LocalCopyAction, LocalDeleteAction
from .network import WebhookAction
from .command import CommandAction

if TYPE_CHECKING:
    from ..config import ActionConfig, SettingsConfig


def create_action(cfg: ActionConfig, settings: Optional[SettingsConfig] = None) -> BaseAction:
    allowed_roots = settings.allowed_roots if settings else None
    allow_symlinks = settings.allow_symlinks if settings else False

    if cfg.type == "local_move":
        return LocalMoveAction(
            destination_template=cfg.destination or "",
            overwrite=cfg.overwrite,
            allow_dir_overwrite=cfg.allow_dir_overwrite,
            allowed_roots=allowed_roots,
            allow_symlinks=allow_symlinks
        )
    elif cfg.type == "local_copy":
        return LocalCopyAction(
            destination_template=cfg.destination or "",
            overwrite=cfg.overwrite,
            allow_dir_overwrite=cfg.allow_dir_overwrite,
            allowed_roots=allowed_roots,
            allow_symlinks=allow_symlinks
        )
    elif cfg.type == "local_delete":
        return LocalDeleteAction(
            missing_ok=cfg.missing_ok,
            allow_dir_deletion=cfg.allow_dir_deletion,
            allowed_roots=allowed_roots,
            allow_symlinks=allow_symlinks
        )
    elif cfg.type == "webhook":
        return WebhookAction(
            url_template=cfg.url or "",
            method=cfg.method,
            headers=cfg.headers,
            json_payload=cfg.json_payload,
            timeout=cfg.timeout,
            allowed_domains=settings.allowed_webhook_domains if settings else None,
            allow_private_networks=settings.allow_private_networks if settings else False
        )
    elif cfg.type == "command":
        return CommandAction(
            cmd_template=cfg.cmd,
            args=cfg.args,
            shell=cfg.shell,
            allow_shell_commands=settings.allow_shell_commands if settings else False,
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
