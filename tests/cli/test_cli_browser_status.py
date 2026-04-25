from datetime import datetime
from unittest.mock import MagicMock, patch

from cli import HermesCLI


def _make_cli():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.config = {}
    cli_obj.console = MagicMock()
    cli_obj.agent = None
    cli_obj.conversation_history = []
    cli_obj.session_id = "session-browser"
    cli_obj._pending_input = MagicMock()
    cli_obj._status_bar_visible = True
    cli_obj.model = "openai/gpt-5.4"
    cli_obj.provider = "openai"
    cli_obj.session_start = datetime(2026, 4, 22, 15, 30)
    return cli_obj


def test_browser_status_prints_cdp_inventory(monkeypatch, capsys):
    cli_obj = _make_cli()
    monkeypatch.setenv("BROWSER_CDP_URL", "http://127.0.0.1:9222")

    fake_status = {
        "mode": "cdp",
        "label": "live Chrome via CDP",
        "endpoint": "http://127.0.0.1:9222",
        "resolved_endpoint": "ws://127.0.0.1:9222/devtools/browser/abc",
        "reachable": True,
        "browser_version": "Chrome/136.0.0.0",
        "discovered_websocket": "ws://127.0.0.1:9222/devtools/browser/abc",
        "window_title": "Docs - Hermes",
    }

    with patch("tools.browser_tool.get_browser_backend_status", return_value=fake_status):
        cli_obj._handle_browser_command("/browser status")

    out = capsys.readouterr().out
    assert "Browser: live Chrome via CDP" in out
    assert "Endpoint: http://127.0.0.1:9222" in out
    assert "Resolved: ws://127.0.0.1:9222/devtools/browser/abc" in out
    assert "Status: ✓ reachable" in out
    assert "Browser: Chrome/136.0.0.0" in out
    assert "Window: Docs - Hermes" in out


def test_browser_status_prints_cloud_provider_inventory(monkeypatch, capsys):
    cli_obj = _make_cli()
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    fake_status = {
        "mode": "cloud",
        "label": "browser-use (cloud)",
        "provider": "browser-use",
        "connected": False,
    }

    with patch("tools.browser_tool.get_browser_backend_status", return_value=fake_status):
        cli_obj._handle_browser_command("/browser status")

    out = capsys.readouterr().out
    assert "Browser: browser-use (cloud)" in out
    assert "Provider: browser-use" in out
