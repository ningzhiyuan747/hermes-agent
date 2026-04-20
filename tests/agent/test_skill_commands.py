from pathlib import Path

from agent.skill_commands import _build_skill_message


def _loaded_skill(description: str = "Debug Python scripts"):
    return {
        "name": "python-debug",
        "content": "# Python Debug\n\nStep 1: inspect traceback\nStep 2: reproduce bug\n",
        "description": description,
        "linked_files": {
            "references": ["references/guide.md"],
        },
    }


def test_build_skill_message_compact_mode_omits_full_content_and_supporting_files(tmp_path):
    skill_dir = tmp_path / "coding" / "python-debug"
    skill_dir.mkdir(parents=True)

    result = _build_skill_message(
        _loaded_skill(),
        skill_dir,
        '[SYSTEM: auto-loaded]',
        compact=True,
    )

    assert '[SYSTEM: auto-loaded]' in result
    assert '[Skill card]' in result
    assert '- name: python-debug' in result
    assert '- summary: Debug Python scripts' in result
    assert 'Step 1: inspect traceback' not in result
    assert 'supporting files' not in result
    assert '- full: skill_view(name="python-debug")' in result


def test_build_skill_message_default_mode_keeps_full_content(tmp_path):
    skill_dir = tmp_path / "coding" / "python-debug"
    skill_dir.mkdir(parents=True)

    result = _build_skill_message(
        _loaded_skill(),
        skill_dir,
        '[SYSTEM: explicit load]',
    )

    assert '[SYSTEM: explicit load]' in result
    assert 'Step 1: inspect traceback' in result
    assert 'supporting files' in result
