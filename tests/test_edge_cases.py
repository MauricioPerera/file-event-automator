from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
import pytest
import requests
import requests_mock
from pydantic import ValidationError

from file_event_automator.config import (
    ActionConfig,
    AutomatorConfig,
    RuleConfig,
    WatchConfig,
    load_config
)
from file_event_automator.db import TaskDatabase
from file_event_automator.stabilizer import wait_for_file_ready
from file_event_automator.actions import (
    build_context,
    create_action,
    LocalMoveAction,
    LocalCopyAction,
    LocalDeleteAction,
    WebhookAction,
    CommandAction
)
from file_event_automator.engine import AutomatorEngine, FileAutomatorHandler


# ==============================================================================
# 1. CASOS DE BORDE Y VALIDACIÓN DE CONFIGURACIÓN
# ==============================================================================

def test_config_invalid_action_missing_fields():
    """Acción local sin destination debe fallar validación."""
    with pytest.raises(ValidationError) as exc:
        ActionConfig(type="local_move")
    assert "requiere el campo 'destination'" in str(exc.value)

    # Webhook sin url debe fallar
    with pytest.raises(ValidationError) as exc:
        ActionConfig(type="webhook")
    assert "requiere el campo 'url'" in str(exc.value)

    # Command sin cmd debe fallar
    with pytest.raises(ValidationError) as exc:
        ActionConfig(type="command")
    assert "requiere el campo 'cmd'" in str(exc.value)


def test_config_invalid_yaml_file(tmp_path):
    """YAML mal formado o corrupto debe lanzar error legible."""
    corrupt_yaml = tmp_path / "corrupt.yaml"
    corrupt_yaml.write_text("settings:\n  debounce_seconds: [esto no es valido: {", encoding="utf-8")
    with pytest.raises(Exception):
        load_config(corrupt_yaml)


def test_config_non_existent_file():
    """Cargar un archivo inexistente debe lanzar FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_config("ruta_totalmente_inexistente_12345.yaml")


# ==============================================================================
# 2. ACCIONES LOCALES: ERRORES Y CASOS LÍMITE
# ==============================================================================

def test_local_move_source_not_found(tmp_path):
    """Mover un archivo que no existe debe lanzar FileNotFoundError."""
    action = LocalMoveAction(destination_template=str(tmp_path / "dest.txt"))
    ctx = {"filepath": str(tmp_path / "inexistente.txt")}
    with pytest.raises(FileNotFoundError):
        action.execute(ctx)


def test_local_move_destination_conflict_no_overwrite(tmp_path):
    """Si el destino ya existe y overwrite=False, debe lanzar FileExistsError."""
    src = tmp_path / "origen.txt"
    src.write_text("datos origen", encoding="utf-8")
    dest = tmp_path / "existente.txt"
    dest.write_text("datos previos", encoding="utf-8")

    action = LocalMoveAction(destination_template=str(dest), overwrite=False)
    ctx = {"filepath": str(src)}
    with pytest.raises(FileExistsError):
        action.execute(ctx)

    # Verificar que el destino no fue modificado
    assert dest.read_text(encoding="utf-8") == "datos previos"
    assert src.exists()


def test_local_delete_missing_file_strict(tmp_path):
    """Borrar con missing_ok=False cuando el archivo no existe debe lanzar FileNotFoundError."""
    action = LocalDeleteAction(missing_ok=False)
    ctx = {"filepath": str(tmp_path / "fantasma.txt")}
    with pytest.raises(FileNotFoundError):
        action.execute(ctx)


# ==============================================================================
# 3. COMANDOS DE SISTEMA: FALLOS, CÓDIGOS DE ERROR Y TIMEOUTS
# ==============================================================================

def test_command_action_failing_exit_code(tmp_path):
    """Comando que retorna código distinto de 0 debe fallar si check_returncode=True."""
    ctx = {"filepath": "dummy"}
    cmd = CommandAction(
        cmd_template='python -c "import sys; sys.exit(42)"',
        check_returncode=True
    )
    with pytest.raises(RuntimeError) as exc:
        cmd.execute(ctx)
    assert "código 42" in str(exc.value)


def test_command_action_failing_exit_code_ignored(tmp_path):
    """Comando con error pero check_returncode=False NO debe lanzar excepción."""
    ctx = {"filepath": "dummy"}
    cmd = CommandAction(
        cmd_template='python -c "import sys; sys.exit(7)"',
        check_returncode=False
    )
    res = cmd.execute(ctx)
    assert res["last_command_returncode"] == 7


def test_command_action_timeout():
    """Comando que supera el tiempo límite debe lanzar subprocess.TimeoutExpired."""
    ctx = {"filepath": "dummy"}
    cmd = CommandAction(
        cmd_template='python -c "import time; time.sleep(2)"',
        timeout=0.3
    )
    with pytest.raises(subprocess.TimeoutExpired):
        cmd.execute(ctx)


# ==============================================================================
# 4. WEBHOOKS: ERRORES HTTP, SERVIDORES CAÍDOS Y TIMEOUTS
# ==============================================================================

def test_webhook_http_500_error():
    """Respuesta HTTP 500 debe lanzar HTTPError para activar reintentos."""
    ctx = {"filepath": "dummy", "filename": "test.txt"}
    with requests_mock.Mocker() as m:
        m.post("https://api.empresa.test/fallo", status_code=500, text="Internal Server Error")
        action = WebhookAction(url_template="https://api.empresa.test/fallo", method="POST")
        with pytest.raises(requests.exceptions.HTTPError):
            action.execute(ctx)


def test_webhook_http_timeout():
    """Webhook que tarda más del timeout debe lanzar Timeout."""
    ctx = {"filepath": "dummy"}
    with requests_mock.Mocker() as m:
        m.post("https://api.empresa.test/lenta", exc=requests.exceptions.ConnectTimeout)
        action = WebhookAction(url_template="https://api.empresa.test/lenta", timeout=0.1)
        with pytest.raises(requests.exceptions.ConnectTimeout):
            action.execute(ctx)


# ==============================================================================
# 5. ESTABILIZADOR: ARCHIVOS BORRADOS ANTES DE ESTABILIZAR O CORRUPTOS
# ==============================================================================

def test_stabilizer_file_disappears(tmp_path):
    """Si un archivo temporal desaparece mientras se espera, wait_for_file_ready debe devolver False."""
    phantom_file = tmp_path / "efimero.tmp"
    phantom_file.write_text("temp", encoding="utf-8")
    phantom_file.unlink()  # Desaparece inmediatamente
    ready = wait_for_file_ready(phantom_file, timeout=0.4, check_interval=0.1)
    assert ready is False


# ==============================================================================
# 6. PERSISTENCIA Y RESILIENCIA ANTE CAÍDAS (CRASH RECOVERY)
# ==============================================================================

def test_crash_recovery_stuck_in_processing(tmp_path):
    """
    Simula una caída del servidor (apagón eléctrico o kill del proceso)
    mientras una tarea estaba en estado PROCESSING.
    Al reiniciar, recover_stuck_tasks debe regresarla a PENDING.
    """
    db_file = tmp_path / "crash_test.db"
    db = TaskDatabase(db_file)

    t_id = db.enqueue_task(
        event_id="crash-evt",
        rule_name="CriticalRule",
        event_type="created",
        source_path="/tmp/crash.csv",
        action_index=0,
        action_type="command",
        action_payload={"type": "command", "cmd": "echo 1"}
    )

    # Reclamar tarea (pasa a PROCESSING)
    task = db.claim_next_task()
    assert task is not None
    assert task.status == "PROCESSING"

    # Simular caída abrupta: no se llama a complete ni a fail
    db.close()

    # Nuevo inicio del demonio tras reinicio
    restarted_db = TaskDatabase(db_file)
    recovered_count = restarted_db.recover_stuck_tasks()
    assert recovered_count == 1

    # La tarea debe volver a estar en PENDING y lista para ser ejecutada
    resumed_task = restarted_db.claim_next_task()
    assert resumed_task is not None
    assert resumed_task.id == t_id
    restarted_db.complete_task(resumed_task.id)

    stats = restarted_db.get_stats()
    assert stats["SUCCESS"] == 1
    assert stats["PROCESSING"] == 0
    assert stats["PENDING"] == 0
    restarted_db.close()


def test_chain_action_cancel_on_intermediate_failure(tmp_path):
    """
    En una cadena de 3 acciones: [A (OK), B (FALLA DEFINITIVA), C (DEPENDIENTE)],
    C nunca debe ejecutarse y debe ser marcada como FAILED automáticamente.
    """
    db = TaskDatabase(tmp_path / "chain_fail.db")
    eid = "chain-001"

    # Encolar 3 acciones para el mismo evento
    db.enqueue_task(eid, "Regla", "created", "/tmp/x.txt", 0, "cmd", {"type": "command", "cmd": "echo A"})
    db.enqueue_task(eid, "Regla", "created", "/tmp/x.txt", 1, "cmd", {"type": "command", "cmd": "echo B"}, max_retries=0)
    db.enqueue_task(eid, "Regla", "created", "/tmp/x.txt", 2, "cmd", {"type": "command", "cmd": "echo C"})

    # 1. Acción 0 se ejecuta con éxito
    t0 = db.claim_next_task()
    assert t0.action_index == 0
    db.complete_task(t0.id)

    # 2. Acción 1 se ejecuta y falla inmediatamente (max_retries=0)
    t1 = db.claim_next_task()
    assert t1.action_index == 1
    will_retry = db.fail_or_retry_task(t1.id, "Error fatal en B")
    assert will_retry is False

    # 3. Intentar reclamar la siguiente tarea: Acción 2 no debe ejecutarse
    t2 = db.claim_next_task()
    assert t2 is None, "La tarea 2 dependiente NO debió ser reclamada tras fallo definitivo de la tarea 1"

    stats = db.get_stats()
    assert stats["SUCCESS"] == 1
    assert stats["FAILED"] == 2  # Tarea 1 y Tarea 2 cancelada
    db.close()


# ==============================================================================
# 7. DEBOUNCING: TORMENTA DE EVENTOS RÁPIDOS
# ==============================================================================

def test_debouncing_event_storm(tmp_path):
    """Una ráfaga de 30 eventos sobre el mismo archivo en menos de debounce_seconds debe filtrar los duplicados."""
    db = TaskDatabase(tmp_path / "storm.db")
    cfg = AutomatorConfig(
        settings={"debounce_seconds": 1.0},
        watches=[WatchConfig(path=str(tmp_path))],
        rules=[]
    )
    import threading
    ev = threading.Event()
    handler = FileAutomatorHandler(cfg, db, ev)

    # Primer evento -> no debe debouncar
    assert handler._should_debounce("modified", "archivo_modificado.txt") is False

    # Siguientes 29 eventos inmediatos -> todos deben ser ignorados por el debouncer
    for _ in range(29):
        assert handler._should_debounce("modified", "archivo_modificado.txt") is True

    # Para un archivo diferente -> no debe ser debounceado
    assert handler._should_debounce("modified", "otro_archivo.txt") is False

    db.close()
