from __future__ import annotations

import json
import time
from pathlib import Path
import pytest
import requests_mock

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
from file_event_automator.engine import AutomatorEngine


def test_config_validation(tmp_path):
    config_file = tmp_path / "test_rules.yaml"
    config_content = """
settings:
  debounce_seconds: 0.5
  stability_timeout: 2.0
  max_workers: 2
  db_path: "test_queue.db"

watches:
  - path: "./watch_test"
    recursive: true

rules:
  - name: "Test Rule"
    events: ["created"]
    patterns: ["*.txt"]
    actions:
      - type: "local_copy"
        destination: "./backup/{filename}"
"""
    config_file.write_text(config_content, encoding="utf-8")
    cfg = load_config(config_file)

    assert cfg.settings.debounce_seconds == 0.5
    assert cfg.settings.max_workers == 2
    assert len(cfg.watches) == 1
    assert cfg.watches[0].recursive is True
    assert len(cfg.rules) == 1
    assert cfg.rules[0].actions[0].type == "local_copy"


def test_database_lifecycle(tmp_path):
    db_file = tmp_path / "test.db"
    db = TaskDatabase(db_file)

    # 1. Encolar dos acciones dependientes (index 0 y index 1)
    event_id = "evt-123"
    t1_id = db.enqueue_task(
        event_id=event_id,
        rule_name="Pipeline",
        event_type="created",
        source_path="/tmp/test.txt",
        action_index=0,
        action_type="command",
        action_payload={"type": "command", "cmd": "echo 1"}
    )
    t2_id = db.enqueue_task(
        event_id=event_id,
        rule_name="Pipeline",
        event_type="created",
        source_path="/tmp/test.txt",
        action_index=1,
        action_type="local_delete",
        action_payload={"type": "local_delete"}
    )

    # Inicialmente solo la tarea 0 debe ser ejecutable (la 1 depende de que la 0 tenga éxito)
    claimed1 = db.claim_next_task()
    assert claimed1 is not None
    assert claimed1.id == t1_id
    assert claimed1.action_index == 0

    # Si intentamos reclamar otra mientras la primera está en PROCESSING, no hay nada disponible
    assert db.claim_next_task() is None

    # Completamos la tarea 0
    db.complete_task(claimed1.id)

    # Ahora la tarea 1 debe ser ejecutable
    claimed2 = db.claim_next_task()
    assert claimed2 is not None
    assert claimed2.id == t2_id
    assert claimed2.action_index == 1

    # Marcamos la tarea 1 como completada
    db.complete_task(claimed2.id)

    stats = db.get_stats()
    assert stats["SUCCESS"] == 2
    assert stats["PENDING"] == 0

    db.close()


def test_database_retry_and_downstream_cancel(tmp_path):
    db_file = tmp_path / "test_retry.db"
    db = TaskDatabase(db_file)

    event_id = "evt-fail"
    db.enqueue_task(
        event_id=event_id,
        rule_name="RuleFail",
        event_type="created",
        source_path="/tmp/test.txt",
        action_index=0,
        action_type="webhook",
        action_payload={"type": "webhook", "url": "https://invalid.test"},
        max_retries=1
    )
    db.enqueue_task(
        event_id=event_id,
        rule_name="RuleFail",
        event_type="created",
        source_path="/tmp/test.txt",
        action_index=1,
        action_type="local_delete",
        action_payload={"type": "local_delete"}
    )

    # Primer intento tarea 0
    t = db.claim_next_task()
    assert t is not None
    will_retry = db.fail_or_retry_task(t.id, "Connection failed")
    assert will_retry is True

    # Segundo intento (reintento 1 == max_retries)
    t = db.claim_next_task()
    assert t is not None
    will_retry = db.fail_or_retry_task(t.id, "Connection failed again")
    assert will_retry is False  # Ahora pasa a FAILED

    # Al fallar la tarea 0, la tarea 1 downstream debe cancelarse
    assert db.claim_next_task() is None
    stats = db.get_stats()
    assert stats["FAILED"] == 2

    # Probar retry_failed_tasks
    recovered = db.retry_failed_tasks()
    assert recovered == 2
    stats = db.get_stats()
    assert stats["PENDING"] == 2

    db.close()


def test_local_actions(tmp_path):
    src_file = tmp_path / "data.csv"
    src_file.write_text("a,b,c\n1,2,3", encoding="utf-8")

    ctx = build_context(str(src_file), event_type="created", event_id="evt-1")

    # 1. Copiar
    dest_copy = tmp_path / "backup" / "{filename}"
    copy_action = LocalCopyAction(destination_template=str(dest_copy))
    ctx = copy_action.execute(ctx)
    assert (tmp_path / "backup" / "data.csv").exists()
    assert src_file.exists()

    # 2. Mover
    dest_move = tmp_path / "processed" / "{stem}_v1{ext}"
    move_action = LocalMoveAction(destination_template=str(dest_move))
    ctx = move_action.execute(ctx)
    assert not src_file.exists()
    assert Path(ctx["filepath"]).exists()
    assert Path(ctx["filepath"]).name == "data_v1.csv"

    # 3. Eliminar
    del_action = LocalDeleteAction()
    ctx = del_action.execute(ctx)
    assert not Path(ctx["filepath"]).exists()


def test_webhook_action(tmp_path):
    file = tmp_path / "report.json"
    file.write_text("{}", encoding="utf-8")
    ctx = build_context(str(file), event_type="created", event_id="evt-wh")

    with requests_mock.Mocker() as m:
        m.post("https://api.test/webhook", json={"ok": True}, status_code=200)

        action = WebhookAction(
            url_template="https://api.test/webhook",
            method="POST",
            headers={"X-Test": "{filename}"},
            json_payload={"file": "{filename}", "size": "{filesize}"}
        )
        res_ctx = action.execute(ctx)
        assert res_ctx["last_webhook_status"] == 200
        history = m.request_history
        assert len(history) == 1
        assert history[0].headers["X-Test"] == "report.json"
        body = history[0].json()
        assert body["file"] == "report.json"


def test_command_action(tmp_path):
    file = tmp_path / "doc.txt"
    file.write_text("antigravity", encoding="utf-8")
    ctx = build_context(str(file), event_type="created")

    cmd = CommandAction(
        cmd_template='python -c "import sys; print(\'TEST_OUTPUT:\' + sys.argv[1])" "{filename}"',
        check_returncode=True
    )
    res_ctx = cmd.execute(ctx)
    assert "TEST_OUTPUT:doc.txt" in res_ctx["last_command_stdout"]
    assert res_ctx["last_command_returncode"] == 0


def test_stabilizer_stable_file(tmp_path):
    file = tmp_path / "ready.txt"
    file.write_text("Hello World", encoding="utf-8")
    ready = wait_for_file_ready(file, timeout=1.0, check_interval=0.1)
    assert ready is True


def test_engine_integration(tmp_path):
    watch_dir = tmp_path / "inbox"
    watch_dir.mkdir()
    proc_dir = tmp_path / "processed"
    db_file = tmp_path / "engine_test.db"

    cfg = AutomatorConfig(
        settings={
            "debounce_seconds": 0.1,
            "stability_timeout": 1.0,
            "stability_check_interval": 0.1,
            "max_workers": 2,
            "db_path": str(db_file),
            "log_level": "DEBUG"
        },
        watches=[WatchConfig(path=str(watch_dir), recursive=False)],
        rules=[
            RuleConfig(
                name="MoveTextFiles",
                events=["created"],
                patterns=["*.txt"],
                actions=[
                    ActionConfig(
                        type="command",
                        cmd="echo Processing {filename}"
                    ),
                    ActionConfig(
                        type="local_move",
                        destination=str(proc_dir / "{filename}")
                    )
                ]
            )
        ]
    )

    engine = AutomatorEngine(cfg)
    engine.start()

    try:
        # Crear un archivo en el directorio observado
        test_file = watch_dir / "sample.txt"
        test_file.write_text("Automator integration content", encoding="utf-8")

        # Esperar a que el motor lo procese
        deadline = time.time() + 6.0
        success = False
        while time.time() < deadline:
            stats = engine.db.get_stats()
            if stats["SUCCESS"] >= 2:
                success = True
                break
            time.sleep(0.2)

        assert success, f"Las acciones no se completaron a tiempo. Stats: {engine.db.get_stats()}"
        assert not test_file.exists(), "El archivo original no debió quedar en inbox"
        assert (proc_dir / "sample.txt").exists(), "El archivo debió moverse a processed"

    finally:
        engine.stop()
