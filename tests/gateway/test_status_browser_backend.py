"""Test gateway status includes browser backend information."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest


def test_runtime_status_includes_browser_backend_field():
    """Verify that _build_runtime_status_record includes browser_backend field."""
    from gateway.status import _build_runtime_status_record
    
    record = _build_runtime_status_record()
    assert "browser_backend" in record
    assert record["browser_backend"] is None  # default value


def test_write_runtime_status_accepts_browser_backend():
    """Verify write_runtime_status accepts and persists browser_backend."""
    from gateway.status import write_runtime_status, read_runtime_status
    
    fake_browser_status = {
        "mode": "local",
        "label": "local headless Chromium (agent-browser)",
        "connected": False,
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        status_path = Path(tmpdir) / "gateway_state.json"
        
        with patch("gateway.status._get_runtime_status_path", return_value=status_path):
            write_runtime_status(
                gateway_state="running",
                browser_backend=fake_browser_status,
            )
            
            persisted = read_runtime_status()
            assert persisted is not None
            assert persisted["browser_backend"] == fake_browser_status
            assert persisted["gateway_state"] == "running"


def test_gateway_update_runtime_status_captures_browser_backend():
    """Verify gateway's _update_runtime_status captures browser backend info."""
    from unittest.mock import MagicMock
    
    fake_browser_status = {
        "mode": "cdp",
        "label": "live Chrome via CDP",
        "connected": True,
        "endpoint": "http://localhost:9222",
    }
    
    # Mock the gateway instance
    gateway_mock = MagicMock()
    gateway_mock._restart_requested = False
    gateway_mock._running_agent_count = MagicMock(return_value=2)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        status_path = Path(tmpdir) / "gateway_state.json"
        
        with patch("gateway.status._get_runtime_status_path", return_value=status_path):
            with patch("tools.browser_tool.get_browser_backend_status", return_value=fake_browser_status):
                # Import and bind the method
                from gateway.run import GatewayRunner
                method = GatewayRunner._update_runtime_status
                method(gateway_mock, gateway_state="running")
                
                # Verify persisted data
                from gateway.status import read_runtime_status
                persisted = read_runtime_status()
                assert persisted is not None
                assert persisted["browser_backend"] == fake_browser_status
                assert persisted["gateway_state"] == "running"
                assert persisted["active_agents"] == 2


def test_browser_backend_status_survives_import_failure():
    """Verify gateway status update doesn't crash if browser_tool import fails."""
    from unittest.mock import MagicMock
    
    gateway_mock = MagicMock()
    gateway_mock._restart_requested = False
    gateway_mock._running_agent_count = MagicMock(return_value=0)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        status_path = Path(tmpdir) / "gateway_state.json"
        
        with patch("gateway.status._get_runtime_status_path", return_value=status_path):
            # Simulate import failure by making get_browser_backend_status raise
            with patch("tools.browser_tool.get_browser_backend_status", side_effect=ImportError("mock failure")):
                from gateway.run import GatewayRunner
                method = GatewayRunner._update_runtime_status
                
                # Should not crash
                method(gateway_mock, gateway_state="running")
                
                from gateway.status import read_runtime_status
                persisted = read_runtime_status()
                assert persisted is not None
                assert persisted["gateway_state"] == "running"
                # browser_backend should be None when import fails
                assert persisted.get("browser_backend") is None
