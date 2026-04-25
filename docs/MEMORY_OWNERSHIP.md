# Memory Ownership

Last updated: 2026-04-25

Hermes is the durable memory owner in the current three-layer system.

## Ownership Matrix

| Surface | Durable owner | Bridge role | OpenClaw role |
| --- | --- | --- | --- |
| User profile memory | Hermes | carry `person_memory_key`, request writeback/recall | consume scoped context only |
| Task / conversation memory | Hermes | carry `task_scope_key`, request task recall/status | execute bounded task context only |
| Temporary execution traces | runtime-local | attach metadata to status/handoff | store local evidence only |

## Current Hermes-Owned Entry Point

- `scripts/platform_memory_writeback.py`

This script is the current adapter-facing Hermes-owned writeback surface for
private preference writeback/distillation flows.

## Rules

- Do not move durable memory ownership into platform adapters.
- Do not move durable memory ownership into OpenClaw.
- Adapter-triggered writeback should enter Hermes through stable scripts/services.
