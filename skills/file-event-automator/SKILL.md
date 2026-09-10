---
name: file-event-automator
description: Configure, adjust, validate, and simulate file automations through the file-event-automator CLI for non-technical users.
---

# File Event Automator

Use this skill when a user asks an agent to install, configure, modify, verify, or troubleshoot a
file-event-automator workflow. Keep the user-facing explanation simple, but make the CLI interaction
deterministic and machine-readable.

## Operating rules

- Inspect the current project and existing `rules.yaml` before changing it.
- Prefer `uv sync` and `uv run file-automator ...`; fall back to the documented virtualenv flow only
  when `uv` is unavailable.
- Use `--json` for every command whose output will be consumed by the agent.
- Put complex action definitions in an `actions.json` file and pass `--actions-file`; avoid fragile
  inline shell quoting.
- Before applying a change, run `add-rule --dry-run --json` or `remove-rule --dry-run --json` and
  inspect the proposed result.
- After applying a change, run `validate --json` and `test-event --json` with a representative file
  name and event. Do not start the daemon until validation succeeds.
- Treat `security_warnings` as actionable. For workflows with local file operations or webhooks,
  recommend `strict_mode: true`, explicit `allowed_roots`, and explicit `allowed_webhook_domains`.
- Do not enable `allow_shell_commands` or `allow_private_networks` unless the user explicitly asks
  for that behavior and understands the security impact.
- Never claim that `test-event` executed an action: it only previews matching rules and interpolated
  values.
- Preserve existing rules unless the user explicitly requests replacement or deletion. A rule name
  is an upsert key for `add-rule`.

## Standard workflow

1. Verify installation with `uv run file-automator --help`.
2. Initialize with `init` only when the config does not exist.
3. Inspect with `list-rules --json` and `validate --json`.
4. Build or update actions in a temporary/user-approved `actions.json`.
5. Preview with `add-rule --dry-run --json`.
6. Apply with `add-rule --actions-file ... --json`.
7. Validate and preview a representative event with `test-event --json`.
8. Start `run -c rules.yaml` only after the user asks to activate the workflow.

Read [references/cli-workflow.md](references/cli-workflow.md) for the command contract and examples.
