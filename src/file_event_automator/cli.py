from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import (
    ActionConfig,
    AutomatorConfig,
    RuleConfig,
    WatchConfig,
    load_config,
    save_config,
)
from .db import TaskDatabase
from .engine import AutomatorEngine
from .actions import build_context, interpolate_template

CLI_SCHEMA_VERSION = 1


def load_actions(args) -> list[dict[str, Any]]:
    """Carga acciones desde inline JSON o desde un archivo UTF-8."""
    if getattr(args, "actions_file", None):
        raw = json.loads(Path(args.actions_file).read_text(encoding="utf-8"))
    else:
        raw = json.loads(args.actions)
    if not isinstance(raw, list):
        raise ValueError("Las acciones deben ser una lista JSON.")
    return raw


def structured_error(code: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": CLI_SCHEMA_VERSION,
        "status": "error",
        "error_code": code,
        "message": message,
    }


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] (%(threadName)s) %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )


def print_output(data: Dict[str, Any], is_json: bool = False, text_msg: str = ""):
    if is_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(text_msg)


def cmd_init(args):
    config_path = Path(args.config)
    if config_path.exists() and not args.force:
        err = f"El archivo de configuración '{config_path}' ya existe. Usa --force para sobrescribir."
        if args.json:
            print(json.dumps({"status": "error", "message": err}))
            sys.exit(1)
        print(f"[!] {err}", file=sys.stderr)
        sys.exit(1)

    watch_dir = args.watch or "./inbox"
    initial_config = AutomatorConfig(
        watches=[WatchConfig(path=watch_dir, recursive=False)],
        rules=[
            RuleConfig(
                name="Ejemplo Inicial",
                events=["created"],
                patterns=["*.txt", "*.csv"],
                actions=[
                    ActionConfig(
                        type="command",
                        cmd='python -c "import sys; print(\'Detectado:\', sys.argv[1])" "{filepath}"'
                    ),
                    ActionConfig(
                        type="local_move",
                        destination=f"{watch_dir}/procesados/{{filename}}"
                    )
                ]
            )
        ]
    )

    save_config(initial_config, config_path)
    Path(watch_dir).mkdir(parents=True, exist_ok=True)

    print_output(
        data={"status": "success", "message": "Configuración inicializada", "config_path": str(config_path)},
        is_json=args.json,
        text_msg=f"[OK] Archivo de configuración creado en '{config_path}' con directorio de escucha '{watch_dir}'."
    )


def cmd_list_rules(args):
    config_path = Path(args.config)
    try:
        config = load_config(config_path)
        rules_data = [r.model_dump(by_alias=True) for r in config.rules]
        if args.json:
            print(json.dumps({"status": "success", "count": len(rules_data), "rules": rules_data}, indent=2, ensure_ascii=False))
        else:
            print(f"=== Reglas configuradas en {config_path} ({len(rules_data)}) ===")
            for idx, r in enumerate(config.rules, start=1):
                print(f"[{idx}] {r.name}")
                print(f"    Eventos:  {r.events}")
                print(f"    Patrones: {r.patterns} (Ignorar: {r.ignore_patterns})")
                print(f"    Acciones: {len(r.actions)}")
                for aidx, a in enumerate(r.actions, start=1):
                    detail = a.destination or a.url or a.cmd or a.duplicate_destination or a.type
                    print(f"      {aidx}. {a.type} -> {detail}")
    except Exception as e:
        if args.json:
            print(json.dumps({"status": "error", "message": str(e)}))
            sys.exit(1)
        print(f"[ERROR] No se pudieron listar las reglas: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_add_rule(args):
    config_path = Path(args.config)
    try:
        if config_path.exists():
            config = load_config(config_path)
        else:
            config = AutomatorConfig()

        # Parsear acciones desde JSON inline o archivo
        actions_raw = load_actions(args)
        actions = [ActionConfig.model_validate(a) for a in actions_raw]

        new_rule = RuleConfig(
            name=args.name,
            events=args.events.split(","),
            patterns=args.patterns.split(","),
            ignore_patterns=args.ignore_patterns.split(",") if args.ignore_patterns else [],
            max_retries=args.max_retries,
            actions=actions
        )

        # Evitar nombres duplicados
        config.rules = [r for r in config.rules if r.name != new_rule.name]
        config.rules.append(new_rule)

        if not args.dry_run:
            save_config(config, config_path)

        print_output(
            data={
                "schema_version": CLI_SCHEMA_VERSION,
                "status": "planned" if args.dry_run else "success",
                "applied": not args.dry_run,
                "message": f"Regla '{new_rule.name}' {'previsualizada' if args.dry_run else 'agregada correctamente'}",
                "rule_added": new_rule.name,
                "rule": new_rule.model_dump(by_alias=True),
                "total_rules": len(config.rules)
            },
            is_json=args.json,
            text_msg=f"[OK] Regla '{new_rule.name}' agregada exitosamente a '{config_path}'."
        )
    except Exception as e:
        if args.json:
            print(json.dumps(structured_error("ADD_RULE_FAILED", str(e)), ensure_ascii=False))
            sys.exit(1)
        print(f"[ERROR] Error al agregar regla: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_remove_rule(args):
    config_path = Path(args.config)
    try:
        config = load_config(config_path)
        initial_count = len(config.rules)
        config.rules = [r for r in config.rules if r.name != args.name]

        if len(config.rules) == initial_count:
            msg = f"No se encontró ninguna regla llamada '{args.name}'."
            if args.json:
                print(json.dumps({"status": "error", "message": msg}))
                sys.exit(1)
            print(f"[!] {msg}", file=sys.stderr)
            sys.exit(1)

        if not args.dry_run:
            save_config(config, config_path)
        print_output(
            data={"schema_version": CLI_SCHEMA_VERSION, "status": "planned" if args.dry_run else "success", "applied": not args.dry_run, "message": f"Regla '{args.name}' {'previsualizada para eliminación' if args.dry_run else 'eliminada'}", "remaining_rules": len(config.rules)},
            is_json=args.json,
            text_msg=f"[OK] Regla '{args.name}' eliminada de '{config_path}'."
        )
    except Exception as e:
        if args.json:
            print(json.dumps({"status": "error", "message": str(e)}))
            sys.exit(1)
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


def cmd_test_event(args):
    """Dry-run: Simula qué reglas y acciones se dispararían ante un archivo dado."""
    config_path = Path(args.config)
    try:
        config = load_config(config_path)
        target_path = Path(args.file)
        filename = target_path.name
        event_type = args.event

        matched = []
        ctx = build_context(str(target_path), event_type=event_type, event_id="dry-run-test")

        for rule in config.rules:
            if event_type not in rule.events:
                continue

            # Check ignore
            ignored = False
            for ign in rule.ignore_patterns:
                if fnmatch.fnmatch(filename, ign):
                    ignored = True
                    break
            if ignored:
                continue

            # Check match
            match = any(fnmatch.fnmatch(filename, pat) for pat in rule.patterns)
            if match:
                action_previews = []
                for a in rule.actions:
                    preview = {"type": a.type}
                    if a.destination:
                        preview["destination"] = interpolate_template(a.destination, ctx)
                    if a.url:
                        preview["url"] = interpolate_template(a.url, ctx)
                    if a.cmd:
                        preview["cmd"] = interpolate_template(a.cmd, ctx)
                    if a.json_payload:
                        preview["json"] = interpolate_template(a.json_payload, ctx)
                    if a.type == "deduplicate":
                        preview["hash_algorithm"] = a.hash_algorithm
                        preview["on_duplicate"] = a.on_duplicate
                        if a.duplicate_destination:
                            preview["duplicate_destination"] = interpolate_template(a.duplicate_destination, ctx)
                    action_previews.append(preview)

                matched.append({
                    "rule_name": rule.name,
                    "actions_count": len(rule.actions),
                    "action_previews": action_previews
                })

        if args.json:
            print(json.dumps({
                "status": "success",
                "file": str(target_path),
                "event": event_type,
                "matches_count": len(matched),
                "matched_rules": matched
            }, indent=2, ensure_ascii=False))
        else:
            print(f"=== Simulación Dry-Run para evento '{event_type}' en '{target_path.name}' ===")
            if not matched:
                print("  [!] Ninguna regla coincide con este archivo y evento.")
            else:
                for m in matched:
                    print(f"  * Regla activada: '{m['rule_name']}'")
                    for idx, a in enumerate(m["action_previews"], start=1):
                        print(f"      {idx}. {a['type']} -> {a.get('destination') or a.get('cmd') or a.get('url')}")
    except Exception as e:
        if args.json:
            print(json.dumps({"status": "error", "message": str(e)}))
            sys.exit(1)
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


def cmd_inspect_task(args):
    db_path = Path(args.db)
    if not db_path.exists():
        msg = f"La base de datos '{db_path}' no existe."
        if args.json:
            print(json.dumps(structured_error("RULE_NOT_FOUND", msg), ensure_ascii=False))
            sys.exit(1)
        print(f"[!] {msg}", file=sys.stderr)
        sys.exit(1)

    db = TaskDatabase(db_path)
    task = db.get_task(args.task_id)
    db.close()

    if not task:
        msg = f"No se encontró la tarea #{args.task_id}."
        if args.json:
            print(json.dumps({"status": "error", "message": msg}))
            sys.exit(1)
        print(f"[!] {msg}", file=sys.stderr)
        sys.exit(1)

    data = asdict(task)
    if args.json:
        print(json.dumps({"status": "success", "task": data}, indent=2, ensure_ascii=False))
    else:
        print(f"=== Detalle de Tarea #{task.id} ===")
        print(f"  Regla:        {task.rule_name}")
        print(f"  Evento:       {task.event_type} en {task.source_path}")
        print(f"  Acción #{task.action_index}: {task.action_type}")
        print(f"  Estado:       {task.status} (Reintentos: {task.retries}/{task.max_retries})")
        if task.error_message:
            print(f"  Error:        {task.error_message}")
        print(f"  Payload:      {json.dumps(task.action_payload)}")
        print(f"  Registrada:   {task.created_at} | Actualizada: {task.updated_at}")


def cmd_status(args):
    db_path = Path(args.db)
    if not db_path.exists():
        if args.json:
            print(json.dumps({
                "status": "success",
                "db": str(db_path),
                "exists": False,
                "stats": {"PENDING": 0, "PROCESSING": 0, "SUCCESS": 0, "FAILED": 0},
                "total": 0
            }))
        else:
            print(f"La base de datos '{db_path}' aún no ha sido creada (sin tareas registradas).")
        return

    db = TaskDatabase(db_path)
    stats = db.get_stats()
    total = sum(stats.values())
    recent_failed = [asdict(t) for t in db.get_recent_tasks(limit=5, status="FAILED")]
    db.close()

    if args.json:
        print(json.dumps({
            "status": "success",
            "db": str(db_path),
            "exists": True,
            "stats": stats,
            "total": total,
            "recent_failed": recent_failed
        }, indent=2, ensure_ascii=False))
    else:
        print(f"=== Estado de la Cola de Tareas ({db_path}) ===")
        print(f"  Total tareas registradas: {total}")
        print(f"  - PENDING:    {stats['PENDING']}")
        print(f"  - PROCESSING: {stats['PROCESSING']}")
        print(f"  - SUCCESS:    {stats['SUCCESS']}")
        print(f"  - FAILED:     {stats['FAILED']}")
        if recent_failed:
            print("\n  [!] Últimas tareas fallidas:")
            for rf in recent_failed:
                print(f"    * #{rf['id']} ({rf['rule_name']}): {rf['error_message']}")


def cmd_retry_failed(args):
    db_path = Path(args.db)
    if not db_path.exists():
        msg = f"La base de datos '{db_path}' no existe."
        if args.json:
            print(json.dumps({"status": "error", "message": msg}))
            sys.exit(1)
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    event_id = getattr(args, "event_id", None)
    db = TaskDatabase(db_path)
    count = db.retry_failed_tasks(event_id=event_id)
    db.close()

    print_output(
        data={"status": "success", "retried_count": count, "event_id": event_id},
        is_json=args.json,
        text_msg=f"[*] {count} tareas en estado FAILED fueron devueltas a PENDING con reintentos reiniciados."
    )



def cmd_validate(args):
    config_path = Path(args.config)
    try:
        config = load_config(config_path)
        security_warnings = []
        if config.settings.allowed_roots is None:
            security_warnings.append("allowed_roots no está configurado: las acciones locales no tienen restricción de rutas (Path Jailing inactivo).")
        if config.settings.allowed_webhook_domains is None:
            has_webhooks = any(a.type == "webhook" for r in config.rules for a in r.actions)
            if has_webhooks:
                security_warnings.append("allowed_webhook_domains no está configurado: los webhooks pueden enviar peticiones a cualquier dominio público.")
        if config.settings.allow_shell_commands:
            security_warnings.append("allow_shell_commands está activado: los comandos pueden ejecutarse en shell del sistema.")
        if config.settings.allow_private_networks:
            security_warnings.append("allow_private_networks está activado: la protección contra SSRF para IPs privadas está deshabilitada.")

        data = {
            "status": "success",
            "valid": True,
            "config_path": str(config_path),
            "watches": [w.model_dump() for w in config.watches],
            "rules_count": len(config.rules),
            "settings": config.settings.model_dump(),
            "security_warnings": security_warnings
        }
        if args.json:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(f"[OK] El archivo '{config_path}' es válido:")
            print(f"  - Directorios a monitorear: {len(config.watches)}")
            for w in config.watches:
                print(f"    * {w.path} (recursivo: {w.recursive})")
            print(f"  - Reglas definidas: {len(config.rules)}")
            for r in config.rules:
                print(f"    * '{r.name}' -> Eventos: {r.events}, Patrones: {r.patterns}, Acciones: {len(r.actions)}")
            if security_warnings:
                print(f"  - Advertencias de Seguridad ({len(security_warnings)}):")
                for w in security_warnings:
                    print(f"    [!] {w}")
    except Exception as e:
        if args.json:
            print(json.dumps({"status": "error", "valid": False, "message": str(e)}))
            sys.exit(1)
        print(f"[ERROR] Error de validación en '{config_path}': {e}", file=sys.stderr)
        sys.exit(1)


def cmd_run(args):
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: El archivo de configuración '{config_path}' no existe.", file=sys.stderr)
        sys.exit(1)

    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"Error cargando configuración: {e}", file=sys.stderr)
        sys.exit(1)

    setup_logging(config.settings.log_level)
    logging.info(f"Cargando configuración desde {config_path.resolve()}...")

    engine = AutomatorEngine(config)
    engine.start()

    print(f"[*] File Event Automator activo. Monitoreando {len(config.watches)} directorios. Presiona Ctrl+C para salir.")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[!] Señal de interrupción recibida.")
        engine.stop()
        print("[*] Salida ordenada completada.")


def main():
    parser = argparse.ArgumentParser(
        prog="file-automator",
        description="Sistema de automatización basado en eventos de archivos optimizado para humanos y Agentes de IA"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcomando: init
    p_init = subparsers.add_parser("init", help="Crea un archivo de configuración inicial")
    p_init.add_argument("-c", "--config", default="rules.yaml", help="Ruta al archivo YAML (default: rules.yaml)")
    p_init.add_argument("--watch", default="./inbox", help="Directorio inicial para observar (default: ./inbox)")
    p_init.add_argument("--force", action="store_true", help="Sobrescribir archivo si ya existe")
    p_init.add_argument("--json", action="store_true", help="Salida estructurada en formato JSON")
    p_init.set_defaults(func=cmd_init)

    # Subcomando: list-rules
    p_list = subparsers.add_parser("list-rules", help="Lista todas las reglas configuradas")
    p_list.add_argument("-c", "--config", default="rules.yaml", help="Ruta al archivo YAML (default: rules.yaml)")
    p_list.add_argument("--json", action="store_true", help="Salida estructurada en formato JSON")
    p_list.set_defaults(func=cmd_list_rules)

    # Subcomando: add-rule
    p_add = subparsers.add_parser("add-rule", help="Agrega programáticamente una regla al archivo YAML")
    p_add.add_argument("-c", "--config", default="rules.yaml", help="Ruta al archivo YAML (default: rules.yaml)")
    p_add.add_argument("--name", required=True, help="Nombre único para la regla")
    p_add.add_argument("--events", default="created", help="Eventos separados por coma: created,modified,deleted (default: created)")
    p_add.add_argument("--patterns", default="*", help="Patrones glob separados por coma (ej: *.csv,*.pdf)")
    p_add.add_argument("--ignore-patterns", default="", help="Patrones a ignorar separados por coma")
    p_add.add_argument("--max-retries", type=int, default=3, help="Reintentos máximos (default: 3)")
    actions_group = p_add.add_mutually_exclusive_group(required=True)
    actions_group.add_argument("--actions", help="Array JSON con las acciones a ejecutar")
    actions_group.add_argument("--actions-file", help="Archivo UTF-8 que contiene el array JSON de acciones")
    p_add.add_argument("--dry-run", action="store_true", help="Previsualizar el cambio sin modificar el YAML")
    p_add.add_argument("--json", action="store_true", help="Salida estructurada en formato JSON")
    p_add.set_defaults(func=cmd_add_rule)

    # Subcomando: remove-rule
    p_rm = subparsers.add_parser("remove-rule", help="Elimina una regla por su nombre")
    p_rm.add_argument("-c", "--config", default="rules.yaml", help="Ruta al archivo YAML (default: rules.yaml)")
    p_rm.add_argument("--name", required=True, help="Nombre de la regla a eliminar")
    p_rm.add_argument("--dry-run", action="store_true", help="Previsualizar la eliminación sin modificar el YAML")
    p_rm.add_argument("--json", action="store_true", help="Salida estructurada en formato JSON")
    p_rm.set_defaults(func=cmd_remove_rule)

    # Subcomando: test-event (dry-run)
    p_test = subparsers.add_parser("test-event", help="Simula (dry-run) qué regla y acciones se dispararían")
    p_test.add_argument("-c", "--config", default="rules.yaml", help="Ruta al archivo YAML (default: rules.yaml)")
    p_test.add_argument("--file", required=True, help="Nombre o ruta del archivo a simular")
    p_test.add_argument("--event", default="created", choices=["created", "modified", "deleted", "moved"], help="Tipo de evento")
    p_test.add_argument("--json", action="store_true", help="Salida estructurada en formato JSON")
    p_test.set_defaults(func=cmd_test_event)

    # Subcomando: inspect-task
    p_inspect = subparsers.add_parser("inspect-task", help="Inspecciona el detalle de una tarea en SQLite")
    p_inspect.add_argument("task_id", type=int, help="ID de la tarea a inspeccionar")
    p_inspect.add_argument("--db", default="automator.db", help="Ruta a la base de datos (default: automator.db)")
    p_inspect.add_argument("--json", action="store_true", help="Salida estructurada en formato JSON")
    p_inspect.set_defaults(func=cmd_inspect_task)

    # Subcomando: status
    p_status = subparsers.add_parser("status", help="Muestra el estado de la cola de tareas")
    p_status.add_argument("--db", default="automator.db", help="Ruta a la base de datos (default: automator.db)")
    p_status.add_argument("--json", action="store_true", help="Salida estructurada en formato JSON")
    p_status.set_defaults(func=cmd_status)

    # Subcomando: retry-failed
    p_retry = subparsers.add_parser("retry-failed", help="Reintenta tareas fallidas")
    p_retry.add_argument("--db", default="automator.db", help="Ruta a la base de datos (default: automator.db)")
    p_retry.add_argument("--event-id", default=None, help="Reintentar solo tareas de un event_id específico")
    p_retry.add_argument("--json", action="store_true", help="Salida estructurada en formato JSON")
    p_retry.set_defaults(func=cmd_retry_failed)

    # Subcomando: validate
    p_val = subparsers.add_parser("validate", help="Valida la sintaxis del archivo YAML")
    p_val.add_argument("-c", "--config", default="rules.yaml", help="Ruta al archivo YAML (default: rules.yaml)")
    p_val.add_argument("--json", action="store_true", help="Salida estructurada en formato JSON")
    p_val.set_defaults(func=cmd_validate)

    # Subcomando: run
    p_run = subparsers.add_parser("run", help="Inicia el daemon de monitoreo")
    p_run.add_argument("-c", "--config", default="rules.yaml", help="Ruta al archivo YAML (default: rules.yaml)")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
