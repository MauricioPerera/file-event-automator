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

---

## 🧪 Ejecución de Pruebas

```bash
uv run pytest -v
```

