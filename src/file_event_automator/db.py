from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class TaskRecord:
    id: int
    event_id: str
    rule_name: str
    event_type: str
    source_path: str
    action_index: int
    action_type: str
    action_payload: Dict[str, Any]
    status: str
    retries: int
    max_retries: int
    error_message: Optional[str]
    created_at: str
    updated_at: str
    lease_id: Optional[str] = None
    src_path: Optional[str] = None


class TaskDatabase:
    """Gestor de persistencia transaccional en SQLite para la cola de tareas y auditoría."""

    def __init__(self, db_path: str | Path = "automator.db"):
        self.db_path = str(db_path)
        self._local = threading.local()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            # Cada hilo obtiene su propia conexión; check_same_thread=False permite
            # cerrarlas de forma centralizada durante el apagado ordenado.
            conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            self._local.conn = conn
            with self._connections_lock:
                self._connections.add(conn)
        return self._local.conn

    def init_db(self) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    rule_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    src_path TEXT,
                    action_index INTEGER NOT NULL,
                    action_type TEXT NOT NULL,
                    action_payload TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PENDING', 'PROCESSING', 'SUCCESS', 'FAILED')),
                    retries INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    lease_id TEXT
                );
            """)
            # Migración ligera: agregar columna src_path si no existe en tablas preexistentes
            try:
                conn.execute("ALTER TABLE task_queue ADD COLUMN src_path TEXT;")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE task_queue ADD COLUMN lease_id TEXT;")
            except sqlite3.OperationalError:
                pass

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON task_queue(status, id);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_event ON task_queue(event_id, action_index);
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_catalog (
                    hash_algorithm TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    first_path TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (hash_algorithm, file_hash)
                );
            """)

    def enqueue_task(
        self,
        event_id: str,
        rule_name: str,
        event_type: str,
        source_path: str,
        action_index: int,
        action_type: str,
        action_payload: Dict[str, Any],
        max_retries: int = 3,
        src_path: Optional[str] = None
    ) -> int:
        conn = self._get_connection()
        payload_str = json.dumps(action_payload, ensure_ascii=False)
        resolved_src = src_path or source_path
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO task_queue (
                    event_id, rule_name, event_type, source_path, src_path,
                    action_index, action_type, action_payload, status, max_retries,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (event_id, rule_name, event_type, source_path, resolved_src, action_index, action_type, payload_str, max_retries)
            )
            return cursor.lastrowid

    def enqueue_tasks(self, tasks: List[Dict[str, Any]]) -> List[int]:
        """Inserta todas las acciones de un evento en una única transacción."""
        conn = self._get_connection()
        ids: List[int] = []
        with conn:
            for task in tasks:
                payload_str = json.dumps(task["action_payload"], ensure_ascii=False)
                cursor = conn.execute(
                    """
                    INSERT INTO task_queue (
                        event_id, rule_name, event_type, source_path, src_path,
                        action_index, action_type, action_payload, status, max_retries,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        task["event_id"], task["rule_name"], task["event_type"],
                        task["source_path"], task.get("src_path") or task["source_path"],
                        task["action_index"], task["action_type"], payload_str,
                        task.get("max_retries", 3),
                    ),
                )
                ids.append(cursor.lastrowid)
        return ids

    def register_file_hash(self, file_hash: str, hash_algorithm: str, file_size: int, path: str) -> tuple[bool, Optional[str]]:
        """Registra un hash de contenido y devuelve si ya existía y su primera ruta."""
        conn = self._get_connection()
        with conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO file_catalog (hash_algorithm, file_hash, file_size, first_path) VALUES (?, ?, ?, ?)",
                (hash_algorithm, file_hash, file_size, path),
            )
            if cursor.rowcount == 1:
                return False, None
            row = conn.execute(
                "SELECT first_path FROM file_catalog WHERE hash_algorithm = ? AND file_hash = ?",
                (hash_algorithm, file_hash),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE file_catalog SET last_seen_at = CURRENT_TIMESTAMP WHERE hash_algorithm = ? AND file_hash = ?",
                    (hash_algorithm, file_hash),
                )
                return True, row["first_path"]
            raise RuntimeError("No se pudo registrar ni recuperar el hash del archivo.")

    def claim_next_task(self) -> Optional[TaskRecord]:
        """
        Reclama la siguiente tarea ejecutable:
        - Si action_index == 0: inmediatamente ejecutable.
        - Si action_index > 0: solo ejecutable si la acción anterior (index - 1) está en SUCCESS.
        - Cancela automáticamente tareas downstream de eventos que fallaron.
        """
        conn = self._get_connection()
        with conn:
            # 1. Cancelar tareas pendientes de eventos que hayan fallado
            conn.execute(
                """
                UPDATE task_queue
                SET status = 'FAILED',
                    error_message = 'Cancelado: una acción previa en la cadena de este evento falló',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'PENDING'
                  AND event_id IN (
                      SELECT DISTINCT event_id FROM task_queue WHERE status = 'FAILED'
                  )
                """
            )

            # 2. Buscar la siguiente tarea ejecutable y reclamarla atómicamente
            while True:
                cursor = conn.execute(
                    """
                    SELECT t.* FROM task_queue t
                    WHERE t.status = 'PENDING'
                      AND (
                          t.action_index = 0
                          OR (
                              SELECT prev.status FROM task_queue prev
                              WHERE prev.event_id = t.event_id AND prev.action_index = t.action_index - 1
                          ) = 'SUCCESS'
                      )
                    ORDER BY t.id ASC
                    LIMIT 1
                    """
                )
                row = cursor.fetchone()
                if not row:
                    return None

                task_id = row["id"]
                lease_id = uuid.uuid4().hex
                update_cursor = conn.execute(
                    """
                    UPDATE task_queue 
                    SET status = 'PROCESSING', updated_at = CURRENT_TIMESTAMP, lease_id = ?
                    WHERE id = ? AND status = 'PENDING'
                    """,
                    (lease_id, task_id)
                )
                if update_cursor.rowcount > 0:
                    break

            payload = json.loads(row["action_payload"])
            src_val = row["src_path"] if "src_path" in row.keys() and row["src_path"] else row["source_path"]
            return TaskRecord(
                id=row["id"],
                event_id=row["event_id"],
                rule_name=row["rule_name"],
                event_type=row["event_type"],
                source_path=row["source_path"],
                action_index=row["action_index"],
                action_type=row["action_type"],
                action_payload=payload,
                status="PROCESSING",
                retries=row["retries"],
                max_retries=row["max_retries"],
                error_message=row["error_message"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                src_path=src_val,
                lease_id=lease_id
            )

    def heartbeat_task(self, task_id: int, lease_id: Optional[str] = None) -> bool:
        """Actualiza updated_at de una tarea en PROCESSING para renovar su lease y evitar recuperación indebida."""
        conn = self._get_connection()
        with conn:
            cursor = conn.execute(
                """
                UPDATE task_queue
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'PROCESSING'
                  AND (? IS NULL OR lease_id = ?)
                """,
                (task_id, lease_id, lease_id)
            )
            return cursor.rowcount > 0

    def update_downstream_path(self, event_id: str, new_path: str) -> None:
        """Actualiza la ruta del archivo para las acciones pendientes del mismo evento (ej: tras moverlo)."""
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                UPDATE task_queue
                SET source_path = ?, updated_at = CURRENT_TIMESTAMP
                WHERE event_id = ? AND status = 'PENDING'
                """,
                (new_path, event_id)
            )

    def complete_task(self, task_id: int, lease_id: Optional[str] = None) -> bool:
        conn = self._get_connection()
        with conn:
            cursor = conn.execute(
                """
                UPDATE task_queue 
                SET status = 'SUCCESS', error_message = NULL, updated_at = CURRENT_TIMESTAMP, lease_id = NULL
                WHERE id = ? AND status = 'PROCESSING'
                  AND (? IS NULL OR lease_id = ?)
                """,
                (task_id, lease_id, lease_id)
            )
            return cursor.rowcount > 0

    def fail_or_retry_task(self, task_id: int, error: str, lease_id: Optional[str] = None) -> bool:
        """
        Incrementa los reintentos. Si supera max_retries, pasa a FAILED.
        Devuelve True si volverá a reintentarse (PENDING), False si quedó en FAILED.
        """
        conn = self._get_connection()
        with conn:
            cursor = conn.execute(
                """SELECT retries, max_retries, event_id, lease_id FROM task_queue WHERE id = ? AND status = 'PROCESSING' AND (? IS NULL OR lease_id = ?)""", (task_id, lease_id, lease_id)
            )
            row = cursor.fetchone()
            if not row:
                return False

            retries = row["retries"] + 1
            max_retries = row["max_retries"]
            event_id = row["event_id"]

            if retries <= max_retries:
                status = "PENDING"
                will_retry = True
            else:
                status = "FAILED"
                will_retry = False

            conn.execute(
                """
                UPDATE task_queue 
                SET status = ?, retries = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP, lease_id = NULL
                WHERE id = ?
                """,
                (status, retries, str(error), task_id)
            )

            # Si falló definitivamente, cancelar downstream
            if not will_retry:
                conn.execute(
                    """
                    UPDATE task_queue
                    SET status = 'FAILED',
                        error_message = 'Cancelado: acción precedente falló',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE event_id = ? AND status = 'PENDING'
                    """,
                    (event_id,)
                )

            return will_retry

    def recover_stuck_tasks(self, lease_timeout_seconds: Optional[float] = None, force: bool = False) -> int:
        """
        Recupera tareas que quedaron en PROCESSING debido a un apagón o crash.
        Si lease_timeout_seconds se especifica (> 0), solo recupera tareas cuyo último
        heartbeat o updated_at supera dicho lapso para no interferir con otros daemons concurrentes.
        Si es None o se pasa force=True, recupera todas las tareas en PROCESSING.
        """
        conn = self._get_connection()
        with conn:
            if force or lease_timeout_seconds is None or lease_timeout_seconds <= 0:
                cursor = conn.execute(
                    """
                    UPDATE task_queue 
                    SET status = 'PENDING', updated_at = CURRENT_TIMESTAMP 
                    WHERE status = 'PROCESSING'
                    """
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE task_queue 
                    SET status = 'PENDING', updated_at = CURRENT_TIMESTAMP 
                    WHERE status = 'PROCESSING'
                      AND (strftime('%s', 'now') - strftime('%s', updated_at)) > ?
                    """,
                    (int(lease_timeout_seconds),)
                )
            return cursor.rowcount

    def retry_failed_tasks(self, event_id: Optional[str] = None) -> int:
        """Devuelve tareas FAILED al estado PENDING para reintento manual (global o por event_id)."""
        conn = self._get_connection()
        with conn:
            if event_id:
                cursor = conn.execute(
                    """
                    UPDATE task_queue 
                    SET status = 'PENDING', retries = 0, error_message = NULL, updated_at = CURRENT_TIMESTAMP 
                    WHERE status = 'FAILED' AND event_id = ?
                    """,
                    (event_id,)
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE task_queue 
                    SET status = 'PENDING', retries = 0, error_message = NULL, updated_at = CURRENT_TIMESTAMP 
                    WHERE status = 'FAILED'
                    """
                )
            return cursor.rowcount

    def get_stats(self) -> Dict[str, int]:
        """Devuelve recuento de tareas por estado."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT status, COUNT(*) as count 
            FROM task_queue 
            GROUP BY status
            """
        )
        stats = {"PENDING": 0, "PROCESSING": 0, "SUCCESS": 0, "FAILED": 0}
        for row in cursor.fetchall():
            stats[row["status"]] = row["count"]
        return stats

    def get_task(self, task_id: int) -> Optional[TaskRecord]:
        """Obtiene una tarea por su ID."""
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM task_queue WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        payload = json.loads(row["action_payload"])
        src_val = row["src_path"] if "src_path" in row.keys() and row["src_path"] else row["source_path"]
        return TaskRecord(
            id=row["id"],
            event_id=row["event_id"],
            rule_name=row["rule_name"],
            event_type=row["event_type"],
            source_path=row["source_path"],
            action_index=row["action_index"],
            action_type=row["action_type"],
            action_payload=payload,
            status=row["status"],
            retries=row["retries"],
            max_retries=row["max_retries"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            src_path=src_val
        )

    def get_recent_tasks(self, limit: int = 10, status: Optional[str] = None) -> List[TaskRecord]:
        """Devuelve las tareas más recientes."""
        conn = self._get_connection()
        query = "SELECT * FROM task_queue"
        params = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, tuple(params))
        tasks = []
        for row in cursor.fetchall():
            payload = json.loads(row["action_payload"])
            src_val = row["src_path"] if "src_path" in row.keys() and row["src_path"] else row["source_path"]
            tasks.append(TaskRecord(
                id=row["id"],
                event_id=row["event_id"],
                rule_name=row["rule_name"],
                event_type=row["event_type"],
                source_path=row["source_path"],
                action_index=row["action_index"],
                action_type=row["action_type"],
                action_payload=payload,
                status=row["status"],
                retries=row["retries"],
                max_retries=row["max_retries"],
                error_message=row["error_message"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                src_path=src_val
            ))
        return tasks

    def close(self) -> None:
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local.conn = None

