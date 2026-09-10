from __future__ import annotations

import fnmatch
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional, Tuple

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .actions import build_context, create_action
from .config import ActionConfig, AutomatorConfig, RuleConfig
from .db import TaskDatabase, TaskRecord
from .stabilizer import wait_for_file_ready

logger = logging.getLogger("file_event_automator.engine")


class FileAutomatorHandler(FileSystemEventHandler):
    """Handler de Watchdog que filtra, estabiliza y encola tareas en SQLite."""

    def __init__(self, config: AutomatorConfig, db: TaskDatabase, task_trigger: threading.Event):
        super().__init__()
        self.config = config
        self.db = db
        self.task_trigger = task_trigger
        self._last_events: Dict[Tuple[str, str], float] = {}
        self._lock = threading.Lock()
        # Pool ligero para no bloquear el hilo del observer con el file stabilizer
        self._ingest_pool = ThreadPoolExecutor(max_workers=config.settings.max_workers, thread_name_prefix="IngestWorker")

    def _should_debounce(self, event_type: str, path: str) -> bool:
        now = time.time()
        key = (event_type, path)
        with self._lock:
            last = self._last_events.get(key, 0)
            if now - last < self.config.settings.debounce_seconds:
                return True
            self._last_events[key] = now
            # Limpiar entradas viejas de más de 60 segundos
            if len(self._last_events) > 500:
                self._last_events = {k: v for k, v in self._last_events.items() if now - v < 60}
        return False

    def _matches_rule(self, rule: RuleConfig, filepath: str, event_type: str) -> bool:
        if event_type not in rule.events:
            return False

        filename = Path(filepath).name

        # Comprobar si está en ignore_patterns
        for ign in rule.ignore_patterns:
            if fnmatch.fnmatch(filename, ign):
                return False

        # Comprobar si coincide con al menos un patrón
        for pat in rule.patterns:
            if fnmatch.fnmatch(filename, pat):
                return True

        return False

    def _handle_event(self, event: FileSystemEvent, event_type: str):
        if event.is_directory:
            return

        if event_type == "moved":
            dest = getattr(event, "dest_path", None)
            filepath = str(Path(dest).resolve()) if dest else str(Path(event.src_path).resolve())
            src_path = str(Path(event.src_path).resolve())
        else:
            filepath = str(Path(event.src_path).resolve())
            src_path = filepath

        if self._should_debounce(event_type, filepath):
            logger.debug(f"Debounced: {event_type} en {filepath}")
            return

        # Buscar reglas que apliquen
        matching_rules = [r for r in self.config.rules if self._matches_rule(r, filepath, event_type)]
        if not matching_rules:
            return

        # Enviar a pool de ingesta para estabilización y encolado en SQLite
        self._ingest_pool.submit(self._ingest_file_event, filepath, event_type, matching_rules, src_path)

    def _ingest_file_event(self, filepath: str, event_type: str, rules: list[RuleConfig], src_path: Optional[str] = None):
        # Si es creación, modificación o movimiento, verificar estabilidad
        if event_type in ("created", "modified", "moved"):
            ready = wait_for_file_ready(
                filepath,
                timeout=self.config.settings.stability_timeout,
                check_interval=self.config.settings.stability_check_interval
            )
            if not ready:
                logger.warning(f"Archivo no estabilizado, descartando evento: {filepath}")
                return

        for rule in rules:
            event_id = uuid.uuid4().hex
            logger.info(f"Regla activada '{rule.name}' para evento {event_type} en {filepath} (ID: {event_id[:8]})")
            for idx, action_cfg in enumerate(rule.actions):
                self.db.enqueue_task(
                    event_id=event_id,
                    rule_name=rule.name,
                    event_type=event_type,
                    source_path=filepath,
                    action_index=idx,
                    action_type=action_cfg.type,
                    action_payload=action_cfg.model_dump(by_alias=True),
                    max_retries=rule.max_retries,
                    src_path=src_path
                )
            self.task_trigger.set()

    def on_created(self, event):
        self._handle_event(event, "created")

    def on_modified(self, event):
        self._handle_event(event, "modified")

    def on_deleted(self, event):
        self._handle_event(event, "deleted")

    def on_moved(self, event):
        self._handle_event(event, "moved")

    def shutdown(self):
        self._ingest_pool.shutdown(wait=False)


class AutomatorEngine:
    """Motor orquestador que coordina Watchdog, SQLite y los Workers."""

    def __init__(self, config: AutomatorConfig):
        self.config = config
        self.db = TaskDatabase(config.settings.db_path)
        self.task_trigger = threading.Event()
        self.handler = FileAutomatorHandler(self.config, self.db, self.task_trigger)
        self.observer = Observer()
        self._running = False
        self._workers: list[threading.Thread] = []

    def _worker_loop(self, worker_id: int):
        logger.debug(f"Worker #{worker_id} iniciado.")
        while self._running:
            # Esperar señal de nueva tarea o timeout
            self.task_trigger.wait(timeout=0.5)

            # Reclamar siguiente tarea ejecutable
            task = self.db.claim_next_task()
            if not task:
                self.task_trigger.clear()
                continue

            # Si encontramos una tarea, mantenemos la señal activa por si hay más
            self.task_trigger.set()

            logger.info(
                f"[Worker #{worker_id}] Ejecutando acción #{task.action_index} ({task.action_type}) "
                f"de regla '{task.rule_name}' para {task.source_path}"
            )

            # Iniciar heartbeat en segundo plano para renovar el lease durante ejecuciones prolongadas
            stop_hb = threading.Event()
            lease_timeout = self.config.settings.task_lease_timeout_seconds
            hb_interval = max(1.0, min(15.0, lease_timeout / 3.0))

            def _heartbeat_worker():
                while not stop_hb.wait(timeout=hb_interval):
                    try:
                        alive = self.db.heartbeat_task(task.id)
                        if not alive:
                            break
                        logger.debug(f"[Worker #{worker_id}] Heartbeat renovado para tarea #{task.id}")
                    except Exception as hb_err:
                        logger.warning(f"[Worker #{worker_id}] Error en heartbeat tarea #{task.id}: {hb_err}")

            hb_thread = threading.Thread(
                target=_heartbeat_worker,
                daemon=True,
                name=f"HB-Task-{task.id}"
            )
            hb_thread.start()

            try:
                action_cfg = ActionConfig.model_validate(task.action_payload)
                action = create_action(action_cfg, settings=self.config.settings)
                context = build_context(
                    task.source_path,
                    event_type=task.event_type,
                    event_id=task.event_id,
                    action_index=task.action_index,
                    src_path=task.src_path
                )
                new_context = action.execute(context)

                # Si la acción modificó la ruta (ej. local_move), propagar a tareas posteriores del mismo evento
                if new_context.get("filepath") and new_context["filepath"] != task.source_path:
                    self.db.update_downstream_path(task.event_id, new_context["filepath"])

                self.db.complete_task(task.id)
                logger.info(f"[Worker #{worker_id}] Acción #{task.action_index} completada con éxito (Tarea #{task.id})")

            except Exception as e:
                logger.error(f"[Worker #{worker_id}] Error en Tarea #{task.id}: {e}")
                will_retry = self.db.fail_or_retry_task(task.id, str(e))
                if will_retry:
                    logger.warning(f"Tarea #{task.id} reenviada a reintento (intento {task.retries + 1}/{task.max_retries})")
                else:
                    logger.error(f"Tarea #{task.id} falló definitivamente tras superar reintentos máximos.")
            finally:
                stop_hb.set()
                hb_thread.join(timeout=1.0)

        logger.debug(f"Worker #{worker_id} finalizado.")

    def start(self):
        # 1. Recuperar tareas que hayan quedado colgadas por caída previa (respetando lease timeout)
        recovered = self.db.recover_stuck_tasks(
            lease_timeout_seconds=self.config.settings.task_lease_timeout_seconds
        )
        if recovered > 0:
            logger.info(f"Se recuperaron {recovered} tareas huérfanas de ejecuciones anteriores.")
            self.task_trigger.set()

        # 2. Configurar rutas de monitoreo en Watchdog
        for watch in self.config.watches:
            watch_path = Path(watch.path).resolve()
            watch_path.mkdir(parents=True, exist_ok=True)
            self.observer.schedule(self.handler, path=str(watch_path), recursive=watch.recursive)
            logger.info(f"Monitorizando ruta: {watch_path} (recursivo: {watch.recursive})")

        # 3. Iniciar hilos de trabajo
        self._running = True
        for i in range(self.config.settings.max_workers):
            t = threading.Thread(target=self._worker_loop, args=(i + 1,), daemon=True, name=f"Worker-{i+1}")
            t.start()
            self._workers.append(t)

        # 4. Iniciar Observer
        self.observer.start()
        logger.info(f"Motor de automatización iniciado con {self.config.settings.max_workers} trabajadores.")

    def stop(self):
        logger.info("Deteniendo motor de automatización...")
        self._running = False
        self.task_trigger.set()

        self.observer.stop()
        self.observer.join(timeout=3.0)

        self.handler.shutdown()

        for t in self._workers:
            t.join(timeout=2.0)

        self.db.close()
        logger.info("Motor detenido completamente.")
