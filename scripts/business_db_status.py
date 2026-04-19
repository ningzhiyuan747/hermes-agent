#!/usr/bin/env python3
"""Show Hermes structured business database status."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.background_jobs import iter_jobs, recent_events
from agent.business_db import (
    connect,
    list_approvals,
    list_capabilities,
    list_capability_runs,
    status_snapshot,
    upsert_background_job,
    insert_background_job_event,
)


def _translate_status(value: str) -> str:
    return {
        "created": "已创建",
        "running": "运行中",
        "completed": "已完成",
        "cancelled": "已取消",
        "failed": "失败",
        "pending": "待处理",
        "pending_approval": "待审批",
        "queued": "已排队",
    }.get(str(value or "").strip(), str(value or "").strip())


def _translate_kind(value: str) -> str:
    return {"capability_run": "能力运行"}.get(str(value or "").strip(), str(value or "").strip())


def _translate_risk(value: str) -> str:
    return {"low": "低", "medium": "中", "high": "高"}.get(str(value or "").strip(), str(value or "").strip())


def _translate_category(value: str) -> str:
    return {
        "business_automation": "业务自动化",
        "collaboration": "协作",
        "data": "数据",
        "documents": "文档",
        "finance": "财务",
        "hr_admin": "人事行政",
        "knowledge": "知识",
        "legal_compliance": "法务合规",
        "operations": "运维",
        "orchestration": "编排",
        "procurement": "采购",
        "project_management": "项目管理",
        "reporting": "汇报",
        "research": "研究",
        "sales": "销售",
    }.get(str(value or "").strip(), str(value or "").strip())


def _translate_capability(value: str) -> str:
    return {
        "table_sync": "表同步",
        "meeting_minutes": "会议纪要",
        "data_cleaning": "数据清洗",
        "document_generate": "文档生成",
        "finance_reconcile": "财务对账",
        "hr_admin": "人事行政",
        "knowledge_ingest": "知识入库",
        "contract_review": "合同审查",
        "inventory_check": "库存检查",
        "ops_recovery": "运维恢复",
        "workflow_dispatch": "流程分发",
        "procurement_compare": "采购比对",
        "project_tracking": "项目跟踪",
        "report_rollup": "报告汇总",
        "bid_research": "投标研究",
        "contract_retrieval": "合同检索",
        "customer_followup": "客户跟进",
        "quote_generation": "报价生成",
    }.get(str(value or "").strip(), str(value or "").strip())


def _translate_title(value: str) -> str:
    text = str(value or "")
    replacements = {
        "Report rollup - ": "报告汇总 - ",
        "Contract retrieval - ": "合同检索 - ",
        "Bid research - ": "投标研究 - ",
        "Customer follow-up - ": "客户跟进 - ",
        "Ops recovery - ": "运维恢复 - ",
        "Meeting minutes - ": "会议纪要 - ",
    }
    for old, new in replacements.items():
        if text.startswith(old):
            return new + text[len(old):]
    return text


def sync_background_jobs_from_json() -> int:
    count = 0
    for job in iter_jobs():
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue
        upsert_background_job(job)
        with connect() as conn:
            conn.execute("DELETE FROM background_job_events WHERE job_id=?", (job_id,))
            conn.commit()
        for event in recent_events(job_id, limit=500):
            insert_background_job_event(job_id, event)
        count += 1
    return count


def render_status() -> str:
    snapshot = status_snapshot()
    lines = [
        "Hermes 业务数据库",
        "",
        f"路径: {snapshot['path']}",
        f"模式版本: {snapshot['schema_version']}",
        f"大小: {snapshot['size_bytes']} bytes",
        f"活跃后台任务: {snapshot['active_background_jobs']}",
        "",
        "表记录数:",
    ]
    for table, count in snapshot["counts"].items():
        lines.append(f"- {table}: {count}")
    pending_approvals = list_approvals(status="pending", limit=5)
    if pending_approvals:
        lines.extend(["", "待审批:"])
        for item in pending_approvals:
            lines.append(f"- {item['approval_id']} | {_translate_kind(item['kind'])} | {item['target_id']} | 申请人={item['requested_by'] or '-'}")
    capabilities = list_capabilities(enabled_only=True, limit=20)
    if capabilities:
        lines.extend(["", "已启用能力:"])
        grouped = {}
        for item in capabilities:
            grouped.setdefault(item["category"], []).append(item)
        for category in sorted(grouped):
            names = []
            for item in grouped[category]:
                approval = "需审批" if item.get("default_approval_required") else "可自动"
                names.append(f"{_translate_capability(item['name'])}({_translate_risk(item['risk_level'])}/{approval})")
            lines.append(f"- {_translate_category(category)}: {', '.join(names)}")
    runs = list_capability_runs(limit=8)
    if runs:
        lines.extend(["", "最近能力运行:"])
        for run in runs:
            lines.append(f"- {run['run_id']} | {_translate_capability(run['capability_name'])} | {_translate_status(run['status'])} | {_translate_title(run['title'])}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="显示 Hermes 业务数据库状态。")
    parser.add_argument("--sync-background-jobs", action="store_true", help="将现有 JSON 后台任务同步到 SQLite。")
    parser.add_argument("--json", action="store_true", help="输出 JSON。")
    args = parser.parse_args()

    synced = sync_background_jobs_from_json() if args.sync_background_jobs else 0
    if args.json:
        payload = status_snapshot()
        payload["synced_background_jobs"] = synced
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if synced:
            print(f"已同步后台任务 JSON 到数据库: {synced}")
            print("")
        print(render_status())


if __name__ == "__main__":
    main()
