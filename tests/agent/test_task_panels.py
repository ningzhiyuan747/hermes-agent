from agent.task_panels import (
    build_activity_snapshot_text,
    format_background_job_snapshot,
    format_capability_run_snapshot,
    format_approval_list_text,
    format_task_panel_snapshot,
    sorted_artifact_items,
)


def test_sorted_artifact_items_prioritizes_deliverables_then_evidence():
    items = [
        {
            "label": "过程记录",
            "kind": "artifact",
            "summary": "普通过程产物",
            "created_at_unix": 10,
        },
        {
            "label": "证据链接",
            "kind": "link",
            "summary": "公开公告链接",
            "created_at_unix": 20,
        },
        {
            "label": "合同PDF",
            "kind": "file",
            "summary": "最终合同扫描件",
            "created_at_unix": 30,
            "metadata": {"final": True},
        },
    ]

    ordered = sorted_artifact_items(items)

    assert [item["label"] for item in ordered] == ["合同PDF", "证据链接", "过程记录"]


def test_format_task_panel_snapshot_includes_recent_artifacts_with_fullwidth_colon():
    snapshot = {
        "task": {
            "task_id": "task-123",
            "title": "司羿中标合同取证",
            "status": "active",
            "goal": "找到 24 年司羿手功能 06 08 的合同",
        },
        "current_run": {
            "capability_name": "contract_retrieval",
            "status": "running",
            "input": {
                "task_scope_key": "feishu:chat:group-42:thread:task-9",
                "person_memory_key": "feishu:user:user-7",
            },
        },
        "current_job": {"job_id": "job-42", "status": "active"},
        "current_delegation": {
            "worker_role": "ops-worker",
            "status": "running",
            "task_scope_key": "feishu:chat:group-42:thread:task-9",
            "person_memory_key": "feishu:user:user-7",
        },
        "approvals": [{"approval_id": "approval-1"}],
        "control_summary": {
            "status": "pending_approval",
            "execution_status": "running",
            "delivery_status": "pending",
            "current_executor": "openclaw",
            "current_focus": "正在搜合同公告",
            "next_step": "等审批通过后继续抓附件",
            "blocker": "Pending approval.",
            "recovery_hint": "Resolve the pending approval before dispatch continues.",
            "dispatch_action": "wait_approval",
            "suggested_executor": "openclaw",
        },
        "artifact_items": [
            {
                "label": "合同PDF",
                "kind": "file",
                "summary": "最终合同扫描件",
                "metadata": {"deliverable": True},
                "created_at_unix": 30,
            },
            {
                "label": "公告链接",
                "kind": "link",
                "path_or_ref": "https://example.com/notice",
                "created_at_unix": 20,
            },
            {
                "label": "过程报告",
                "kind": "artifact",
                "summary": "初步检索记录",
                "created_at_unix": 10,
            },
        ],
    }

    text = format_task_panel_snapshot(snapshot, fullwidth_colon=True)

    assert "Task ID: task-123" in text
    assert "标题: 司羿中标合同取证" in text
    assert "状态: pending_approval" in text
    assert "当前执行器: openclaw" in text
    assert "当前 Run: contract_retrieval / running" in text
    assert "当前后台任务: job-42 / active" in text
    assert "当前子代理: ops-worker / running" in text
    assert "待审批: 1" in text
    assert "当前: 正在搜合同公告" in text
    assert "下一步: 等审批通过后继续抓附件" in text
    assert "阻塞: Pending approval." in text
    assert "执行状态: running" in text
    assert "投递状态: pending" in text
    assert "恢复建议: Resolve the pending approval before dispatch continues." in text
    assert "调度动作: wait_approval" in text
    assert "建议执行器: openclaw" in text
    assert "Task scope： feishu:chat:group-42:thread:task-9" in text
    assert "Person memory： feishu:user:user-7" in text
    assert "Deliverable： 合同PDF -> 最终合同扫描件" in text
    assert "Evidence： 公告链接 -> https://example.com/notice" in text


def test_format_approval_list_text_prefers_task_scope_label():
    approvals = [
        {
            "approval_id": "approval-123",
            "status": "pending",
            "kind": "capability",
            "requested_by": "ou_user_1",
            "target_id": "run-1",
            "payload": {
                "capability": "quote_generation",
                "title": "华东报价单生成",
            },
        }
    ]

    text = format_approval_list_text(
        approvals,
        task_id="task-123",
        task_title="华东报价任务",
        scope_all=False,
    )

    assert text.startswith("当前任务审批列表：")
    assert "任务：华东报价任务" in text
    assert "approval-123 | pending | quote_generation | 华东报价单生成" in text


def test_format_capability_run_snapshot_includes_task_origin_and_artifacts():
    run = {
        "capability_name": "contract_retrieval",
        "run_id": "run-123",
        "trace_id": "trace-123",
        "status": "running",
        "title": "司羿合同取证",
        "task_id": "task-123",
        "task_title": "司羿合同任务",
        "approval_id": "approval-9",
        "current_focus": "正在追合同公告",
        "next_step": "继续追附件下载链接",
        "blocker": "公开站点返回慢",
        "result": "已拿到合同公告",
        "task_scope_key": "feishu:chat:group-42:thread:task-9",
        "person_memory_key": "feishu:user:user-7",
        "artifact_items": [
            {
                "label": "合同PDF",
                "kind": "file",
                "summary": "最终合同扫描件",
                "metadata": {"final": True},
                "created_at_unix": 30,
            }
        ],
        "origin": {"platform": "feishu", "chat_name": "商务", "chat_id": "oc_xxx"},
    }

    text = format_capability_run_snapshot(run, fullwidth_colon=True)

    assert "Capability： contract_retrieval" in text
    assert "Run： run-123" in text
    assert "任务： 司羿合同任务" in text
    assert "审批ID： approval-9" in text
    assert "Task scope： feishu:chat:group-42:thread:task-9" in text
    assert "Person memory： feishu:user:user-7" in text
    assert "来源： feishu / 商务" in text
    assert "Deliverable： 合同PDF -> 最终合同扫描件" in text


def test_format_background_job_snapshot_includes_run_fields_and_origin():
    job = {
        "status": "active",
        "job_id": "job-123",
        "trace_id": "trace-123",
        "executor": "openclaw",
        "tags": [
            "worker_kind:research",
            "task_scope:dingtalk:chat:cid_xxx",
            "person_memory:dingtalk:user:user-9",
        ],
        "title": "司羿合同检索",
        "current_focus": "正在搜公告",
        "next_step": "转附件页",
        "capability_name": "contract_retrieval",
        "capability_status": "running",
        "capability_run_id": "run-123",
        "task_id": "task-123",
        "task_title": "司羿合同任务",
        "approval_id": "approval-9",
        "task_scope_key": "dingtalk:chat:group-42:thread:task-9",
        "person_memory_key": "dingtalk:user:user-7",
        "capability_result": "已命中合同公告",
        "capability_focus": "锁定公告页",
        "capability_next_step": "继续找附件",
        "artifact_items": [
            {
                "label": "公告链接",
                "kind": "link",
                "path_or_ref": "https://example.com/notice",
                "created_at_unix": 20,
            }
        ],
        "origin": {"platform": "dingtalk", "chat_name": "商务", "chat_id": "cid_xxx"},
    }

    text = format_background_job_snapshot(job, fullwidth_colon=True)

    assert "后台研究任务： active" in text
    assert "任务ID： job-123" in text
    assert "Worker kind： research" in text
    assert "Capability： contract_retrieval / running" in text
    assert "Run： run-123" in text
    assert "任务： 司羿合同任务" in text
    assert "Task scope： dingtalk:chat:group-42:thread:task-9" in text
    assert "Person memory： dingtalk:user:user-7" in text
    assert "来源： dingtalk / 商务" in text



def test_build_activity_snapshot_text_prefers_background_job_over_run():
    job = {
        "status": "active",
        "job_id": "job-123",
        "trace_id": "trace-job",
        "executor": "openclaw",
    }
    run = {
        "capability_name": "contract_retrieval",
        "run_id": "run-123",
        "trace_id": "trace-run",
        "status": "running",
    }

    text = build_activity_snapshot_text(job, run, fullwidth_colon=True)

    assert "后台研究任务： active" in text
    assert "任务ID： job-123" in text
    assert "Run： run-123" not in text
