"""
Tests for JSON-RPC Server

Tests the JSON-RPC wrapper around ValidationService API.
"""
import json
import pytest
from validation_lib.jsonrpc_server import ValidationJsonRpcServer


@pytest.fixture
def server():
    """Create a ValidationJsonRpcServer instance for testing."""
    return ValidationJsonRpcServer(debug=False)


@pytest.fixture
def sample_loan():
    """Sample loan entity for testing."""
    return {
        "$schema": "https://bank.example.com/schemas/loan/v1.0.0",
        "id": "TEST-001",
        "loan_number": "LN-001",
        "facility_id": "FAC-100",
        "financial": {
            "principal_amount": 100000,
            "interest_rate": 0.045,
            "currency": "USD"
        },
        "dates": {
            "origination_date": "2024-01-01",
            "maturity_date": "2025-01-01"
        },
        "status": "active"
    }


class TestRequestParsing:
    """Test JSON-RPC request parsing."""

    def test_valid_request(self, server):
        """Test parsing valid JSON-RPC request."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "discover_rulesets",
            "params": {}
        })

        response = server.handle_request(request)

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "result" in response

    def test_invalid_json(self, server):
        """Test handling invalid JSON."""
        request = "not valid json {"

        response = server.handle_request(request)

        assert "error" in response
        assert response["error"]["code"] == server.ERROR_PARSE

    def test_missing_jsonrpc_version(self, server):
        """Test handling missing jsonrpc version."""
        request = json.dumps({
            "id": 1,
            "method": "discover_rulesets"
        })

        response = server.handle_request(request)

        assert "error" in response
        assert response["error"]["code"] == server.ERROR_INVALID_REQUEST

    def test_wrong_jsonrpc_version(self, server):
        """Test handling wrong JSON-RPC version."""
        request = json.dumps({
            "jsonrpc": "1.0",
            "id": 1,
            "method": "discover_rulesets"
        })

        response = server.handle_request(request)

        assert "error" in response
        assert response["error"]["code"] == server.ERROR_INVALID_REQUEST

    def test_missing_method(self, server):
        """Test handling missing method field."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "params": {}
        })

        response = server.handle_request(request)

        assert "error" in response
        assert response["error"]["code"] == server.ERROR_INVALID_REQUEST

    def test_params_not_dict(self, server):
        """Test handling params that are not a dict."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "discover_rulesets",
            "params": [1, 2, 3]  # Array instead of object
        })

        response = server.handle_request(request)

        assert "error" in response
        assert response["error"]["code"] == server.ERROR_INVALID_PARAMS


class TestMethodDispatch:
    """Test method dispatch."""

    def test_unknown_method(self, server):
        """Test calling unknown method."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "unknown_method",
            "params": {}
        })

        response = server.handle_request(request)

        assert "error" in response
        assert "not found" in response["error"]["message"].lower()

    def test_discover_rulesets_method(self, server):
        """Test discover_rulesets method."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "discover_rulesets",
            "params": {}
        })

        response = server.handle_request(request)

        assert "result" in response
        assert isinstance(response["result"], dict)
        assert "quick" in response["result"]

    def test_get_cache_age_method(self, server):
        """Test get_cache_age method."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "get_cache_age",
            "params": {}
        })

        response = server.handle_request(request)

        assert "result" in response
        assert "cache_age" in response["result"]

    def test_reload_logic_method(self, server):
        """Test reload_logic method."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "reload_logic",
            "params": {}
        })

        response = server.handle_request(request)

        assert "result" in response
        assert response["result"]["status"] == "ok"


class TestValidateMethod:
    """Test validate method via JSON-RPC."""

    def test_validate_success(self, server, sample_loan):
        """Test successful validation."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "validate",
            "params": {
                "entity_type": "loan",
                "entity_data": sample_loan,
                "ruleset_name": "quick"
            }
        })

        response = server.handle_request(request)

        assert "result" in response
        assert isinstance(response["result"], list)
        assert len(response["result"]) > 0

    def test_validate_missing_entity_type(self, server, sample_loan):
        """Test validate with missing entity_type."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "validate",
            "params": {
                "entity_data": sample_loan,
                "ruleset_name": "quick"
            }
        })

        response = server.handle_request(request)

        assert "error" in response
        assert "entity_type" in response["error"]["message"]

    def test_validate_missing_entity_data(self, server):
        """Test validate with missing entity_data."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "validate",
            "params": {
                "entity_type": "loan",
                "ruleset_name": "quick"
            }
        })

        response = server.handle_request(request)

        assert "error" in response
        assert "entity_data" in response["error"]["message"]

    def test_validate_missing_ruleset(self, server, sample_loan):
        """Test validate with missing ruleset_name."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "validate",
            "params": {
                "entity_type": "loan",
                "entity_data": sample_loan
            }
        })

        response = server.handle_request(request)

        assert "error" in response
        assert "ruleset_name" in response["error"]["message"]


class TestDiscoverRulesMethod:
    """Test discover_rules method via JSON-RPC."""

    def test_discover_rules_success(self, server, sample_loan):
        """Test successful rule discovery."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "discover_rules",
            "params": {
                "entity_type": "loan",
                "entity_data": sample_loan,
                "ruleset_name": "quick"
            }
        })

        response = server.handle_request(request)

        assert "result" in response
        assert isinstance(response["result"], dict)
        assert len(response["result"]) > 0

    def test_discover_rules_missing_params(self, server):
        """Test discover_rules with missing parameters."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "discover_rules",
            "params": {
                "entity_type": "loan"
            }
        })

        response = server.handle_request(request)

        assert "error" in response


class TestBatchValidateMethod:
    """Test batch_validate method via JSON-RPC."""

    def test_batch_validate_success(self, server, sample_loan):
        """Test successful batch validation."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "batch_validate",
            "params": {
                "entities": [sample_loan],
                "id_fields": ["id"],
                "ruleset_name": "quick"
            }
        })

        response = server.handle_request(request)

        assert "result" in response
        assert isinstance(response["result"], list)
        assert len(response["result"]) == 1

    def test_batch_validate_multiple_entities(self, server, sample_loan):
        """Test batch validation with multiple entities."""
        loan2 = sample_loan.copy()
        loan2["id"] = "TEST-002"

        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "batch_validate",
            "params": {
                "entities": [sample_loan, loan2],
                "id_fields": ["id"],
                "ruleset_name": "quick"
            }
        })

        response = server.handle_request(request)

        assert "result" in response
        assert len(response["result"]) == 2


class TestResponseFormat:
    """Test JSON-RPC response formatting."""

    def test_success_response_structure(self, server):
        """Test structure of successful response."""
        response = server._success_response(1, {"key": "value"})

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert response["result"] == {"key": "value"}

    def test_error_response_structure(self, server):
        """Test structure of error response."""
        response = server._error_response(1, -32000, "Test error")

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "error" in response
        assert response["error"]["code"] == -32000
        assert response["error"]["message"] == "Test error"

    def test_error_response_with_data(self, server):
        """Test error response with additional data."""
        response = server._error_response(1, -32000, "Test error",
                                         data={"detail": "Extra info"})

        assert "error" in response
        assert "data" in response["error"]
        assert response["error"]["data"]["detail"] == "Extra info"


class TestServerLifecycle:
    """Test server start/stop."""

    def test_server_initialization(self):
        """Test server can be initialized."""
        server = ValidationJsonRpcServer(debug=True)
        assert server is not None
        assert server.debug is True
        assert server.running is False

    def test_server_has_methods(self):
        """Test server has all expected methods."""
        server = ValidationJsonRpcServer()
        expected_methods = [
            'validate',
            'discover_rules',
            'discover_rulesets',
            'batch_validate',
            'batch_file_validate',
            'reload_logic',
            'get_cache_age'
        ]

        for method in expected_methods:
            assert method in server.methods

    def test_stop_server(self):
        """Test stop_server sets running flag."""
        server = ValidationJsonRpcServer()
        server.running = True
        server.stop_server()
        assert server.running is False
