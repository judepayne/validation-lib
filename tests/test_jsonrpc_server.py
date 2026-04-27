"""Tests for JSON-RPC server."""

import io
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from validation_lib.jsonrpc_server import ValidationJsonRpcServer


SCHEMA_V1 = (
    "https://raw.githubusercontent.com/judepayne/validation-logic/main/"
    "models/loan.schema.v1.0.0.json"
)


@pytest.fixture(autouse=True)
def local_logic(monkeypatch):
    """Point tests at the sibling validation-logic checkout, not GitHub."""
    repo_root = Path(__file__).resolve().parents[2]
    business_config = repo_root / "validation-logic" / "business-config.yaml"
    monkeypatch.setenv(
        "VALIDATION_LIB_BUSINESS_CONFIG_URI", f"file://{business_config}"
    )


@pytest.fixture
def server():
    """Create a ValidationJsonRpcServer instance for testing."""
    return ValidationJsonRpcServer(debug=False)


@pytest.fixture
def sample_loan():
    """Sample loan entity for testing."""
    return {
        "$schema": SCHEMA_V1,
        "id": "TEST-001",
        "loan_number": "LN-001",
        "facility_id": "FAC-100",
        "financial": {
            "principal_amount": 100000,
            "interest_rate": 0.045,
            "currency": "USD",
        },
        "dates": {"origination_date": "2024-01-01", "maturity_date": "2025-01-01"},
        "status": "active",
    }


def _free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _tcp_call(port: int, method: str, params: dict) -> dict:
    """Open a TCP connection, send one JSON-RPC request, return response."""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        rfile = sock.makefile("r", encoding="utf-8")
        wfile = sock.makefile("w", encoding="utf-8")
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        wfile.write(json.dumps(request) + "\n")
        wfile.flush()
        return json.loads(rfile.readline())


class TestRequestParsing:
    """Test JSON-RPC request parsing."""

    def test_valid_request(self, server):
        """Test parsing valid JSON-RPC request."""
        request = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "discover_rulesets", "params": {}}
        )
        response = server.handle_request(request)
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "result" in response

    def test_invalid_json(self, server):
        """Test handling invalid JSON."""
        response = server.handle_request("not valid json {")
        assert response["error"]["code"] == server.ERROR_PARSE

    def test_missing_jsonrpc_version(self, server):
        """Test handling missing jsonrpc version."""
        response = server.handle_request(json.dumps({"id": 1, "method": "x"}))
        assert response["error"]["code"] == server.ERROR_INVALID_REQUEST

    def test_params_not_dict(self, server):
        """Test handling params that are not a dict."""
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "discover_rulesets",
                "params": [],
            }
        )
        response = server.handle_request(request)
        assert response["error"]["code"] == server.ERROR_INVALID_PARAMS


class TestMethodDispatch:
    """Test method dispatch."""

    def test_unknown_method(self, server):
        """Test calling unknown method."""
        request = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "unknown_method", "params": {}}
        )
        response = server.handle_request(request)
        assert "error" in response
        assert "not found" in response["error"]["message"].lower()

    def test_discover_rulesets_method(self, server):
        """Test discover_rulesets method."""
        request = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "discover_rulesets", "params": {}}
        )
        response = server.handle_request(request)
        assert "quick" in response["result"]

    def test_get_cache_age_method(self, server):
        """Test get_cache_age method."""
        request = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "get_cache_age", "params": {}}
        )
        response = server.handle_request(request)
        assert "cache_age" in response["result"]


class TestValidateMethod:
    """Test validate method via JSON-RPC."""

    def test_validate_success(self, server, sample_loan):
        """Test successful validation."""
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "validate",
                "params": {
                    "entity_type": "loan",
                    "entity_data": sample_loan,
                    "ruleset_name": "quick",
                },
            }
        )
        response = server.handle_request(request)
        assert "result" in response
        assert response["result"]["entity_type"] == "loan"
        assert response["result"]["ruleset"] == "quick"
        assert isinstance(response["result"]["results"], list)

    def test_validate_missing_entity_type(self, server, sample_loan):
        """Test validate with missing entity_type."""
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "validate",
                "params": {"entity_data": sample_loan, "ruleset_name": "quick"},
            }
        )
        response = server.handle_request(request)
        assert "error" in response
        assert "entity_type" in response["error"]["message"]

    def test_validate_plugin_name(self, server):
        """Test validate forwards plugin_name."""
        payload = {
            "vendor_id": "LOAN-00077",
            "loan_ref": "LN-VENDOR-077",
            "facility_ref": "FAC-100",
            "amount": 100000,
            "currency": "USD",
            "rate": 0.045,
            "origination": "2024-01-01",
            "maturity": "2025-01-01",
            "status": "active",
        }
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "validate",
                "params": {
                    "entity_type": "loan",
                    "entity_data": payload,
                    "ruleset_name": "quick",
                    "plugin_name": "vendor_x_loan",
                },
            }
        )
        response = server.handle_request(request)
        assert "result" in response
        assert response["result"]["status"] != "PLUGIN_FAIL"


class TestDiscoverRulesMethod:
    """Test discover_rules method via JSON-RPC."""

    def test_discover_rules_success(self, server, sample_loan):
        """Test successful rule discovery."""
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "discover_rules",
                "params": {
                    "entity_type": "loan",
                    "entity_data": sample_loan,
                    "ruleset_name": "quick",
                },
            }
        )
        response = server.handle_request(request)
        assert "rule_001_v1" in response["result"]


class TestBatchValidateMethod:
    """Test batch_validate method via JSON-RPC."""

    def test_batch_validate_success(self, server, sample_loan):
        """Test successful batch validation."""
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "batch_validate",
                "params": {
                    "items": [{"correlation_id": "row-1", "data": sample_loan}],
                    "ruleset_name": "quick",
                },
            }
        )
        response = server.handle_request(request)
        assert response["result"]["status"] == "COMPLETED"
        assert response["result"]["items"][0]["correlation_id"] == "row-1"

    def test_batch_validate_missing_items(self, server):
        """Test batch_validate requires items."""
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "batch_validate",
                "params": {"ruleset_name": "quick"},
            }
        )
        response = server.handle_request(request)
        assert "error" in response
        assert "items" in response["error"]["message"]


class TestResponseFormat:
    """Test JSON-RPC response formatting."""

    def test_success_response_structure(self, server):
        """Test structure of successful response."""
        response = server._success_response(1, {"key": "value"})
        assert response == {"jsonrpc": "2.0", "id": 1, "result": {"key": "value"}}

    def test_error_response_structure(self, server):
        """Test structure of error response."""
        response = server._error_response(1, -32000, "Test error")
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert response["error"]["code"] == -32000


class TestServerLifecycle:
    """Test server start/stop."""

    def test_server_initialization(self):
        """Test server can be initialized."""
        server = ValidationJsonRpcServer(debug=True)
        assert server.debug is True
        assert server.running is False

    def test_server_has_methods(self):
        """Test server has expected methods."""
        server = ValidationJsonRpcServer()
        expected_methods = [
            "validate",
            "discover_rules",
            "discover_rulesets",
            "batch_validate",
            "batch_file_validate",
            "reload_logic",
            "get_cache_age",
        ]
        for method in expected_methods:
            assert method in server.methods

    def test_stop_server(self, server):
        """Test stop_server sets running flag."""
        server.running = True
        server.stop_server()
        assert server.running is False


class TestSendResponseWfile:
    """Test _send_response() with an explicit wfile."""

    def test_send_response_writes_to_wfile(self, server):
        """_send_response writes to provided wfile."""
        wfile = io.StringIO()
        server._send_response({"jsonrpc": "2.0", "id": 1, "result": "ok"}, wfile)
        parsed = json.loads(wfile.getvalue().strip())
        assert parsed["result"] == "ok"


class TestServeStream:
    """Test _serve_stream() using in-memory file-like objects."""

    def test_single_request_processed(self, server):
        """A valid request is handled and response written."""
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "discover_rulesets",
                "params": {},
            }
        )
        rfile = io.StringIO(request + "\n")
        wfile = io.StringIO()
        server.running = True
        server._serve_stream(rfile, wfile)
        response = json.loads(wfile.getvalue().strip())
        assert "result" in response

    def test_notification_produces_no_response(self, server):
        """A notification with no id must not produce a response."""
        notification = json.dumps(
            {"jsonrpc": "2.0", "method": "discover_rulesets", "params": {}}
        )
        rfile = io.StringIO(notification + "\n")
        wfile = io.StringIO()
        server.running = True
        server._serve_stream(rfile, wfile)
        assert wfile.getvalue() == ""


class TestTcpServer:
    """Smoke-test TCP JSON-RPC transport."""

    def test_tcp_single_request(self):
        """TCP server responds to one request."""
        server = ValidationJsonRpcServer(debug=False)
        port = _free_port()
        thread = threading.Thread(
            target=server.start_tcp_server, args=("127.0.0.1", port), daemon=True
        )
        thread.start()
        time.sleep(0.2)
        try:
            response = _tcp_call(port, "discover_rulesets", {})
            assert "result" in response
        finally:
            server.stop_server()
            thread.join(timeout=2)
            server.service.close()
