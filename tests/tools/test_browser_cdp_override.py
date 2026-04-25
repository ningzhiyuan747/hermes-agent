from unittest.mock import Mock, patch


HOST = "example-host"
PORT = 9223
WS_URL = f"ws://{HOST}:{PORT}/devtools/browser/abc123"
HTTP_URL = f"http://{HOST}:{PORT}"
VERSION_URL = f"{HTTP_URL}/json/version"


class TestResolveCdpOverride:
    def test_keeps_full_devtools_websocket_url(self):
        from tools.browser_tool import _resolve_cdp_override

        assert _resolve_cdp_override(WS_URL) == WS_URL

    def test_resolves_http_discovery_endpoint_to_websocket(self):
        from tools.browser_tool import _resolve_cdp_override

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        with patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            resolved = _resolve_cdp_override(HTTP_URL)

        assert resolved == WS_URL
        mock_get.assert_called_once_with(VERSION_URL, timeout=10)

    def test_resolves_bare_ws_hostport_to_discovery_websocket(self):
        from tools.browser_tool import _resolve_cdp_override

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        with patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            resolved = _resolve_cdp_override(f"ws://{HOST}:{PORT}")

        assert resolved == WS_URL
        mock_get.assert_called_once_with(VERSION_URL, timeout=10)

    def test_falls_back_to_raw_url_when_discovery_fails(self):
        from tools.browser_tool import _resolve_cdp_override

        with patch("tools.browser_tool.requests.get", side_effect=RuntimeError("boom")):
            assert _resolve_cdp_override(HTTP_URL) == HTTP_URL

    def test_normalizes_provider_returned_http_cdp_url_when_creating_session(self, monkeypatch):
        import tools.browser_tool as browser_tool

        provider = Mock()
        provider.create_session.return_value = {
            "session_name": "cloud-session",
            "bb_session_id": "bu_123",
            "cdp_url": "https://cdp.browser-use.example/session",
            "features": {"browser_use": True},
        }

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        monkeypatch.setattr(browser_tool, "_active_sessions", {})
        monkeypatch.setattr(browser_tool, "_session_last_activity", {})
        monkeypatch.setattr(browser_tool, "_start_browser_cleanup_thread", lambda: None)
        monkeypatch.setattr(browser_tool, "_update_session_activity", lambda task_id: None)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "")
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: provider)

        with patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            session_info = browser_tool._get_session_info("task-browser-use")

        assert session_info["cdp_url"] == WS_URL
        provider.create_session.assert_called_once_with("task-browser-use")
        mock_get.assert_called_once_with(
            "https://cdp.browser-use.example/session/json/version",
            timeout=10,
        )


class TestBrowserBackendStatus:
    def test_reports_local_mode_when_no_override_or_cloud_provider(self, monkeypatch):
        from tools.browser_tool import get_browser_backend_status

        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
        monkeypatch.setattr("tools.browser_tool._get_cloud_provider", lambda: None)

        status = get_browser_backend_status()

        assert status["mode"] == "local"
        assert status["connected"] is False
        assert "local headless Chromium" in status["label"]

    def test_reports_cdp_discovery_details(self, monkeypatch):
        from tools.browser_tool import get_browser_backend_status

        monkeypatch.setenv("BROWSER_CDP_URL", HTTP_URL)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "Browser": "Chrome/136.0.0.0",
            "windowTitle": "Hermes Test Browser",
            "webSocketDebuggerUrl": WS_URL,
        }

        with patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            status = get_browser_backend_status()

        assert status["mode"] == "cdp"
        assert status["reachable"] is True
        assert status["endpoint"] == HTTP_URL
        assert status["resolved_endpoint"] == WS_URL
        assert status["browser_version"] == "Chrome/136.0.0.0"
        assert status["window_title"] == "Hermes Test Browser"
        assert status["discovered_websocket"] == WS_URL
        assert mock_get.call_count == 2

    def test_reports_probe_error_for_unreachable_cdp_endpoint(self, monkeypatch):
        from tools.browser_tool import get_browser_backend_status

        monkeypatch.setenv("BROWSER_CDP_URL", HTTP_URL)
        with patch("tools.browser_tool.requests.get", side_effect=RuntimeError("boom")):
            status = get_browser_backend_status()

        assert status["mode"] == "cdp"
        assert status["reachable"] is False
        assert "boom" in status["error"]
