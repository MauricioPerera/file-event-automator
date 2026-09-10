from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import requests
import requests_mock
from pydantic import ValidationError

from file_event_automator.config import (
    ActionConfig,
    AutomatorConfig,
    RuleConfig,
    SettingsConfig,
    WatchConfig,
)
from file_event_automator.db import TaskDatabase
from file_event_automator.actions import (
    build_context,
    create_action,
    LocalMoveAction,
    LocalCopyAction,
    LocalDeleteAction,
    WebhookAction,
    CommandAction,
)
from file_event_automator.engine import AutomatorEngine, FileAutomatorHandler
from watchdog.events import FileMovedEvent


# ==============================================================================
# 1. TEST FIX: EVENTO MOVED DEBE EVALUAR dest_path
# ==============================================================================

def test_moved_event_evaluates_dest_path(tmp_path):
    db_file = tmp_path / "moved_test.db"
    db = TaskDatabase(db_file)
    import threading
    ev = threading.Event()

    cfg = AutomatorConfig(
        settings=SettingsConfig(debounce_seconds=0.01, stability_timeout=1.0, stability_check_interval=0.05),
        watches=[WatchConfig(path=str(tmp_path))],
        rules=[
            RuleConfig(
                name="DetectarRenombradoCSV",
                events=["moved"],
                patterns=["*.csv"],
                actions=[
                    ActionConfig(type="local_copy", destination=str(tmp_path / "copia_{filename}"))
                ]
            )
        ]
    )

    handler = FileAutomatorHandler(cfg, db, ev)

    # El archivo original era un .tmp y se renombra a un .csv
    old_file = tmp_path / "temp_download.tmp"
    new_file = tmp_path / "final_report.csv"
    new_file.write_text("id,val\n1,100", encoding="utf-8")

    event = FileMovedEvent(src_path=str(old_file), dest_path=str(new_file))
    handler.on_moved(event)

    # Esperar a que el worker de ingesta encole
    deadline = time.time() + 2.0
    task = None
    while time.time() < deadline:
        task = db.claim_next_task()
        if task:
            break
        time.sleep(0.05)

    assert task is not None, "El evento moved con destino *.csv debio activar la regla"
    assert task.source_path == str(new_file.resolve())
    assert task.src_path == str(old_file.resolve())
    handler.shutdown()
    db.close()


# ==============================================================================
# 2. TEST HARDENING: COMANDOS SEGUROS Y BLOQUEO DE SHELL
# ==============================================================================

def test_command_safe_args_execution(tmp_path):
    """Comando ejecutado con lista de argumentos (sin shell) es inmune a inyeccion."""
    test_file = tmp_path / "factura; rm -rf ;.txt"
    test_file.write_text("datos seguros", encoding="utf-8")

    ctx = build_context(str(test_file))
    cmd_action = CommandAction(
        args=["python", "-c", "import sys; print('ARG:', sys.argv[1])", "{filename}"],
        shell=False
    )
    res = cmd_action.execute(ctx)
    assert f"ARG: {test_file.name}" in res["last_command_stdout"]
    assert res["last_command_returncode"] == 0


def test_command_shell_blocked_without_permission():
    """shell=True debe ser rechazado si allow_shell_commands=False."""
    cmd_action = CommandAction(
        cmd_template="echo hola",
        shell=True,
        allow_shell_commands=False
    )
    with pytest.raises(PermissionError) as exc:
        cmd_action.execute({"filepath": "dummy"})
    assert "shell=True bloqueada por seguridad" in str(exc.value)


# ==============================================================================
# 3. TEST HARDENING: PATH JAILING Y PROTECCION ANTI-RMTREE
# ==============================================================================

def test_local_action_path_jailing(tmp_path):
    safe_zone = tmp_path / "safe"
    safe_zone.mkdir()
    danger_zone = tmp_path / "system_danger"
    danger_zone.mkdir()

    src = safe_zone / "doc.txt"
    src.write_text("test", encoding="utf-8")

    ctx = build_context(str(src))

    # Intento de mover fuera de allowed_roots
    action = LocalMoveAction(
        destination_template=str(danger_zone / "{filename}"),
        allowed_roots=[str(safe_zone)]
    )

    with pytest.raises(PermissionError) as exc:
        action.execute(ctx)
    assert "no está contenida en ninguna de las raíces permitidas" in str(exc.value)


def test_local_action_refuse_dir_overwrite(tmp_path):
    src = tmp_path / "algo.txt"
    src.write_text("archivo", encoding="utf-8")

    existing_dir = tmp_path / "carpeta_existente"
    existing_dir.mkdir()
    conflicting_dir = existing_dir / "algo.txt"
    conflicting_dir.mkdir()

    ctx = build_context(str(src))
    action = LocalMoveAction(
        destination_template=str(existing_dir),
        overwrite=True,
        allow_dir_overwrite=False
    )

    # Intento de sobreescribir un directorio existente sin permiso explícito
    with pytest.raises(IsADirectoryError) as exc:
        action.execute(ctx)
    assert "es un directorio existente" in str(exc.value)
    assert "allow_dir_overwrite: true" in str(exc.value)


def test_local_delete_refuse_dir_deletion(tmp_path):
    folder = tmp_path / "carpeta_peligrosa"
    folder.mkdir()

    ctx = {"filepath": str(folder)}
    action = LocalDeleteAction(allow_dir_deletion=False)

    with pytest.raises(IsADirectoryError) as exc:
        action.execute(ctx)
    assert "es un directorio" in str(exc.value)


# ==============================================================================
# 4. TEST HARDENING: PROTECCION SSRF Y CLAVE DE IDEMPOTENCIA
# ==============================================================================

def test_webhook_ssrf_blocked_localhost():
    """Petición a localhost / 127.0.0.1 debe ser bloqueada por defecto."""
    action = WebhookAction(
        url_template="http://127.0.0.1:8080/internal-api",
        allow_private_networks=False
    )
    with pytest.raises(PermissionError) as exc:
        action.execute({"event_id": "evt-1"})
    assert "Bloqueo SSRF" in str(exc.value)


def test_webhook_domain_allowlist():
    """Petición a dominio fuera de allowed_domains debe ser bloqueada."""
    action = WebhookAction(
        url_template="https://evil-server.com/steal-data",
        allowed_domains=["empresa.com", "slack.com"]
    )
    with pytest.raises(PermissionError) as exc:
        action.execute({"event_id": "evt-1"})
    assert "Dominio 'evil-server.com' no permitido" in str(exc.value)


def test_webhook_automatic_idempotency_header():
    """Debe inyectar Idempotency-Key con {event_id}_{action_index} si no fue provisto."""
    ctx = {"event_id": "evt-xyz-789", "action_index": 2}
    with requests_mock.Mocker() as m:
        m.post("https://api.empresa.com/webhook", json={"ok": True}, status_code=200)
        action = WebhookAction(
            url_template="https://api.empresa.com/webhook",
            allowed_domains=["empresa.com"],
            allow_private_networks=True
        )
        action.execute(ctx)

        history = m.request_history
        assert len(history) == 1
        assert history[0].headers["Idempotency-Key"] == "evt-xyz-789_2"


# ==============================================================================
# 5. TEST HARDENING: LEASE TIMEOUT Y MULTI-DAEMON
# ==============================================================================

def test_recover_stuck_tasks_lease_timeout(tmp_path):
    db_file = tmp_path / "lease.db"
    db = TaskDatabase(db_file)

    t_id = db.enqueue_task(
        event_id="lease-evt",
        rule_name="Regla",
        event_type="created",
        source_path="/tmp/doc.txt",
        action_index=0,
        action_type="command",
        action_payload={"type": "command", "cmd": "echo 1"}
    )

    task = db.claim_next_task()
    assert task.status == "PROCESSING"

    # 1. Recuperación con lease_timeout=300s: la tarea acaba de ser reclamada (updated_at reciente), no debe recuperarse
    recovered = db.recover_stuck_tasks(lease_timeout_seconds=300.0, force=False)
    assert recovered == 0, "No debió recuperar tarea con lease activo"

    # 2. Con force=True (o lease expirado): sí debe recuperarse
    forced = db.recover_stuck_tasks(force=True)
    assert forced == 1
    stats = db.get_stats()
    assert stats["PENDING"] == 1
    db.close()


# ==============================================================================
# 6. TEST HARDENING: REINTENTOS MANUALES POR EVENT_ID
# ==============================================================================

def test_retry_failed_tasks_targeted_event_id(tmp_path):
    db = TaskDatabase(tmp_path / "targeted_retry.db")

    # Evento 1 que falló
    db.enqueue_task("evt-1", "R1", "created", "/f1", 0, "cmd", {"type": "command", "cmd": "echo"}, max_retries=0)
    t1 = db.claim_next_task()
    db.fail_or_retry_task(t1.id, "error 1")

    # Evento 2 que falló
    db.enqueue_task("evt-2", "R2", "created", "/f2", 0, "cmd", {"type": "command", "cmd": "echo"}, max_retries=0)
    t2 = db.claim_next_task()
    db.fail_or_retry_task(t2.id, "error 2")

    stats = db.get_stats()
    assert stats["FAILED"] == 2

    # Reintentar SOLO evt-1
    count = db.retry_failed_tasks(event_id="evt-1")
    assert count == 1

    stats = db.get_stats()
    assert stats["PENDING"] == 1
    assert stats["FAILED"] == 1

    # Verificar que la tarea que quedó PENDING es la de evt-1
    pending_task = db.claim_next_task()
    assert pending_task.event_id == "evt-1"
    db.close()


# ==============================================================================
# 7. TEST HARDENING V2: VALIDACIONES PREVENTIVAS Y FAIL-CLOSED
# ==============================================================================

def test_path_jailing_rejects_before_creating_dirs(tmp_path):
    """Verifica que rutas no permitidas se rechacen ANTES de crear cualquier carpeta en disco."""
    safe_zone = tmp_path / "safe"
    safe_zone.mkdir()
    src = safe_zone / "data.txt"
    src.write_text("datos", encoding="utf-8")

    unauthorized_parent = tmp_path / "evil_dir" / "nested"
    assert not (tmp_path / "evil_dir").exists()

    ctx = build_context(str(src))
    action = LocalMoveAction(
        destination_template=str(unauthorized_parent / "{filename}"),
        allowed_roots=[str(safe_zone)]
    )

    with pytest.raises(PermissionError):
        action.execute(ctx)

    # La carpeta no debe haberse creado en disco
    assert not (tmp_path / "evil_dir").exists()


def test_task_lease_heartbeat(tmp_path):
    """Verifica que el heartbeat actualice el lease de una tarea en PROCESSING."""
    db = TaskDatabase(tmp_path / "hb.db")
    db.enqueue_task("evt-hb", "R", "created", "/path", 0, "cmd", {"type": "command", "cmd": "echo"})
    t = db.claim_next_task()
    assert t is not None

    time.sleep(1.0)
    ok = db.heartbeat_task(t.id)
    assert ok is True

    refreshed = db.get_task(t.id)
    assert refreshed.updated_at >= t.updated_at
    db.close()


def test_webhook_idempotency_key_propagates_action_index():
    """Verifica que dos webhooks consecutivos en el mismo evento tengan Idempotency-Key diferenciada."""
    ctx0 = build_context("/path/file.csv", event_type="created", event_id="evt-100", action_index=0)
    ctx1 = build_context("/path/file.csv", event_type="created", event_id="evt-100", action_index=1)
    assert ctx0["action_index"] == 0
    assert ctx1["action_index"] == 1

    with requests_mock.Mocker() as m:
        m.post("https://api.empresa.com/wh", json={"ok": True})
        action = WebhookAction(
            url_template="https://api.empresa.com/wh",
            allowed_domains=["empresa.com"],
            allow_private_networks=True
        )
        action.execute(ctx0)
        action.execute(ctx1)

        history = m.request_history
        assert len(history) == 2
        assert history[0].headers["Idempotency-Key"] == "evt-100_0"
        assert history[1].headers["Idempotency-Key"] == "evt-100_1"


def test_webhook_ssrf_fail_closed_on_dns_failure():
    """Verifica que dominios no resolubles fallen cerrado con ConnectionError cuando allow_private_networks=False."""
    action = WebhookAction(url_template="https://dominio-inexistente-123456789.xyz/hook")
    with pytest.raises(ConnectionError) as exc:
        action.execute({"filepath": "dummy"})
    assert "Bloqueo SSRF (Fail-Closed)" in str(exc.value)


def test_strict_mode_validation():
    """Verifica que strict_mode=True exija allowed_roots y allowed_webhook_domains."""
    cfg_dict = {
        "settings": {"strict_mode": True},
        "watches": [{"path": "./inbox"}],
        "rules": [{"name": "R", "actions": [{"type": "local_delete"}]}]
    }
    with pytest.raises(ValueError) as exc:
        AutomatorConfig.model_validate(cfg_dict)
    assert "strict_mode activado" in str(exc.value)


def test_webhook_ssrf_anti_dns_rebinding(monkeypatch):
    """Simula ataque de DNS rebinding donde el host parece público al inicio pero el socket conecta a 127.0.0.1."""
    class MockSocket:
        def getpeername(self):
            return ("127.0.0.1", 80)
        def close(self):
            pass

    from urllib3.connection import HTTPConnection
    monkeypatch.setattr(HTTPConnection, "_new_conn", lambda self: MockSocket())

    action = WebhookAction(
        url_template="http://api.test/rebinding-attack",
        allow_private_networks=False
    )
    with pytest.raises(PermissionError) as exc:
        action.execute({"filepath": "dummy"})
    assert "DNS Rebinding" in str(exc.value)
    assert "127.0.0.1" in str(exc.value)


def test_webhook_trust_env_disabled_under_ssrf(monkeypatch):
    """Verifica que requests.Session ignore proxies de entorno (trust_env=False) bajo protección SSRF."""
    captured_trust_env = []
    orig_request = requests.Session.request

    def mock_request(self, *args, **kwargs):
        captured_trust_env.append(self.trust_env)
        return orig_request(self, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", mock_request)
    with requests_mock.Mocker() as m:
        m.post("https://api.empresa.com/wh", json={"ok": True})
        action = WebhookAction(
            url_template="https://api.empresa.com/wh",
            allowed_domains=["empresa.com"],
            allow_private_networks=False
        )
        action.execute({"filepath": "dummy"})

    # Bajo allow_private_networks=False, trust_env debe ser False para ignorar proxies del entorno
    assert len(captured_trust_env) == 1
    assert captured_trust_env[0] is False


def test_local_action_symlink_defense_blocked(tmp_path):
    """Verifica que enlaces simbólicos en destino sean bloqueados cuando allowed_roots está activo."""
    safe_zone = tmp_path / "safe"
    safe_zone.mkdir()
    danger_zone = tmp_path / "danger"
    danger_zone.mkdir()

    src = safe_zone / "doc.txt"
    src.write_text("sensible", encoding="utf-8")

    link = safe_zone / "symlink_trap"
    try:
        link.symlink_to(danger_zone, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Creación de symlinks no permitida sin privilegios elevados en este entorno")

    ctx = build_context(str(src))
    action = LocalMoveAction(
        destination_template=str(link / "{filename}"),
        allowed_roots=[str(safe_zone)],
        allow_symlinks=False
    )

    with pytest.raises(PermissionError) as exc:
        action.execute(ctx)
    assert "Symlink Defense" in str(exc.value)


def test_local_source_symlink_defense_blocked(tmp_path):
    """Un symlink de origen no debe permitir leer o mover fuera de allowed_roots."""
    safe_zone = tmp_path / "safe"
    safe_zone.mkdir()
    danger_zone = tmp_path / "danger"
    danger_zone.mkdir()
    outside = danger_zone / "secret.txt"
    outside.write_text("secreto", encoding="utf-8")
    source_link = safe_zone / "input.txt"
    try:
        source_link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Creación de symlinks no permitida sin privilegios elevados en este entorno")

    action = LocalCopyAction(
        destination_template=str(safe_zone / "copy_{filename}"),
        allowed_roots=[str(safe_zone)],
        allow_symlinks=False,
    )
    with pytest.raises(PermissionError) as exc:
        action.execute(build_context(str(source_link)))
    assert "Symlink Defense" in str(exc.value)


def test_old_lease_cannot_complete_task(tmp_path):
    """Un worker cuyo lease fue reemplazado no puede completar la tarea."""
    db = TaskDatabase(tmp_path / "lease_owner.db")
    db.enqueue_task("evt", "R", "created", "/path", 0, "cmd", {"type": "command", "cmd": "echo"})
    first = db.claim_next_task()
    assert first is not None and first.lease_id
    db.recover_stuck_tasks(force=True)
    second = db.claim_next_task()
    assert second is not None and second.lease_id != first.lease_id
    assert db.complete_task(first.id, first.lease_id) is False
    assert db.complete_task(second.id, second.lease_id) is True
    db.close()


def test_deduplicate_action_catalog_and_duplicate_move(tmp_path):
    """Archivos con el mismo contenido se identifican aunque tengan nombres distintos."""
    from file_event_automator.actions import DeduplicateAction

    safe = tmp_path / "safe"
    duplicates = safe / "duplicates"
    safe.mkdir()
    first = safe / "first.txt"
    second = safe / "second.txt"
    first.write_text("same content", encoding="utf-8")
    second.write_text("same content", encoding="utf-8")
    db = TaskDatabase(tmp_path / "catalog.db")
    action = DeduplicateAction(
        database=db,
        allowed_roots=[str(safe)],
        on_duplicate="move",
        duplicate_destination=str(duplicates / "{filename}"),
    )

    first_ctx = action.execute(build_context(str(first)))
    second_ctx = action.execute(build_context(str(second)))
    assert first_ctx["duplicate"] is False
    assert second_ctx["duplicate"] is True
    assert second_ctx["duplicate_of"] == str(first.resolve())
    assert not second.exists()
    assert (duplicates / "second.txt").exists()
    db.close()


def test_deduplicate_must_be_terminal():
    with pytest.raises(ValidationError) as exc:
        RuleConfig(
            name="invalid-dedupe",
            actions=[
                ActionConfig(type="deduplicate"),
                ActionConfig(type="local_delete"),
            ],
        )
    assert "debe ser la última" in str(exc.value)



