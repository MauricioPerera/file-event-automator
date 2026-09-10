# Agent CLI workflow

## Install

From the repository root:

```bash
uv sync
uv run file-automator --help
```

If `uv` is unavailable:

```bash
python -m venv .venv
.venv/bin/pip install -e .
```

Use `.venv\\Scripts\\file-automator` on Windows.

## Configure safely

Create an action file instead of embedding a large JSON string in a shell command:

```json
[
  {
    "type": "command",
    "args": ["python", "scripts/process.py", "{filepath}"]
  },
  {
    "type": "local_move",
    "destination": "./processed/{filename}"
  }
]
```

Preview and apply an upsert by rule name:

```bash
uv run file-automator add-rule -c rules.yaml \
  --name "Procesar CSV" --events created --patterns "*.csv" \
  --actions-file actions.json --dry-run --json

uv run file-automator add-rule -c rules.yaml \
  --name "Procesar CSV" --events created --patterns "*.csv" \
  --actions-file actions.json --json
```

The dry-run response must have `status: "planned"` and `applied: false`. Do not treat that as an
applied change.

## Verify

```bash
uv run file-automator validate -c rules.yaml --json
uv run file-automator test-event -c rules.yaml \
  --file "sample.csv" --event created --json
```

Check `status`, `valid`, `security_warnings`, `matches_count`, and `action_previews`. A successful
dry-run does not contact webhooks, execute commands, or move files.

## Deduplicate files

Use the native `deduplicate` action when the workflow should identify equal file contents, regardless
of filename. SHA-256 is the default. The action must be the last action in its rule:

```yaml
actions:
  - type: deduplicate
    hash_algorithm: sha256
    on_duplicate: move
    duplicate_destination: ./duplicates/{filename}
```

The first occurrence is recorded in the SQLite file catalog. Later occurrences can be ignored, moved,
or deleted. During verification, explain that hashing reads the complete file and that `test-event`
only previews the rule; it does not calculate or register a hash.

## Control and troubleshooting

```bash
uv run file-automator status --db automator.db --json
uv run file-automator inspect-task 42 --db automator.db --json
uv run file-automator retry-failed --db automator.db --event-id EVENT_ID --json
```

Stable error codes currently include `ADD_RULE_FAILED` and `RULE_NOT_FOUND`; always preserve the
full JSON error when reporting a failure to the user.

## Security defaults

For production-like workflows, configure:

```yaml
settings:
  strict_mode: true
  allowed_roots: ["./inbox", "./processed"]
  allowed_webhook_domains: ["example.com"]
  allow_shell_commands: false
  allow_private_networks: false
  allow_symlinks: false
```

Use `args` rather than shell command strings. Ask for explicit confirmation before enabling shell
commands, private networks, destructive directory operations, or external webhooks.
