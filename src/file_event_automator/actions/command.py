from __future__ import annotations

import logging
import shlex
import subprocess
import sys
from typing import Any, Dict, List, Optional
from .base import BaseAction, interpolate_template

logger = logging.getLogger("file_event_automator.actions.command")


class CommandAction(BaseAction):
    def __init__(
        self,
        cmd_template: Optional[str] = None,
        args: Optional[List[str]] = None,
        shell: bool = False,
        allow_shell_commands: bool = False,
        check_returncode: bool = True,
        timeout: float = 30.0
    ):
        self.cmd_template = cmd_template
        self.args = args
        self.shell = shell
        self.allow_shell_commands = allow_shell_commands
        self.check_returncode = check_returncode
        self.timeout = timeout

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if self.shell and not self.allow_shell_commands:
            raise PermissionError(
                "Ejecución con shell=True bloqueada por seguridad. "
                "Para habilitarla, declare 'allow_shell_commands: true' en settings."
            )

        if self.args:
            # Modo seguro por lista de argumentos (sin shell)
            resolved_cmd = [str(interpolate_template(arg, context)) for arg in self.args]
            logger.info(f"Ejecutando comando seguro (sin shell): {resolved_cmd}")
            result = subprocess.run(
                resolved_cmd,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
        elif self.cmd_template:
            cmd_str = interpolate_template(self.cmd_template, context)
            if self.shell:
                logger.info(f"Ejecutando comando en shell: {cmd_str}")
                result = subprocess.run(
                    cmd_str,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout
                )
            else:
                # Tokenizar con shlex para no invocar shell del SO
                raw_tokens = shlex.split(cmd_str, posix=False)
                tokens = []
                for tok in raw_tokens:
                    if len(tok) >= 2 and ((tok.startswith('"') and tok.endswith('"')) or (tok.startswith("'") and tok.endswith("'"))):
                        tokens.append(tok[1:-1])
                    else:
                        tokens.append(tok)

                # En Windows, 'echo' es un builtin de cmd.exe sin ejecutable propio.
                # Lo ejecutamos de forma segura mediante python -c (sin invocar shell).
                if sys.platform == "win32" and tokens and tokens[0].lower() == "echo":
                    exec_tokens = [sys.executable, "-c", "import sys; print(' '.join(sys.argv[1:]))"] + tokens[1:]
                else:
                    exec_tokens = tokens

                logger.info(f"Ejecutando comando tokenizado (sin shell): {exec_tokens}")
                result = subprocess.run(
                    exec_tokens,
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout
                )
        else:
            raise ValueError("CommandAction requiere 'cmd_template' o 'args'.")

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

