#!/usr/bin/env python3
"""
Memory Tool Module - Persistent Curated Memory

Provides bounded, file-backed memory that persists across sessions. Two stores:
  - MEMORY.md: agent's personal notes and observations (environment facts, project
    conventions, tool quirks, things learned)
  - USER.md: what the agent knows about the user (preferences, communication style,
    expectations, workflow habits)

Both are injected into the system prompt as a frozen snapshot at session start.
Mid-session writes update files on disk immediately (durable) but do NOT change
the system prompt -- this preserves the prefix cache for the entire session.
The snapshot refreshes on the next session start.

Entry delimiter: § (section sign). Entries can be multiline.
Character limits (not tokens) because char counts are model-independent.

Design:
- Single `memory` tool with action parameter: add, replace, remove, read
- replace/remove use short unique substring matching (not full text or IDs)
- Behavioral guidance lives in the tool schema description
- Frozen snapshot pattern: system prompt is stable, tool responses show live state
"""

import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Dict, Any, List, Optional

# fcntl is Unix-only; on Windows use msvcrt for file locking
msvcrt = None
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        pass

logger = logging.getLogger(__name__)

# Where memory files live — resolved dynamically so profile overrides
# (HERMES_HOME env var changes) are always respected.  The old module-level
# constant was cached at import time and could go stale if a profile switch
# happened after the first import.
def get_memory_dir() -> Path:
    """Return the profile-scoped memories directory."""
    return get_hermes_home() / "memories"

ENTRY_DELIMITER = "\n§\n"

_PRIVATE_CHAT_TYPES = {
    "dm",
    "direct",
    "direct_message",
    "private",
    "im",
    "1:1",
    "1",
    "p2p",
    "single",
    "singlechat",
}


# ---------------------------------------------------------------------------
# Memory content scanning — lightweight check for injection/exfiltration
# in content that gets injected into the system prompt.
# ---------------------------------------------------------------------------

_MEMORY_THREAT_PATTERNS = [
    # Prompt injection
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'you\s+are\s+now\s+', "role_hijack"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'act\s+as\s+(if|though)\s+you\s+(have\s+no|don\'t\s+have)\s+(restrictions|limits|rules)', "bypass_restrictions"),
    # Exfiltration via curl/wget with secrets
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl"),
    (r'wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_wget"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)', "read_secrets"),
    # Persistence via shell rc
    (r'authorized_keys', "ssh_backdoor"),
    (r'\$HOME/\.ssh|\~/\.ssh', "ssh_access"),
    (r'\$HOME/\.hermes/\.env|\~/\.hermes/\.env', "hermes_env"),
]

_SYSTEM_MEMORY_DISALLOWED_PATTERNS = [
    (r"\b(next step|blocker|todo|pending approval|current focus|task-\w+)\b", "task_state"),
    (r"(下一步|阻塞|待办|审批中|当前任务|任务进展)", "task_state_cn"),
    (r"\b(user|customer|client)\b[^\n]{0,30}\b(prefers|likes|dislikes|hates|wants)\b", "person_preference"),
    (r"(用户|客户)[^\n]{0,20}(偏好|喜欢|讨厌|习惯)", "person_preference_cn"),
]

# Subset of invisible chars for injection detection
_INVISIBLE_CHARS = {
    '\u200b', '\u200c', '\u200d', '\u2060', '\ufeff',
    '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',
}


def _scan_memory_content(content: str) -> Optional[str]:
    """Scan memory content for injection/exfil patterns. Returns error string if blocked."""
    # Check invisible unicode
    for char in _INVISIBLE_CHARS:
        if char in content:
            return f"Blocked: content contains invisible unicode character U+{ord(char):04X} (possible injection)."

    # Check threat patterns
    for pattern, pid in _MEMORY_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return f"Blocked: content matches threat pattern '{pid}'. Memory entries are injected into the system prompt and must not contain injection or exfiltration payloads."

    return None


def _scan_system_memory_content(content: str) -> Optional[str]:
    """Reject obvious person/task content for system memory."""
    for pattern, pid in _SYSTEM_MEMORY_DISALLOWED_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return (
                f"Blocked: content matches system-memory exclusion '{pid}'. "
                "System memory is only for shared repo/workspace/tool facts and stable workflows."
            )
    return None


def _get_session_env(name: str, default: str = "") -> str:
    try:
        from gateway.session_context import get_session_env
        return get_session_env(name, default) or default
    except Exception:
        return os.getenv(name, default)


def _get_bound_task_id(platform: str, chat_id: str, thread_id: str = "") -> str:
    if not platform or not chat_id:
        return ""
    try:
        from agent.business_db import get_channel_task

        binding = get_channel_task(platform=platform, chat_id=chat_id, thread_id=thread_id)
        task = (binding or {}).get("task") or {}
        return str(task.get("task_id") or "").strip()
    except Exception:
        return ""


def _build_scoped_memory_summary(entries: List[str]) -> str:
    if not entries:
        return ""
    if len(entries) == 1:
        return entries[0][:400]
    latest = entries[-1][:220]
    return f"{len(entries)} 条记忆；最新：{latest}"


def _normalize_memory_target(target: str) -> str:
    normalized = str(target or "memory").strip().lower() or "memory"
    if normalized in {"memory", "system"}:
        return normalized
    if normalized == "user":
        return normalized
    return normalized


def _storage_target(target: str) -> str:
    normalized = _normalize_memory_target(target)
    return "memory" if normalized == "system" else normalized


def _char_limit_for_target(target: str, store: Optional["MemoryStore"]) -> int:
    normalized = _storage_target(target)
    if normalized == "user":
        return int(getattr(store, "user_char_limit", 1375) or 1375)
    return int(getattr(store, "memory_char_limit", 2200) or 2200)


def _resolve_scoped_memory_target(target: str) -> Dict[str, str]:
    normalized_target = _normalize_memory_target(target)
    if normalized_target == "system":
        return {}
    platform = str(_get_session_env("HERMES_SESSION_PLATFORM", "") or "").strip().lower()
    if not platform:
        return {}

    chat_type = str(_get_session_env("HERMES_SESSION_CHAT_TYPE", "") or "").strip().lower()
    user_id = str(_get_session_env("HERMES_SESSION_USER_ID", "") or "").strip()
    chat_id = str(_get_session_env("HERMES_SESSION_CHAT_ID", "") or "").strip()
    thread_id = str(_get_session_env("HERMES_SESSION_THREAD_ID", "") or "").strip()
    is_private = chat_type in _PRIVATE_CHAT_TYPES
    task_id = "" if is_private else _get_bound_task_id(platform, chat_id, thread_id)
    if is_private and user_id:
        return {
            "kind": "user",
            "platform": platform,
            "user_id": user_id,
            "scope": "profile" if normalized_target == "user" else "notes",
            "chat_type": chat_type,
            "chat_id": chat_id,
            "thread_id": thread_id,
        }
    if task_id and normalized_target == "memory":
        return {
            "kind": "task",
            "task_id": task_id,
            "platform": platform,
            "chat_type": chat_type,
            "chat_id": chat_id,
            "thread_id": thread_id,
        }
    return {
        "kind": "blocked",
        "platform": platform,
        "chat_type": chat_type,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "user_id": user_id,
        "task_id": task_id,
    }


def _load_scoped_entries(target_info: Dict[str, str]) -> List[str]:
    try:
        from agent.business_db import get_task_memory, get_user_memory

        if target_info.get("kind") == "user":
            record = get_user_memory(
                platform=target_info.get("platform", ""),
                user_id=target_info.get("user_id", ""),
                scope=target_info.get("scope", "profile"),
            )
        elif target_info.get("kind") == "task":
            record = get_task_memory(
                task_id=target_info.get("task_id", ""),
                scope="shared",
            )
        else:
            return []
    except Exception:
        return []
    memory = (record or {}).get("memory") or {}
    entries = memory.get("entries") if isinstance(memory, dict) else None
    return [str(item).strip() for item in (entries or []) if str(item).strip()]


def _save_scoped_entries(target: str, target_info: Dict[str, str], entries: List[str]) -> None:
    payload = {
        "target": target,
        "entries": entries,
        "entry_count": len(entries),
        "memory_scope": {
            "kind": target_info.get("kind", ""),
            "scope": target_info.get("scope", "shared" if target_info.get("kind") == "task" else ""),
            "task_id": target_info.get("task_id", ""),
        },
        "session": {
            "platform": target_info.get("platform", ""),
            "chat_id": target_info.get("chat_id", ""),
            "thread_id": target_info.get("thread_id", ""),
            "chat_type": target_info.get("chat_type", ""),
            "user_id": target_info.get("user_id", ""),
        },
    }
    summary = _build_scoped_memory_summary(entries)
    from agent.business_db import upsert_task_memory, upsert_user_memory

    if target_info.get("kind") == "user":
        upsert_user_memory(
            platform=target_info.get("platform", ""),
            user_id=target_info.get("user_id", ""),
            scope=target_info.get("scope", "profile"),
            summary=summary,
            memory=payload,
        )
        return

    if target_info.get("kind") == "task":
        upsert_task_memory(
            task_id=target_info.get("task_id", ""),
            scope="shared",
            summary=summary,
            memory=payload,
        )


def _scoped_success_response(target: str, entries: List[str], limit: int, message: str = None) -> Dict[str, Any]:
    current = len(ENTRY_DELIMITER.join(entries)) if entries else 0
    pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
    result = {
        "success": True,
        "target": target,
        "entries": entries,
        "usage": f"{pct}% — {current:,}/{limit:,} chars",
        "entry_count": len(entries),
    }
    if message:
        result["message"] = message
    return result


def _apply_scoped_memory_action(
    *,
    action: str,
    target: str,
    content: str = None,
    old_text: str = None,
    store: Optional["MemoryStore"] = None,
) -> Optional[Dict[str, Any]]:
    target_info = _resolve_scoped_memory_target(target)
    kind = target_info.get("kind")
    if not kind:
        return None

    if kind == "blocked":
        if target == "user":
            return {
                "success": False,
                "error": "User profile memory can only be written inside a private 1:1 conversation.",
            }
        return {
            "success": False,
            "error": "Shared-chat durable memory requires a bound task. Bind the current group to a task first, or save this in a private chat.",
        }

    entries = _load_scoped_entries(target_info)
    limit = _char_limit_for_target(target, store)

    if action == "add":
        content = str(content or "").strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}
        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}
        if content in entries:
            return _scoped_success_response(target, entries, limit, "Entry already exists (no duplicate added).")
        new_entries = entries + [content]
        new_total = len(ENTRY_DELIMITER.join(new_entries))
        if new_total > limit:
            current = len(ENTRY_DELIMITER.join(entries)) if entries else 0
            return {
                "success": False,
                "error": (
                    f"Memory at {current:,}/{limit:,} chars. "
                    f"Adding this entry ({len(content)} chars) would exceed the limit. "
                    f"Replace or remove existing entries first."
                ),
                "current_entries": entries,
                "usage": f"{current:,}/{limit:,}",
            }
        _save_scoped_entries(target, target_info, new_entries)
        return _scoped_success_response(target, new_entries, limit, "Entry added.")

    if action == "replace":
        old_text = str(old_text or "").strip()
        content = str(content or "").strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not content:
            return {"success": False, "error": "Content cannot be empty."}
        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}
        matches = [(i, e) for i, e in enumerate(entries) if old_text in e]
        if not matches:
            return {"success": False, "error": f"No entry matched '{old_text}'."}
        if len(matches) > 1:
            unique_texts = set(e for _, e in matches)
            if len(unique_texts) > 1:
                previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                return {
                    "success": False,
                    "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                    "matches": previews,
                }
        new_entries = list(entries)
        new_entries[matches[0][0]] = content
        new_total = len(ENTRY_DELIMITER.join(new_entries)) if new_entries else 0
        if new_total > limit:
            current = len(ENTRY_DELIMITER.join(entries)) if entries else 0
            return {
                "success": False,
                "error": (
                    f"Memory at {current:,}/{limit:,} chars. "
                    f"Replacing with this entry ({len(content)} chars) would exceed the limit."
                ),
                "current_entries": entries,
                "usage": f"{current:,}/{limit:,}",
            }
        _save_scoped_entries(target, target_info, new_entries)
        return _scoped_success_response(target, new_entries, limit, "Entry replaced.")

    if action == "remove":
        old_text = str(old_text or "").strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        matches = [(i, e) for i, e in enumerate(entries) if old_text in e]
        if not matches:
            return {"success": False, "error": f"No entry matched '{old_text}'."}
        if len(matches) > 1:
            unique_texts = set(e for _, e in matches)
            if len(unique_texts) > 1:
                previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                return {
                    "success": False,
                    "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                    "matches": previews,
                }
        new_entries = list(entries)
        new_entries.pop(matches[0][0])
        _save_scoped_entries(target, target_info, new_entries)
        return _scoped_success_response(target, new_entries, limit, "Entry removed.")

    return None


class MemoryStore:
    """
    Bounded curated memory with file persistence. One instance per AIAgent.

    Maintains two parallel states:
      - _system_prompt_snapshot: frozen at load time, used for system prompt injection.
        Never mutated mid-session. Keeps prefix cache stable.
      - memory_entries / user_entries: live state, mutated by tool calls, persisted to disk.
        Tool responses always reflect this live state.
    """

    def __init__(self, memory_char_limit: int = 2200, user_char_limit: int = 1375):
        self.memory_entries: List[str] = []
        self.user_entries: List[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        # Frozen snapshot for system prompt -- set once at load_from_disk()
        self._system_prompt_snapshot: Dict[str, str] = {"memory": "", "user": ""}

    def load_from_disk(self):
        """Load entries from MEMORY.md and USER.md, capture system prompt snapshot."""
        mem_dir = get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)

        self.memory_entries = self._read_file(mem_dir / "MEMORY.md")
        self.user_entries = self._read_file(mem_dir / "USER.md")

        # Deduplicate entries (preserves order, keeps first occurrence)
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))

        # Capture frozen snapshot for system prompt injection
        self._system_prompt_snapshot = {
            "memory": self._render_block("memory", self.memory_entries),
            "user": self._render_block("user", self.user_entries),
        }

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        """Acquire an exclusive file lock for read-modify-write safety.

        Uses a separate .lock file so the memory file itself can still be
        atomically replaced via os.replace().
        """
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is None and msvcrt is None:
            yield
            return

        if msvcrt and (not lock_path.exists() or lock_path.stat().st_size == 0):
            lock_path.write_text(" ", encoding="utf-8")

        fd = open(lock_path, "r+" if msvcrt else "a+")
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
            yield
        finally:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_UN)
            elif msvcrt:
                try:
                    fd.seek(0)
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                except (OSError, IOError):
                    pass
            fd.close()

    @staticmethod
    def _path_for(target: str) -> Path:
        mem_dir = get_memory_dir()
        normalized = _storage_target(target)
        if normalized == "user":
            return mem_dir / "USER.md"
        return mem_dir / "MEMORY.md"

    def _reload_target(self, target: str):
        """Re-read entries from disk into in-memory state.

        Called under file lock to get the latest state before mutating.
        """
        fresh = self._read_file(self._path_for(target))
        fresh = list(dict.fromkeys(fresh))  # deduplicate
        self._set_entries(target, fresh)

    def save_to_disk(self, target: str):
        """Persist entries to the appropriate file. Called after every mutation."""
        get_memory_dir().mkdir(parents=True, exist_ok=True)
        self._write_file(self._path_for(target), self._entries_for(target))

    def _entries_for(self, target: str) -> List[str]:
        normalized = _storage_target(target)
        if normalized == "user":
            return self.user_entries
        return self.memory_entries

    def _set_entries(self, target: str, entries: List[str]):
        normalized = _storage_target(target)
        if normalized == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def _char_count(self, target: str) -> int:
        entries = self._entries_for(target)
        if not entries:
            return 0
        return len(ENTRY_DELIMITER.join(entries))

    def _char_limit(self, target: str) -> int:
        normalized = _storage_target(target)
        if normalized == "user":
            return self.user_char_limit
        return self.memory_char_limit

    def add(self, target: str, content: str) -> Dict[str, Any]:
        """Append a new entry. Returns error if it would exceed the char limit."""
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        # Scan for injection/exfiltration before accepting
        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}
        if _normalize_memory_target(target) == "system":
            system_scan_error = _scan_system_memory_content(content)
            if system_scan_error:
                return {"success": False, "error": system_scan_error}

        with self._file_lock(self._path_for(target)):
            # Re-read from disk under lock to pick up writes from other sessions
            self._reload_target(target)

            entries = self._entries_for(target)
            limit = self._char_limit(target)

            # Reject exact duplicates
            if content in entries:
                return self._success_response(target, "Entry already exists (no duplicate added).")

            # Calculate what the new total would be
            new_entries = entries + [content]
            new_total = len(ENTRY_DELIMITER.join(new_entries))

            if new_total > limit:
                current = self._char_count(target)
                return {
                    "success": False,
                    "error": (
                        f"Memory at {current:,}/{limit:,} chars. "
                        f"Adding this entry ({len(content)} chars) would exceed the limit. "
                        f"Replace or remove existing entries first."
                    ),
                    "current_entries": entries,
                    "usage": f"{current:,}/{limit:,}",
                }

            entries.append(content)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry added.")

    def replace(self, target: str, old_text: str, new_content: str) -> Dict[str, Any]:
        """Find entry containing old_text substring, replace it with new_content."""
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {"success": False, "error": "new_content cannot be empty. Use 'remove' to delete entries."}

        # Scan replacement content for injection/exfiltration
        scan_error = _scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}
        if _normalize_memory_target(target) == "system":
            system_scan_error = _scan_system_memory_content(new_content)
            if system_scan_error:
                return {"success": False, "error": system_scan_error}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), operate on the first one
                unique_texts = set(e for _, e in matches)
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to replace just the first

            idx = matches[0][0]
            limit = self._char_limit(target)

            # Check that replacement doesn't blow the budget
            test_entries = entries.copy()
            test_entries[idx] = new_content
            new_total = len(ENTRY_DELIMITER.join(test_entries))

            if new_total > limit:
                return {
                    "success": False,
                    "error": (
                        f"Replacement would put memory at {new_total:,}/{limit:,} chars. "
                        f"Shorten the new content or remove other entries first."
                    ),
                }

            entries[idx] = new_content
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry replaced.")

    def remove(self, target: str, old_text: str) -> Dict[str, Any]:
        """Remove the entry containing old_text substring."""
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), remove the first one
                unique_texts = set(e for _, e in matches)
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to remove just the first

            idx = matches[0][0]
            entries.pop(idx)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry removed.")

    def format_for_system_prompt(self, target: str) -> Optional[str]:
        """
        Return the frozen snapshot for system prompt injection.

        This returns the state captured at load_from_disk() time, NOT the live
        state. Mid-session writes do not affect this. This keeps the system
        prompt stable across all turns, preserving the prefix cache.

        Returns None if the snapshot is empty (no entries at load time).
        """
        block = self._system_prompt_snapshot.get(target, "")
        return block if block else None

    # -- Internal helpers --

    def _success_response(self, target: str, message: str = None) -> Dict[str, Any]:
        entries = self._entries_for(target)
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        resp = {
            "success": True,
            "target": target,
            "entries": entries,
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        return resp

    def _render_block(self, target: str, entries: List[str]) -> str:
        """Render a system prompt block with header and usage indicator."""
        if not entries:
            return ""

        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        normalized = _normalize_memory_target(target)
        if normalized == "user":
            header = f"USER PROFILE (who the user is) [{pct}% — {current:,}/{limit:,} chars]"
        elif normalized == "system":
            header = f"SYSTEM MEMORY (shared durable facts) [{pct}% — {current:,}/{limit:,} chars]"
        else:
            header = f"MEMORY (your personal notes) [{pct}% — {current:,}/{limit:,} chars]"

        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    @staticmethod
    def _read_file(path: Path) -> List[str]:
        """Read a memory file and split into entries.

        No file locking needed: _write_file uses atomic rename, so readers
        always see either the previous complete file or the new complete file.
        """
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return []

        if not raw.strip():
            return []

        # Use ENTRY_DELIMITER for consistency with _write_file. Splitting by "§"
        # alone would incorrectly split entries that contain "§" in their content.
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        return [e for e in entries if e]

    @staticmethod
    def _write_file(path: Path, entries: List[str]):
        """Write entries to a memory file using atomic temp-file + rename.

        Previous implementation used open("w") + flock, but "w" truncates the
        file *before* the lock is acquired, creating a race window where
        concurrent readers see an empty file. Atomic rename avoids this:
        readers always see either the old complete file or the new one.
        """
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        try:
            # Write to temp file in same directory (same filesystem for atomic rename)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".mem_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, str(path))  # Atomic on same filesystem
            except BaseException:
                # Clean up temp file on any failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to write memory file {path}: {e}")


def memory_tool(
    action: str,
    target: str = "memory",
    content: str = None,
    old_text: str = None,
    store: Optional[MemoryStore] = None,
) -> str:
    """
    Single entry point for the memory tool. Dispatches to MemoryStore methods.

    Returns JSON string with results.
    """
    if store is None:
        return tool_error("Memory is not available. It may be disabled in config or this environment.", success=False)

    normalized_target = _normalize_memory_target(target)
    if normalized_target not in ("memory", "user", "system"):
        return tool_error(f"Invalid target '{target}'. Use 'memory', 'system', or 'user'.", success=False)

    scoped_result = _apply_scoped_memory_action(
        action=action,
        target=normalized_target,
        content=content,
        old_text=old_text,
        store=store,
    )
    if scoped_result is not None:
        return json.dumps(scoped_result, ensure_ascii=False)

    if action == "add":
        if not content:
            return tool_error("Content is required for 'add' action.", success=False)
        result = store.add(normalized_target, content)

    elif action == "replace":
        if not old_text:
            return tool_error("old_text is required for 'replace' action.", success=False)
        if not content:
            return tool_error("content is required for 'replace' action.", success=False)
        result = store.replace(normalized_target, old_text, content)

    elif action == "remove":
        if not old_text:
            return tool_error("old_text is required for 'remove' action.", success=False)
        result = store.remove(normalized_target, old_text)

    else:
        return tool_error(f"Unknown action '{action}'. Use: add, replace, remove", success=False)

    return json.dumps(result, ensure_ascii=False)


def check_memory_requirements() -> bool:
    """Memory tool has no external requirements -- always available."""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "Save durable information to persistent memory that survives across sessions. "
        "Memory is injected into future turns, so keep it compact and focused on facts "
        "that will still matter later.\n\n"
        "WHEN TO SAVE (do this proactively, don't wait to be asked):\n"
        "- User corrects you or says 'remember this' / 'don't do that again'\n"
        "- User shares a preference, habit, or personal detail (name, role, timezone, coding style)\n"
        "- You discover something about the environment (OS, installed tools, project structure)\n"
        "- You learn a convention, API quirk, or workflow specific to this user's setup\n"
        "- You identify a stable fact that will be useful again in future sessions\n\n"
        "PRIORITY: User preferences and corrections > environment facts > procedural knowledge. "
        "The most valuable memory prevents the user from having to repeat themselves.\n\n"
        "Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO "
        "state to memory; use session_search to recall those from past transcripts.\n"
        "If you've discovered a new way to do something, solved a problem that could be "
        "necessary later, save it as a skill with the skill tool.\n\n"
        "TWO TARGETS:\n"
        "- 'user': who the user is -- name, role, preferences, communication style, pet peeves\n"
        "- 'memory': session-scoped durable notes -- private chats route to user notes, bound shared chats route to task memory\n"
        "- 'system': shared durable system facts -- workspace paths, repo conventions, tool quirks, stable workflows\n\n"
        "Use 'system' for cross-platform facts that are true regardless of the current chat or user. "
        "Do not put personal preferences, user notes, or task progress into 'system'. There is no durable channel-memory bucket.\n\n"
        "ACTIONS: add (new entry), replace (update existing -- old_text identifies it), "
        "remove (delete -- old_text identifies it).\n\n"
        "SKIP: trivial/obvious info, things easily re-discovered, raw data dumps, and temporary task state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "The action to perform."
            },
            "target": {
                "type": "string",
                "enum": ["memory", "system", "user"],
                "description": "Which memory store: 'memory' for session-routed task/user notes, 'system' for shared durable system facts, 'user' for user profile."
            },
            "content": {
                "type": "string",
                "description": "The entry content. Required for 'add' and 'replace'."
            },
            "old_text": {
                "type": "string",
                "description": "Short unique substring identifying the entry to replace or remove."
            },
        },
        "required": ["action", "target"],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="memory",
    toolset="memory",
    schema=MEMORY_SCHEMA,
    handler=lambda args, **kw: memory_tool(
        action=args.get("action", ""),
        target=args.get("target", "memory"),
        content=args.get("content"),
        old_text=args.get("old_text"),
        store=kw.get("store")),
    check_fn=check_memory_requirements,
    emoji="🧠",
)
