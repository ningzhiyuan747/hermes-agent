# Runbook

Last updated: 2026-04-25

## Purpose

This file is the operator/developer runbook for the local Hermes agent repo.

## Main Paths

### Development / direct CLI

```bash
cd /home/user/.hermes/hermes-agent
source venv/bin/activate
hermes
```

### Gateway

```bash
cd /home/user/.hermes/hermes-agent
source venv/bin/activate
hermes gateway
```

### Test / verification

```bash
cd /home/user/.hermes/hermes-agent
source venv/bin/activate
python -m pytest tests/ -q
```

## Bridge / Adapter Integration Check

For platform-owned writeback flows:

```bash
cd /home/user/.hermes/hermes-agent
source venv/bin/activate
python scripts/platform_memory_writeback.py --help
```

## Read Before Editing Boundary Logic

- `AGENTS.md`
- `HANDOFF.md`
- `docs/MEMORY_OWNERSHIP.md`

## Non-Negotiable Boundary

- Hermes owns durable user/task memory.
- Bridge adapters may carry scope keys and request writeback/recall.
- OpenClaw executes bounded work; it does not become the durable memory owner.
