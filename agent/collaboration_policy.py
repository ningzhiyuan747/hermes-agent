from __future__ import annotations

import os
from pathlib import Path


_DEFAULT_POLICY_BASENAME = "hermes-openclaw-collaboration-policy.md"


def _candidate_policy_paths() -> list[Path]:
    env_path = str(os.getenv("HERMES_COLLABORATION_POLICY_FILE", "") or "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path))

    repo_root = Path(__file__).resolve().parents[1]
    candidates.extend(
        [
            repo_root / "codex-prompts" / _DEFAULT_POLICY_BASENAME,
            Path("/mnt/f/hermes-dingtalk-bridge/codex-prompts") / _DEFAULT_POLICY_BASENAME,
        ]
    )
    return candidates


def resolve_collaboration_policy_path() -> Path | None:
    for candidate in _candidate_policy_paths():
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def load_collaboration_policy_text() -> str:
    path = resolve_collaboration_policy_path()
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
