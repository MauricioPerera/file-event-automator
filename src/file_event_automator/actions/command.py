from __future__ import annotations

import logging
import subprocess
from typing import Any, Dict
from .base import BaseAction, interpolate_template

logger = logging.getLogger("file_event_automator.actions.command")


class CommandAction(BaseAction):
    def __init__(
        self,
        cmd_template: str,
        shell: bool = True,
        check_returncode: bool = True,
        timeout: float = 30.0
    ):
        self.cmd_template = cmd_template
        self.shell = shell
        self.check_returncode = check_returncode
        self.timeout = timeout

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        cmd = interpolate_template(self.cmd_template, context)
        logger.info(f"Ejecutando comando: {cmd}")

        result = subprocess.run(
            cmd,
            shell=self.shell,
            capture_output=True,
            text=True,
            timeout=self.timeout
        )

        logger.debug(f"Salida comando ({result.returncode}): {result.stdout.strip()}")
        if result.stderr:
            logger.debug(f"Stderr comando: {result.stderr.strip()}")

        if self.check_returncode and result.returncode != 0:
            error_msg = f"Comando falló con código {result.returncode}. Stderr: {result.stderr.strip()}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        context["last_command_stdout"] = result.stdout.strip()
        context["last_command_returncode"] = result.returncode
        return context
