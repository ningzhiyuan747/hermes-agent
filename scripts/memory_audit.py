#!/usr/bin/env python3
"""Inspect and manage Hermes scoped memory entries.

Usage examples:
  python3 scripts/memory_audit.py list-scopes
  python3 scripts/memory_audit.py show --platform feishu --user-id 123
  python3 scripts/memory_audit.py show --scope feishu_cf6b6b2ada38bc84
  python3 scripts/memory_audit.py remove --scope feishu_cf6b6b2ada38bc84 --target memory --match "old text"
  python3 scripts/memory_audit.py replace --scope feishu_cf6b6b2ada38bc84 --target user --match "nickname" --content "User prefers concise replies."
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_constants import get_hermes_home
from tools.memory_tool import ENTRY_DELIMITER, MemoryStore, get_memory_dir


def _scope_slug(platform: str, user_id: str) -> str:
    safe_platform = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "-" for ch in platform.lower()).strip("-") or "gateway"
    user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
    return f"{safe_platform}_{user_hash}"


def _resolve_scope(args: argparse.Namespace) -> tuple[Optional[str], Optional[str], Path]:
    if args.scope:
        memory_dir = get_hermes_home() / "memories" / "scoped" / args.scope
        return None, None, memory_dir

    platform = (args.platform or "").strip()
    user_id = (args.user_id or "").strip()
    if not platform or not user_id:
        raise SystemExit("Need either --scope or both --platform and --user-id.")
    return platform, user_id, get_memory_dir(platform=platform, user_id=user_id)


def _read_entries(path: Path) -> list[str]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    return [entry.strip() for entry in raw.split(ENTRY_DELIMITER) if entry.strip()]


def cmd_list_scopes(_: argparse.Namespace) -> int:
    scoped_root = get_hermes_home() / "memories" / "scoped"
    rows = []
    if scoped_root.exists():
        for folder in sorted(p for p in scoped_root.iterdir() if p.is_dir()):
            memory_entries = _read_entries(folder / "MEMORY.md")
            user_entries = _read_entries(folder / "USER.md")
            rows.append(
                {
                    "scope": folder.name,
                    "memory_entries": len(memory_entries),
                    "user_entries": len(user_entries),
                    "updated_at": max(
                        ((folder / "MEMORY.md").stat().st_mtime if (folder / "MEMORY.md").exists() else 0),
                        ((folder / "USER.md").stat().st_mtime if (folder / "USER.md").exists() else 0),
                    ),
                }
            )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    _, _, memory_dir = _resolve_scope(args)
    payload = {
        "scope": memory_dir.name,
        "path": str(memory_dir),
        "memory": _read_entries(memory_dir / "MEMORY.md"),
        "user": _read_entries(memory_dir / "USER.md"),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _store_for_scope(args: argparse.Namespace) -> MemoryStore:
    platform, user_id, memory_dir = _resolve_scope(args)
    if platform and user_id:
        store = MemoryStore(platform=platform, user_id=user_id)
    else:
        store = MemoryStore()
        store.memory_dir = memory_dir
    store.load_from_disk()
    return store


def cmd_remove(args: argparse.Namespace) -> int:
    store = _store_for_scope(args)
    result = store.remove(args.target, args.match)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("success") else 1


def cmd_replace(args: argparse.Namespace) -> int:
    store = _store_for_scope(args)
    result = store.replace(args.target, args.match, args.content)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("success") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and manage Hermes scoped memory.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_scopes = subparsers.add_parser("list-scopes", help="List existing scoped memory buckets.")
    list_scopes.set_defaults(func=cmd_list_scopes)

    for name, func in (("show", cmd_show), ("remove", cmd_remove), ("replace", cmd_replace)):
        sub = subparsers.add_parser(name)
        sub.add_argument("--scope", help="Scoped memory folder name, e.g. feishu_cf6b6b2ada38bc84")
        sub.add_argument("--platform", help="Gateway platform name, e.g. feishu or dingtalk")
        sub.add_argument("--user-id", help="Original platform user id")
        if name in {"remove", "replace"}:
            sub.add_argument("--target", choices=["memory", "user"], required=True)
            sub.add_argument("--match", required=True, help="Substring used to locate the entry.")
        if name == "replace":
            sub.add_argument("--content", required=True, help="Replacement entry content.")
        sub.set_defaults(func=func)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
