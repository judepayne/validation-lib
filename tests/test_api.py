"""Tests for ValidationService API."""

import json
import os
from pathlib import Path

import pytest

from validation_lib import ValidationService
from validation_lib.logic_fetcher import LogicPackageFetcher
from validation_lib.results import derive_object_status


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
def service():
    """Create a ValidationService instance for testing."""
    svc = ValidationService()
    yield svc
    svc.close()


@pytest.fixture
def sample_loan():
    """Sample loan entity for testing."""
    return {
        "$schema": SCHEMA_V1,
        "id": "LOAN-00001",
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


@pytest.fixture
def bad_loan(sample_loan):
    """Schema-conforming loan that fails rule_002_v1."""
    loan = json.loads(json.dumps(sample_loan))
    loan["id"] = "LOAN-99999"
    loan["financial"]["outstanding_balance"] = 150000
    return loan


@pytest.fixture
def vendor_payload():
    """Example payload consumed by vendor_x_loan plugin."""
    return {
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


def _find_rule(results, rule_id):
    for result in results:
        if result["rule_id"] == rule_id:
            return result
        child = _find_rule(result.get("children", []), rule_id)
        if child is not None:
            return child
    return None


class TestInitialization:
    """Test ValidationService initialization."""

    def test_create_service(self, service):
        """Test that service can be created."""
        assert service.engine is not None
        assert service.config_loader is not None
        assert service.plugin_loader is not None


class TestDiscover:
    """Test discovery methods."""

    def test_discover_rulesets(self, service):
        """Expected rulesets are present."""
        rulesets = service.discover_rulesets()
        assert "quick" in rulesets
        assert "thorough" in rulesets
        assert "metadata" in rulesets["quick"]
        assert "stats" in rulesets["quick"]

    def test_discover_rules(self, service, sample_loan):
        """Rules are discovered for a sample entity."""
        rules = service.discover_rules("loan", sample_loan, "quick")
        assert isinstance(rules, dict)
        assert "rule_001_v1" in rules
        assert "required_data" in rules["rule_001_v1"]


class TestValidate:
    """Test validate() envelopes."""

    def test_validate_returns_envelope(self, service, sample_loan):
        """Single validation returns an object-level envelope."""
        response = service.validate("loan", sample_loan, "quick")
        assert response["entity_type"] == "loan"
        assert response["ruleset"] == "quick"
        assert response["status"] in {"PASS", "WARN", "FAIL", "NORUN", "ERROR"}
        assert isinstance(response["results"], list)

    def test_bad_loan_fails_rule_002(self, service, bad_loan):
        """Bad loan fails rule_002_v1."""
        response = service.validate("loan", bad_loan, "quick")
        rule_002 = _find_rule(response["results"], "rule_002_v1")
        assert rule_002 is not None
        assert rule_002["status"] == "FAIL"
        assert response["status"] == "FAIL"
        assert "balance" in rule_002["message"].lower()

    def test_invalid_ruleset_returns_norun_envelope(self, service, sample_loan):
        """Unknown ruleset produces empty results and object NORUN."""
        response = service.validate("loan", sample_loan, "invalid_ruleset")
        assert response["status"] == "NORUN"
        assert response["results"] == []

    def test_vendor_plugin_success(self, service, vendor_payload):
        """vendor_x_loan plugin converts source data before validation."""
        response = service.validate(
            "loan", vendor_payload, "quick", plugin_name="vendor_x_loan"
        )
        assert response["entity_type"] == "loan"
        assert response["ruleset"] == "quick"
        assert response["status"] in {"PASS", "WARN", "FAIL", "NORUN", "ERROR"}
        assert "plugin_message" not in response
        assert _find_rule(response["results"], "rule_001_v1") is not None

    def test_vendor_plugin_error_returns_plugin_fail(self, service, vendor_payload):
        """PluginError becomes object-level PLUGIN_FAIL."""
        del vendor_payload["amount"]
        response = service.validate(
            "loan", vendor_payload, "quick", plugin_name="vendor_x_loan"
        )
        assert response["status"] == "PLUGIN_FAIL"
        assert response["results"] == []
        assert response["plugin_name"] == "vendor_x_loan"
        assert "amount" in response["plugin_message"]

    def test_unknown_plugin_raises_value_error(self, service, vendor_payload):
        """Unknown plugin is configuration/caller error."""
        with pytest.raises(ValueError, match="Unknown plugin"):
            service.validate("loan", vendor_payload, "quick", plugin_name="missing")

    def test_wrong_entity_type_plugin_raises_value_error(self, service, vendor_payload):
        """Plugin entity type mismatch is caller/config error."""
        with pytest.raises(ValueError, match="not facility"):
            service.validate(
                "facility", vendor_payload, "quick", plugin_name="vendor_x_loan"
            )


class TestPluginFailureMessages:
    """Test standard PLUGIN_FAIL messages for malformed plugin output."""

    class PluginError(Exception):
        """Test plugin error class."""

    class NonDictPlugin:
        def convert(self, input_data):
            return "not a dict"

    class MissingSchemaPlugin:
        def convert(self, input_data):
            return {"id": "LOAN-1"}

    class ExplodingPlugin:
        def convert(self, input_data):
            raise RuntimeError("boom")

    def _patch_plugin(self, monkeypatch, service, plugin):
        monkeypatch.setattr(service.plugin_loader, "load_plugin", lambda name: plugin)
        monkeypatch.setattr(
            service.plugin_loader,
            "get_plugin_error_class",
            lambda: self.PluginError,
        )

    def test_non_dict_plugin_output_is_plugin_fail(
        self, monkeypatch, service, vendor_payload
    ):
        """Non-dict plugin output gets standard PLUGIN_FAIL message."""
        self._patch_plugin(monkeypatch, service, self.NonDictPlugin())
        response = service.validate(
            "loan", vendor_payload, "quick", plugin_name="vendor_x_loan"
        )
        assert response["status"] == "PLUGIN_FAIL"
        assert response["plugin_message"] == (
            "Plugin output must be a dict containing a $schema field"
        )

    def test_missing_schema_plugin_output_is_plugin_fail(
        self, monkeypatch, service, vendor_payload
    ):
        """Dict without $schema gets standard PLUGIN_FAIL message."""
        self._patch_plugin(monkeypatch, service, self.MissingSchemaPlugin())
        response = service.validate(
            "loan", vendor_payload, "quick", plugin_name="vendor_x_loan"
        )
        assert response["status"] == "PLUGIN_FAIL"
        assert response["plugin_message"] == "Plugin output missing required $schema field"

    def test_unexpected_plugin_exception_is_plugin_fail(
        self, monkeypatch, service, vendor_payload
    ):
        """Unexpected plugin exception gets standard PLUGIN_FAIL message."""
        self._patch_plugin(monkeypatch, service, self.ExplodingPlugin())
        response = service.validate(
            "loan", vendor_payload, "quick", plugin_name="vendor_x_loan"
        )
        assert response["status"] == "PLUGIN_FAIL"
        assert "RuntimeError: boom" in response["plugin_message"]


class TestBatchValidate:
    """Test batch_validate() envelopes."""

    def test_batch_validate_items(self, service, sample_loan):
        """Batch validation returns a batch envelope with item envelopes."""
        response = service.batch_validate(
            [{"correlation_id": "row-1", "data": sample_loan}], "quick"
        )
        assert response["status"] == "COMPLETED"
        assert response["ruleset"] == "quick"
        assert len(response["items"]) == 1
        item = response["items"][0]
        assert item["correlation_id"] == "row-1"
        assert item["entity_type"] == "loan"
        assert isinstance(item["results"], list)

    def test_batch_preserves_order(self, service, sample_loan):
        """Batch result order matches input order."""
        items = [
            {"correlation_id": f"row-{i}", "data": dict(sample_loan, id=f"LOAN-{i:05d}")}
            for i in range(1, 6)
        ]
        response = service.batch_validate(items, "quick")
        assert [item["correlation_id"] for item in response["items"]] == [
            f"row-{i}" for i in range(1, 6)
        ]

    def test_batch_synthesizes_correlation_id(self, service, sample_loan):
        """Missing correlation_id is synthesized."""
        response = service.batch_validate([{"data": sample_loan}], "quick")
        assert response["items"][0]["correlation_id"] == "item-1"

    def test_batch_plugin_fail_does_not_stop_later_items(
        self, service, vendor_payload
    ):
        """One PLUGIN_FAIL item does not stop the rest of the batch."""
        bad_payload = dict(vendor_payload)
        del bad_payload["amount"]
        response = service.batch_validate(
            [
                {"correlation_id": "bad", "data": bad_payload},
                {"correlation_id": "good", "data": vendor_payload},
            ],
            "quick",
            plugin_name="vendor_x_loan",
        )
        assert response["plugin_name"] == "vendor_x_loan"
        assert response["items"][0]["status"] == "PLUGIN_FAIL"
        assert response["items"][0]["correlation_id"] == "bad"
        assert response["items"][1]["correlation_id"] == "good"
        assert response["items"][1]["status"] != "PLUGIN_FAIL"


class TestBatchFileValidate:
    """Test JSON and JSONL file loading."""

    def test_batch_file_validate_json(self, tmp_path, service, sample_loan):
        """JSON files are loaded and validated as batch items."""
        path = tmp_path / "loans.json"
        path.write_text(json.dumps([{"correlation_id": "json-1", "data": sample_loan}]))
        response = service.batch_file_validate(f"file://{path}", "quick")
        assert response["status"] == "COMPLETED"
        assert response["items"][0]["correlation_id"] == "json-1"

    def test_batch_file_validate_jsonl(self, tmp_path, service, sample_loan):
        """JSONL files are loaded line by line."""
        path = tmp_path / "loans.jsonl"
        path.write_text(
            json.dumps({"correlation_id": "line-a", "data": sample_loan}) + "\n"
        )
        response = service.batch_file_validate(f"file://{path}", "quick")
        assert response["status"] == "COMPLETED"
        assert response["items"][0]["correlation_id"] == "line-a"

    def test_batch_file_validate_jsonl_synthesizes_line_id(
        self, tmp_path, service, sample_loan
    ):
        """JSONL lines without correlation_id get line-N IDs."""
        path = tmp_path / "loans.jsonl"
        path.write_text(json.dumps({"data": sample_loan}) + "\n")
        response = service.batch_file_validate(f"file://{path}", "quick")
        assert response["items"][0]["correlation_id"] == "line-1"


class TestLogicFetcher:
    """Test logic package file derivation."""

    def test_plugin_files_are_required(self):
        """Plugin files are derived from business config."""
        files = LogicPackageFetcher.derive_required_files(
            {
                "plugins": {
                    "vendor_x_loan": {
                        "file": "plugins/vendor_x_loan.py",
                        "entity_type": "loan",
                    }
                }
            }
        )
        assert "plugins/base.py" in files
        assert "plugins/vendor_x_loan.py" in files


class TestStatusDerivation:
    """Test object status derivation helper."""

    def test_empty_results_are_norun(self):
        """Empty result list is object-level NORUN."""
        assert derive_object_status([]) == "NORUN"

    def test_all_pass_results_are_pass(self):
        """All PASS rule results produce object-level PASS."""
        results = [
            {"status": "PASS", "children": []},
            {"status": "PASS", "children": []},
        ]
        assert derive_object_status(results) == "PASS"

    def test_child_failure_affects_object_status(self):
        """Nested child results are included in status derivation."""
        results = [
            {
                "status": "PASS",
                "children": [{"status": "FAIL", "children": []}],
            }
        ]
        assert derive_object_status(results) == "FAIL"

    def test_error_precedence(self):
        """ERROR outranks FAIL/WARN/PASS."""
        results = [
            {"status": "FAIL", "children": []},
            {"status": "ERROR", "children": []},
        ]
        assert derive_object_status(results) == "ERROR"
