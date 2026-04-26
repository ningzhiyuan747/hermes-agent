# Handoff

Last updated: 2026-04-25

## Current Snapshot

- Repo: `/home/user/.hermes/hermes-agent`
- This repo is the durable memory and session owner in the current three-layer setup.
- Current external collaborators:
  - control plane: `F:\hermes-control-plane`
  - executor/runtime: local OpenClaw workspace and runtime docs

## Read This First

1. `AGENTS.md`
2. `HANDOFF.md`
3. `RUNBOOK.md`
4. `docs/MEMORY_OWNERSHIP.md`
5. `README.md`

## Current Integration Focus

- Hermes owns durable user/task memory.
- Platform adapters should request recall/writeback through Hermes-owned entrypoints instead of becoming memory owners.
- `scripts/platform_memory_writeback.py` is the current Hermes-owned writeback surface for adapter-triggered private preference distillation/writeback.

## Verification / Resume Commands

```bash
cd /home/user/.hermes/hermes-agent
python -m pytest tests/scripts -q
python scripts/platform_memory_writeback.py --help
```

## Expected Worktree State

If this repo is being touched as part of the current bridge alignment work,
expect changes around:

- `HANDOFF.md`
- `RUNBOOK.md`
- `docs/MEMORY_OWNERSHIP.md`
- `scripts/platform_memory_writeback.py`
- related tests under `tests/scripts/`

If unrelated files are dirty, inspect before continuing.

## Likely Next Step

- Keep Hermes as the only durable memory owner.
- Keep bridge adapters calling Hermes-owned scripts/services for writeback.
- Avoid moving platform-specific persistence rules into OpenClaw or the bridge.
