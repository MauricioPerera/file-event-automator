# File Event Automator

Sistema reactivo de automatización basado en eventos del sistema de archivos (`watchdog`), con configuración declarativa en YAML, persistencia transaccional en SQLite y soporte para acciones locales, webhooks HTTP y comandos de consola.

---

## 🌟 Características Principales

1. **Configuración Declarativa (`rules.yaml`)**:
   - Define reglas, filtros de extensiones/patrones glob y pipelines de acciones sin modificar código Python.
2. **Las 3 Familias de Acciones Soportadas**:
   - **Locales**: Mover (`local_move`), copiar (`local_copy`) y eliminar (`local_delete`) archivos. Creación automática de carpetas destino.
   - **Red / Integración**: Webhooks HTTP (`POST`, `GET`, `PUT`, `PATCH`, `DELETE`) con headers y payloads JSON interpolables.
   - **Comandos de Sistema**: Ejecución de scripts o comandos (`command`) vía `subprocess` con captura de stdout/stderr y validación de código de retorno.
3. **Plantillas de Variables Dinámicas**:
   - Puedes usar variables en destinos, URLs, headers, payloads y comandos:
     - `{filepath}`: Ruta absoluta al archivo.
     - `{filename}`: Nombre del archivo con extensión (ej. `reporte.csv`).
     - `{stem}`: Nombre sin extensión (ej. `reporte`).
     - `{ext}`: Extensión (ej. `.csv`).
     - `{dir}`: Directorio contenedor.
     - `{filesize}`: Tamaño en bytes.
     - `{event_type}`: Tipo de evento (`created`, `modified`, `deleted`, `moved`).
     - `{timestamp}`: Marca de tiempo formateada (`YYYYMMDD_HHMMSS`).
     - `{iso_timestamp}`: Marca de tiempo ISO-8601.
4. **Persistencia y Resiliencia (SQLite)**:
   - Cola de tareas transaccional en modo WAL.
   - Estados: `PENDING`, `PROCESSING`, `SUCCESS`, `FAILED`.
   - Reintentos automáticos configurables (`max_retries`).
   - Recuperación automática de caídas (`recover_stuck_tasks()` al iniciar el daemon).
   - Encadenamiento secuencial: si una acción mueve el archivo, las acciones posteriores reciben la ruta actualizada. Si una acción falla definitivamente, las acciones dependientes se cancelan.
5. **Estabilizador y Debouncer**:
   - *Debouncing*: Filtra ráfagas de eventos repetidos en ventanas de tiempo configurables.
   - *File Stabilizer*: Espera a que las operaciones de copia o descarga I/O terminen y el archivo se desbloquee antes de procesarlo.

---

## 🚀 Instalación y Uso Rápido

### 1. Activar el entorno virtual

```bash
# Con uv (recomendado)
uv sync

# O con pip tradicional
python -m venv .venv
.venv\Scripts\pip install -e .
```

### 2. Validar tu archivo de reglas

```bash
uv run file-automator validate -c rules.example.yaml
```

### 3. Iniciar el servicio (Daemon)

```bash
uv run file-automator run -c rules.example.yaml
```

### 4. Consultar el estado de la cola y reintentos

```bash
# Ver conteo de tareas procesadas, pendientes o fallidas
uv run file-automator status --db automator.db

# Reintentar manualmente tareas que hayan quedado en FAILED
uv run file-automator retry-failed --db automator.db
```

---

## 📋 Ejemplo de Configuración (`rules.yaml`)

```yaml
settings:
  debounce_seconds: 1.0        # Tiempo mínimo para no duplicar eventos
  stability_timeout: 10.0      # Espera máxima para que el archivo termine de escribirse
  stability_check_interval: 0.2
  max_workers: 4               # Hilos de ejecución concurrentes
  db_path: "automator.db"      # Base de datos SQLite
  log_level: "INFO"

watches:
  - path: "./inbox"
    recursive: false

rules:
  - name: "Ingesta y Notificación de CSV"
    events: ["created", "modified"]
    patterns: ["*.csv"]
    ignore_patterns: ["*.tmp", "~*"]
    max_retries: 2
    actions:
      - type: "command"
        cmd: 'python scripts/process_csv.py "{filepath}"'
        check_returncode: true

      - type: "webhook"
        url: "https://httpbin.org/post"
        method: "POST"
        headers:
          Content-Type: "application/json"
        json:
          event: "csv_recibido"
          archivo: "{filename}"
          tamano: "{filesize}"

      - type: "local_move"
        destination: "./inbox/processed/{filename}"
        overwrite: true
```

## 🤖 Uso con Agentes de Inteligencia Artificial (AI Agents)

El CLI está optimizado para que un **usuario no técnico** pueda pedirle a un Agente de IA (Gemini, Claude, ChatGPT, etc.) que instale y configure automatizaciones sin editar archivos YAML a mano.

Todos los comandos soportan el modificador `--json` para que los agentes reciban salidas estructuradas y deterministas.

### Comandos para Agentes de IA:

```bash
# 1. Inicializar configuración en una carpeta
file-automator init -c rules.yaml --watch ./inbox --json

# 2. Listar reglas activas en formato JSON
file-automator list-rules -c rules.yaml --json

# 3. Agregar una nueva regla programáticamente
file-automator add-rule \
  -c rules.yaml \
  --name "Filtro de Facturas PDF" \
  --events created \
  --patterns "*.pdf" \
  --actions '[
    {"type": "command", "cmd": "python scripts/parse_pdf.py \"{filepath}\""},
    {"type": "local_move", "destination": "./inbox/procesados/{filename}"}
  ]' \
  --json

# 4. Simulación Dry-Run (probar si una regla coincide con un archivo sin ejecutar acciones)
file-automator test-event -c rules.yaml --file "factura_marzo.pdf" --event created --json

# 5. Eliminar una regla
file-automator remove-rule -c rules.yaml --name "Filtro de Facturas PDF" --json

# 6. Consultar estado y errores de la cola de tareas
file-automator status --db automator.db --json

# 7. Inspeccionar por qué falló una tarea específica
file-automator inspect-task 42 --db automator.db --json

# 8. Reintentar tareas fallidas
file-automator retry-failed --db automator.db --json
```

### Flujo recomendado para agentes

Un agente puede preparar una automatización sin tocar el YAML directamente:

```bash
# 1. Guardar las acciones en un archivo JSON legible
file-automator add-rule -c rules.yaml \
  --name "Procesar facturas" \
  --patterns "*.pdf" \
  --actions-file actions.json \
  --dry-run --json

# 2. Aplicar la propuesta después de verificar la respuesta
file-automator add-rule -c rules.yaml \
  --name "Procesar facturas" \
  --patterns "*.pdf" \
  --actions-file actions.json \
  --json

# 3. Validar seguridad y estructura
file-automator validate -c rules.yaml --json

# 4. Simular un archivo real sin ejecutar acciones
file-automator test-event -c rules.yaml \
  --file "factura_001.pdf" --event created --json
```

`--actions-file` evita problemas de escape del JSON en la terminal. `--dry-run` devuelve
`status: "planned"` y `applied: false`, por lo que un agente puede revisar el cambio
antes de escribirlo. Las respuestas nuevas incluyen `schema_version: 1`; los errores de
modificación incluyen un `error_code` estable.

### Skill para agentes

El repositorio incluye una skill reutilizable en [`skills/file-event-automator/`](skills/file-event-automator/).
Permite que un agente conozca el flujo recomendado de instalación, configuración, validación y
simulación sin tener que inferirlo desde el código.

Para instalarla en un entorno Codex, copia esa carpeta al directorio de skills del agente, por
ejemplo:

```powershell
Copy-Item -Recurse skills/file-event-automator "$env:CODEX_HOME/skills/"
```

Después el agente podrá usarla cuando el usuario pida configurar o ajustar automatizaciones de
archivos.

## 🛡️ Seguridad y Hardening

El sistema cuenta con protecciones avanzadas contra vectores de ataque comunes en entornos automatizados:

1. **Protección contra Inyección de Comandos**:
   - Soporte para `args` (lista de argumentos ejecutada directamente con `shell=False`).
   - El uso de `shell=True` está deshabilitado por defecto y requiere `allow_shell_commands: true` explícito en `settings`.
2. **Protección contra SSRF en Webhooks**:
   - Bloqueo automático de IPs privadas (`10.x.x.x`, `192.168.x.x`, `172.16.x.x`), loopback (`127.0.0.1`, `localhost`) y direcciones de metadatos (`169.254.169.254`).
   - Allowlist configurable con `allowed_webhook_domains: ["empresa.com", "slack.com"]`.
   - Inyección automática de cabecera `Idempotency-Key: {event_id}_{action_index}` para prevenir peticiones duplicadas.
3. **Path Jailing y Protección Anti-Destrucción**:
   - `allowed_roots`: Restringe destinos locales a rutas autorizadas (previene Directory Traversal).
   - Bloqueo de sobrescritura de directorios completos (`rmtree`) a menos que se configure `allow_dir_overwrite: true`.
   - Bloqueo de eliminación de carpetas en `local_delete` salvo que se especifique `allow_dir_deletion: true`.
4. **Coordinación Multi-Daemon**:
   - `task_lease_timeout_seconds: 300.0`: Evita condiciones de carrera entre demonios concurrentes al recuperar tareas caídas.
5. **Reintentos Granulares**:
   - Posibilidad de reintentar eventos fallidos específicos mediante `file-automator retry-failed --event-id <ID>`.

---

## 🧪 Ejecución de Pruebas

```bash
uv run pytest -v
```
Todas las 38 pruebas unitarias y de integración pasan al 100%.

