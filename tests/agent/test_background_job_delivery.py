from __future__ import annotations

from pathlib import Path

from agent import background_job_delivery
from agent.background_job_delivery import format_delivery_message


def test_format_delivery_message_uses_openclaw_research_subject():
    message = format_delivery_message(
        {
            "job_id": "job-1",
            "title": "查招标",
            "result": "OpenClaw 研究交付\n\n结论\n命中 2 条线索",
            "tags": ["executor:openclaw", "runtime:openclaw"],
        }
    )

    assert message.startswith("OpenClaw research 交付完成：查招标")
    assert "Job: job-1" in message


def test_extract_sendable_dingtalk_attachments_prefers_rich_variants(tmp_path, monkeypatch):
    md = tmp_path / "sample.md"
    pdf = tmp_path / "sample.pdf"
    docx = tmp_path / "sample.docx"
    md.write_text("stub", encoding="utf-8")
    pdf.write_text("pdf", encoding="utf-8")
    docx.write_text("docx", encoding="utf-8")

    monkeypatch.setattr(background_job_delivery, "_DINGTALK_ATTACHMENT_MAX_COUNT", 2)

    attachments = background_job_delivery._extract_sendable_dingtalk_attachments(
        f"本次生成文件：{md}"
    )

    assert attachments == [docx, pdf]


def test_extract_sendable_dingtalk_attachments_detects_generated_path_from_evidence_lines(tmp_path, monkeypatch):
    md = tmp_path / "sample.md"
    docx = tmp_path / "sample.docx"
    pdf = tmp_path / "sample.pdf"
    md.write_text("stub", encoding="utf-8")
    docx.write_text("docx", encoding="utf-8")
    pdf.write_text("pdf", encoding="utf-8")

    monkeypatch.setattr(background_job_delivery, "_DINGTALK_ATTACHMENT_MAX_COUNT", 2)

    text = (
        "3. 证据与来源\n"
        "- 本轮实际生成的文件：\n"
        f"- {md}\n"
        f"- 写入回执：Successfully wrote 10 bytes to {md}\n"
    )

    attachments = background_job_delivery._extract_sendable_dingtalk_attachments(text)

    assert attachments == [docx, pdf]


def test_extract_sendable_dingtalk_attachments_materializes_markdown_when_variants_missing(tmp_path, monkeypatch):
    md = tmp_path / "sample.md"
    md.write_text("stub", encoding="utf-8")
    docx = tmp_path / "sample.docx"
    pdf = tmp_path / "sample.pdf"

    def _fake_materialize(path, _prompt):
        docx.write_text("docx", encoding="utf-8")
        pdf.write_text("pdf", encoding="utf-8")
        return [docx, pdf] if path == md else [path]

    monkeypatch.setattr(background_job_delivery, "_DINGTALK_ATTACHMENT_MAX_COUNT", 2)
    monkeypatch.setattr(background_job_delivery, "_materialize_attachment_variants", _fake_materialize)

    attachments = background_job_delivery._extract_sendable_dingtalk_attachments(
        f"本次生成文件：{md}"
    )

    assert attachments == [docx, pdf]


def test_extract_sendable_dingtalk_attachments_ignores_reference_file_paths(tmp_path, monkeypatch):
    rule_md = tmp_path / "RULES.md"
    rule_docx = tmp_path / "RULES.docx"
    rule_pdf = tmp_path / "RULES.pdf"
    rule_md.write_text("rules", encoding="utf-8")
    rule_docx.write_text("docx", encoding="utf-8")
    rule_pdf.write_text("pdf", encoding="utf-8")

    monkeypatch.setattr(background_job_delivery, "_DINGTALK_ATTACHMENT_MAX_COUNT", 2)

    text = (
        "3. 证据与来源\n"
        "- 规则文件：\n"
        f"- `{rule_md}`\n"
    )

    attachments = background_job_delivery._extract_sendable_dingtalk_attachments(text)

    assert attachments == []


def test_format_delivery_message_for_formal_document_is_concise(tmp_path):
    docx = tmp_path / "信用承诺书.docx"
    pdf = tmp_path / "信用承诺书.pdf"
    docx.write_text("docx", encoding="utf-8")
    pdf.write_text("pdf", encoding="utf-8")

    message = format_delivery_message(
        {
            "job_id": "job-2",
            "title": "信用承诺书初稿",
            "prompt": "按模板生成一份信用承诺书，并输出 docx/pdf",
            "result": (
                "模板来源：F:\\模板库\\信用承诺书模板.doc\n"
                "正文第一段\n"
                "执行结果：\n"
                f"- 已导出文件：{docx}\n"
                f"- 已导出文件：{pdf}\n"
                "证据：\n"
                "- 文档校验通过\n"
            ),
        }
    )

    assert message.startswith("已生成：信用承诺书初稿")
    assert "本次生成文件：" in message
    assert str(docx) in message
    assert str(pdf) in message
    assert "执行结果" not in message
    assert "证据" not in message


def test_deliver_job_result_marks_delivery_failed_when_platform_send_returns_error(monkeypatch):
    job = {
        "job_id": "job-3",
        "origin": {"platform": "weixin", "chat_id": "wxid_demo"},
        "result": "done",
    }
    updates = []
    events = []

    monkeypatch.setattr(background_job_delivery, "get_job", lambda _job_id: dict(job))
    monkeypatch.setattr(
        background_job_delivery,
        "update_job",
        lambda job_id, **fields: updates.append((job_id, fields)) or {**job, **fields},
    )
    monkeypatch.setattr(
        background_job_delivery,
        "append_job_event",
        lambda job_id, **fields: events.append((job_id, fields)),
    )
    monkeypatch.setattr(
        background_job_delivery,
        "send_text_to_target",
        lambda target, text: {"error": "Weixin send failed: invalid context"},
    )

    updated = background_job_delivery.deliver_job_result(job)

    assert updated["delivery_status"] == "failed"
    assert "invalid context" in updated["delivery_error"]
    assert updates[-1][1]["delivery_status"] == "failed"
    assert events[-1][1]["kind"] == "delivery_failed"
