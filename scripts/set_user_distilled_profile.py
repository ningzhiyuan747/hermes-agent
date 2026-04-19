#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.business_db import set_user_distilled_profile_overrides  # noqa: E402


def _parse_overrides(items: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in items:
        key, sep, value = str(item or "").partition("=")
        field = key.strip()
        if not sep or not field:
            raise SystemExit(f"Invalid --set value: {item!r}. Expected field=value.")
        overrides[field] = value.strip()
    return overrides


def main() -> int:
    parser = argparse.ArgumentParser(description="Set manual overrides for a distilled Hermes user profile.")
    parser.add_argument("--platform", default="dingtalk")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--set", dest="sets", action="append", default=[], help="Override a distilled field, e.g. preferred_output=先结论后清单")
    parser.add_argument("--lock", dest="locks", action="append", default=[], help="Lock a distilled field from future auto-overwrite")
    args = parser.parse_args()

    record = set_user_distilled_profile_overrides(
        platform=args.platform,
        user_id=args.user_id,
        overrides=_parse_overrides(args.sets),
        locked_fields=args.locks,
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
