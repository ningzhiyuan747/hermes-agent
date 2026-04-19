#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.user_profile_distiller import distill_recent_users, distill_user_profile  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Distill Hermes user profiles from recent activity.")
    parser.add_argument("--platform", default="dingtalk")
    parser.add_argument("--user-id", default="")
    parser.add_argument("--since-days", type=int, default=30)
    parser.add_argument("--user-limit", type=int, default=20)
    parser.add_argument("--activity-limit", type=int, default=20)
    args = parser.parse_args()

    if args.user_id:
        record = distill_user_profile(
            platform=args.platform,
            user_id=args.user_id,
            since_days=args.since_days,
            limit=args.activity_limit,
        )
        print(json.dumps(record or {}, ensure_ascii=False, indent=2))
        return 0

    records = distill_recent_users(
        platform=args.platform,
        since_days=args.since_days,
        user_limit=args.user_limit,
        activity_limit=args.activity_limit,
    )
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
