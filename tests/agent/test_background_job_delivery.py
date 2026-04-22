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
