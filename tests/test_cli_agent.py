from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest


def run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    cmd = ["uv", "run", "file-automator"] + args
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


def test_cli_init_and_list_rules_json(tmp_path):
    config_file = tmp_path / "rules_agent.yaml"

    # 1. Inicializar
    res_init = run_cli(["init", "-c", str(config_file), "--json"], cwd=tmp_path)
    assert res_init.returncode == 0
    data_init = json.loads(res_init.stdout)
    assert data_init["status"] == "success"
    assert config_file.exists()

    # 2. Listar reglas en JSON
    res_list = run_cli(["list-rules", "-c", str(config_file), "--json"], cwd=tmp_path)
    assert res_list.returncode == 0
    data_list = json.loads(res_list.stdout)
    assert data_list["status"] == "success"
    assert data_list["count"] == 1
    assert data_list["rules"][0]["name"] == "Ejemplo Inicial"


def test_cli_add_rule_and_dry_run_json(tmp_path):
    config_file = tmp_path / "rules_agent.yaml"
    run_cli(["init", "-c", str(config_file), "--json"], cwd=tmp_path)

    # 1. Agregar regla para PDFs con Webhook y Mover
    actions_json = json.dumps([
        {"type": "command", "cmd": "echo Procesando PDF {filename}"},
        {"type": "local_move", "destination": "./documentos/{filename}"}
    ])

    res_add = run_cli([
        "add-rule",
        "-c", str(config_file),
        "--name", "Filtro PDF",
        "--events", "created,modified",
        "--patterns", "*.pdf",
        "--actions", actions_json,
        "--json"
    ], cwd=tmp_path)

    assert res_add.returncode == 0
    data_add = json.loads(res_add.stdout)
    assert data_add["status"] == "success"
    assert data_add["rule_added"] == "Filtro PDF"
    assert data_add["total_rules"] == 2

    # 2. Probar Dry-Run (test-event) con archivo coincidente
    res_test = run_cli([
        "test-event",
        "-c", str(config_file),
        "--file", "factura_septiembre.pdf",
        "--event", "created",
        "--json"
    ], cwd=tmp_path)

    assert res_test.returncode == 0
    data_test = json.loads(res_test.stdout)
    assert data_test["status"] == "success"
    assert data_test["matches_count"] == 1
    assert data_test["matched_rules"][0]["rule_name"] == "Filtro PDF"
    assert data_test["matched_rules"][0]["actions_count"] == 2
    previews = data_test["matched_rules"][0]["action_previews"]
    assert previews[1]["destination"].endswith("factura_septiembre.pdf")


def test_cli_remove_rule_json(tmp_path):
    config_file = tmp_path / "rules_agent.yaml"
    run_cli(["init", "-c", str(config_file), "--json"], cwd=tmp_path)

    # Eliminar la regla de ejemplo
    res_rm = run_cli(["remove-rule", "-c", str(config_file), "--name", "Ejemplo Inicial", "--json"], cwd=tmp_path)
    assert res_rm.returncode == 0
    data_rm = json.loads(res_rm.stdout)
    assert data_rm["status"] == "success"
    assert data_rm["remaining_rules"] == 0

    # Intentar eliminar regla que no existe
    res_rm_fail = run_cli(["remove-rule", "-c", str(config_file), "--name", "Fantasma", "--json"], cwd=tmp_path)
    assert res_rm_fail.returncode != 0
    data_fail = json.loads(res_rm_fail.stdout)
    assert data_fail["status"] == "error"


def test_cli_status_and_inspect_task_json(tmp_path):
    from file_event_automator.db import TaskDatabase

    db_path = tmp_path / "test_cli.db"
    db = TaskDatabase(db_path)
    tid = db.enqueue_task(
        event_id="evt-cli",
        rule_name="ReglaCLI",
        event_type="created",
        source_path="/tmp/datos.csv",
        action_index=0,
        action_type="command",
        action_payload={"type": "command", "cmd": "echo test"}
    )
    db.close()

    # 1. Probar status --json
    res_status = run_cli(["status", "--db", str(db_path), "--json"], cwd=tmp_path)
    assert res_status.returncode == 0
    data_status = json.loads(res_status.stdout)
    assert data_status["status"] == "success"
    assert data_status["stats"]["PENDING"] == 1

    # 2. Probar inspect-task --json
    res_inspect = run_cli(["inspect-task", str(tid), "--db", str(db_path), "--json"], cwd=tmp_path)
    assert res_inspect.returncode == 0
    data_inspect = json.loads(res_inspect.stdout)
    assert data_inspect["status"] == "success"
    assert data_inspect["task"]["id"] == tid
    assert data_inspect["task"]["rule_name"] == "ReglaCLI"
