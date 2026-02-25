"""
Tests for ValidationService API

Tests all 7 public API methods with various scenarios.
"""
import os
import pytest
from validation_lib import ValidationService


@pytest.fixture
def service():
    """Create a ValidationService instance for testing."""
    return ValidationService()


@pytest.fixture
def sample_loan():
    """Sample loan entity for testing."""
    return {
        "$schema": "https://raw.githubusercontent.com/judepayne/validation-logic/main/models/loan.schema.v1.0.0.json",
        "id": "LOAN-00001",
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


@pytest.fixture
def bad_loan():
    """Bad loan that conforms to schema but fails rule 2 (outstanding balance exceeds principal)."""
    return {
        "$schema": "https://raw.githubusercontent.com/judepayne/validation-logic/main/models/loan.schema.v1.0.0.json",
        "id": "LOAN-99999",
        "loan_number": "LN-BAD-001",
        "facility_id": "FAC-100",
        "financial": {
            "principal_amount": 100000,
            "outstanding_balance": 150000,  # Exceeds principal - violates rule 2
            "interest_rate": 0.045,
            "currency": "USD"
        },
        "dates": {
            "origination_date": "2024-01-01",
            "maturity_date": "2025-01-01"
        },
        "status": "active"
    }


class TestInitialization:
    """Test ValidationService initialization."""

    def test_create_service(self):
        """Test that service can be created."""
        service = ValidationService()
        assert service is not None

    def test_service_has_engine(self, service):
        """Test that service has validation engine."""
        assert hasattr(service, 'engine')
        assert service.engine is not None

    def test_service_has_config_loader(self, service):
        """Test that service has config loader."""
        assert hasattr(service, 'config_loader')
        assert service.config_loader is not None


class TestDiscoverRulesets:
    """Test discover_rulesets() method."""

    def test_discover_rulesets_returns_dict(self, service):
        """Test that discover_rulesets returns a dictionary."""
        rulesets = service.discover_rulesets()
        assert isinstance(rulesets, dict)

    def test_discover_rulesets_has_expected_rulesets(self, service):
        """Test that expected rulesets are present."""
        rulesets = service.discover_rulesets()
        assert 'quick' in rulesets
        assert 'thorough' in rulesets

    def test_ruleset_has_metadata(self, service):
        """Test that rulesets have metadata."""
        rulesets = service.discover_rulesets()
        quick = rulesets['quick']
        assert 'metadata' in quick
        assert 'description' in quick['metadata']

    def test_ruleset_has_stats(self, service):
        """Test that rulesets have statistics."""
        rulesets = service.discover_rulesets()
        quick = rulesets['quick']
        assert 'stats' in quick
        assert 'total_rules' in quick['stats']


class TestDiscoverRules:
    """Test discover_rules() method."""

    def test_discover_rules_returns_dict(self, service, sample_loan):
        """Test that discover_rules returns a dictionary."""
        rules = service.discover_rules("loan", sample_loan, "quick")
        assert isinstance(rules, dict)

    def test_discover_rules_has_rules(self, service, sample_loan):
        """Test that rules are discovered."""
        rules = service.discover_rules("loan", sample_loan, "quick")
        assert len(rules) > 0

    def test_rule_metadata_structure(self, service, sample_loan):
        """Test that rule metadata has expected structure."""
        rules = service.discover_rules("loan", sample_loan, "quick")
        first_rule = next(iter(rules.values()))

        assert 'rule_id' in first_rule
        assert 'description' in first_rule
        assert 'required_data' in first_rule


class TestValidate:
    """Test validate() method."""

    def test_validate_returns_list(self, service, sample_loan):
        """Test that validate returns a list."""
        results = service.validate("loan", sample_loan, "quick")
        assert isinstance(results, list)

    def test_validate_has_results(self, service, sample_loan):
        """Test that validation produces results."""
        results = service.validate("loan", sample_loan, "quick")
        assert len(results) > 0

    def test_result_structure(self, service, sample_loan):
        """Test that results have expected structure."""
        results = service.validate("loan", sample_loan, "quick")
        first_result = results[0]

        assert 'rule_id' in first_result
        assert 'status' in first_result
        assert 'description' in first_result

    def test_status_values(self, service, sample_loan):
        """Test that status values are valid."""
        results = service.validate("loan", sample_loan, "quick")
        valid_statuses = {'PASS', 'FAIL', 'NORUN', 'ERROR', 'WARN'}

        for result in results:
            assert result['status'] in valid_statuses

    def test_validate_thorough_ruleset(self, service, sample_loan):
        """Test validation with thorough ruleset."""
        results = service.validate("loan", sample_loan, "thorough")
        assert isinstance(results, list)
        assert len(results) > 0

    def test_validate_bad_loan_returns_results(self, service, bad_loan):
        """Test that bad loan validation returns results."""
        results = service.validate("loan", bad_loan, "quick")
        assert isinstance(results, list)
        assert len(results) > 0

    def test_validate_bad_loan_fails_rule_002(self, service, bad_loan):
        """Test that bad loan fails rule_002_v1 (outstanding balance exceeds principal)."""
        results = service.validate("loan", bad_loan, "quick")

        # Find rule_002_v1 result
        rule_002_result = None
        for result in results:
            if result['rule_id'] == 'rule_002_v1':
                rule_002_result = result
                break

        assert rule_002_result is not None, "rule_002_v1 should be in results"
        assert rule_002_result['status'] == 'FAIL', "rule_002_v1 should fail for bad loan"
        assert 'balance' in rule_002_result['message'].lower(), \
            "Failure message should mention balance issue"

    def test_validate_good_vs_bad_loan(self, service, sample_loan, bad_loan):
        """Test that good loan passes rule_002_v1 but bad loan fails it."""
        good_results = service.validate("loan", sample_loan, "quick")
        bad_results = service.validate("loan", bad_loan, "quick")

        # Find rule_002_v1 in both results
        good_rule_002 = next((r for r in good_results if r['rule_id'] == 'rule_002_v1'), None)
        bad_rule_002 = next((r for r in bad_results if r['rule_id'] == 'rule_002_v1'), None)

        assert good_rule_002 is not None, "Good loan should have rule_002_v1 result"
        assert bad_rule_002 is not None, "Bad loan should have rule_002_v1 result"

        assert good_rule_002['status'] == 'PASS', "Good loan should pass rule_002_v1"
        assert bad_rule_002['status'] == 'FAIL', "Bad loan should fail rule_002_v1"


class TestBatchValidate:
    """Test batch_validate() method."""

    def test_batch_validate_single_entity(self, service, sample_loan):
        """Test batch validation with single entity."""
        results = service.batch_validate([sample_loan], ["id"], "quick")
        assert isinstance(results, list)
        assert len(results) == 1

    def test_batch_validate_multiple_entities(self, service, sample_loan):
        """Test batch validation with multiple entities."""
        loan2 = sample_loan.copy()
        loan2["id"] = "LOAN-00002"

        results = service.batch_validate([sample_loan, loan2], ["id"], "quick")
        assert len(results) == 2

    def test_batch_result_structure(self, service, sample_loan):
        """Test that batch results have expected structure."""
        results = service.batch_validate([sample_loan], ["id"], "quick")
        first_result = results[0]

        assert 'entity_id' in first_result
        assert 'entity_type' in first_result
        assert 'results' in first_result
        assert isinstance(first_result['results'], list)

    def test_batch_validates_each_entity(self, service, sample_loan):
        """Test that each entity in batch is validated."""
        loan2 = sample_loan.copy()
        loan2["id"] = "LOAN-00002"

        results = service.batch_validate([sample_loan, loan2], ["id"], "quick")

        assert results[0]['entity_id'] == 'LOAN-00001'
        assert results[1]['entity_id'] == 'LOAN-00002'


class TestBatchFileValidate:
    """Test batch_file_validate() method."""

    def test_batch_file_validate_local_file(self, service):
        """Test batch file validation with local file."""
        # Get path to sample_loans.json in tests directory
        test_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(test_dir, "sample_loans.json")
        file_uri = f"file://{file_path}"

        results = service.batch_file_validate(file_uri, ["loan"], ["id"], "quick")

        assert isinstance(results, list)
        assert len(results) == 2

    def test_batch_file_validate_result_structure(self, service):
        """Test that batch file results have expected structure."""
        test_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(test_dir, "sample_loans.json")
        file_uri = f"file://{file_path}"

        results = service.batch_file_validate(file_uri, ["loan"], ["id"], "quick")

        # Should have 2 results (good loan + bad loan)
        assert len(results) == 2

        # Each result should have expected structure
        for result in results:
            assert 'entity_id' in result
            assert 'entity_type' in result
            assert 'results' in result
            assert isinstance(result['results'], list)

    def test_batch_file_validate_identifies_bad_loan(self, service):
        """Test that batch file validation identifies the bad loan."""
        test_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(test_dir, "sample_loans.json")
        file_uri = f"file://{file_path}"

        results = service.batch_file_validate(file_uri, ["loan"], ["id"], "quick")

        # Find results for each loan
        good_loan_result = next(r for r in results if r['entity_id'] == 'LOAN-00001')
        bad_loan_result = next(r for r in results if r['entity_id'] == 'LOAN-99999')

        # Check that rule_002_v1 passes for good loan
        good_rule_002 = next(
            (r for r in good_loan_result['results'] if r['rule_id'] == 'rule_002_v1'),
            None
        )
        assert good_rule_002 is not None
        assert good_rule_002['status'] == 'PASS'

        # Check that rule_002_v1 fails for bad loan
        bad_rule_002 = next(
            (r for r in bad_loan_result['results'] if r['rule_id'] == 'rule_002_v1'),
            None
        )
        assert bad_rule_002 is not None
        assert bad_rule_002['status'] == 'FAIL'
        assert 'balance' in bad_rule_002['message'].lower()


class TestReloadLogic:
    """Test reload_logic() method."""

    def test_reload_logic_completes(self, service):
        """Test that reload_logic completes without error."""
        service.reload_logic()
        # If we get here without exception, test passes
        assert True

    def test_service_works_after_reload(self, service, sample_loan):
        """Test that service still works after reload."""
        service.reload_logic()

        # Should still be able to validate
        results = service.validate("loan", sample_loan, "quick")
        assert isinstance(results, list)
        assert len(results) > 0

    def test_multiple_reloads(self, service, sample_loan):
        """Test that multiple reloads work."""
        service.reload_logic()
        service.reload_logic()

        results = service.validate("loan", sample_loan, "quick")
        assert isinstance(results, list)


class TestGetCacheAge:
    """Test get_cache_age() method."""

    def test_get_cache_age_returns_value(self, service):
        """Test that get_cache_age returns a value."""
        age = service.get_cache_age()
        # For local bundled logic, should be None
        # For remote logic, should be a float
        assert age is None or isinstance(age, (int, float))

    def test_cache_age_after_reload(self, service):
        """Test cache age after reload."""
        age_before = service.get_cache_age()
        service.reload_logic()
        age_after = service.get_cache_age()

        # Both should be same type (None for local mode)
        assert type(age_before) == type(age_after)


class TestErrorHandling:
    """Test error handling in API."""

    def test_validate_with_invalid_entity_type(self, service, sample_loan):
        """Test validation with invalid entity type."""
        # System handles gracefully - returns empty results for unknown entity types
        results = service.validate("invalid_type", sample_loan, "quick")
        assert isinstance(results, list)

    def test_validate_with_invalid_ruleset(self, service, sample_loan):
        """Test validation with invalid ruleset."""
        # System handles gracefully - returns empty results for unknown rulesets
        results = service.validate("loan", sample_loan, "invalid_ruleset")
        assert isinstance(results, list)

    def test_validate_without_schema(self, service):
        """Test validation with entity missing $schema."""
        loan_no_schema = {
            "id": "TEST-001",
            "loan_number": "LN-001"
        }

        # Should still work with fallback to default helper
        results = service.validate("loan", loan_no_schema, "quick")
        assert isinstance(results, list)


class TestNotesField:
    """Test the structured notes array field introduced in schema v1.0.0 (updated)."""

    @pytest.fixture
    def loan_with_notes(self, sample_loan):
        """Loan carrying two notes entries: one plain note and one operation-typed entry."""
        import copy
        loan = copy.deepcopy(sample_loan)
        loan["id"] = "LOAN-00001"  # must match ^LOAN-[0-9]+$
        loan["notes"] = [
            {
                "datetime": "2024-03-01T09:00:00Z",
                "operation_type": "note",
                "text": "Initial review completed. All documentation received."
            },
            {
                "datetime": "2024-03-15T14:30:00Z",
                "operation_type": "edited",
                "text": "Interest rate updated following rate reset clause."
            }
        ]
        return loan

    @pytest.fixture
    def loan_with_notes_no_op_type(self, sample_loan):
        """Loan with a notes entry that omits the optional operation_type."""
        import copy
        loan = copy.deepcopy(sample_loan)
        loan["id"] = "LOAN-00002"  # must match ^LOAN-[0-9]+$
        loan["notes"] = [
            {
                "datetime": "2024-06-01T10:00:00Z",
                "text": "Borrower requested repayment schedule review."
            }
        ]
        return loan

    @pytest.fixture
    def loan_with_string_notes(self, sample_loan):
        """Loan using the old freeform string notes field — should fail schema validation."""
        import copy
        loan = copy.deepcopy(sample_loan)
        loan["id"] = "LOAN-00003"  # must match ^LOAN-[0-9]+$
        loan["notes"] = "Some old-style freeform note"
        return loan

    def test_loan_with_notes_passes_schema(self, service, loan_with_notes):
        """Loan with a valid notes array must pass rule_001 (schema validation)."""
        results = service.validate("loan", loan_with_notes, "quick")
        rule_001 = next((r for r in results if r['rule_id'] == 'rule_001_v1'), None)
        assert rule_001 is not None, "rule_001_v1 should be present"
        assert rule_001['status'] == 'PASS', (
            f"Schema validation should pass for a valid notes array; got: {rule_001.get('message')}"
        )

    def test_loan_with_notes_passes_all_rules(self, service, loan_with_notes):
        """Loan with a valid notes array should pass the full thorough ruleset."""
        results = service.validate("loan", loan_with_notes, "thorough")
        failures = [r for r in results if r['status'] == 'FAIL']
        assert failures == [], f"Expected no failures, got: {failures}"

    def test_loan_with_notes_no_op_type_passes_schema(self, service, loan_with_notes_no_op_type):
        """Notes entry without operation_type (optional field) must still pass schema."""
        results = service.validate("loan", loan_with_notes_no_op_type, "quick")
        rule_001 = next((r for r in results if r['rule_id'] == 'rule_001_v1'), None)
        assert rule_001 is not None
        assert rule_001['status'] == 'PASS', (
            f"Notes entry missing optional operation_type should still pass schema; "
            f"got: {rule_001.get('message')}"
        )

    def test_loan_with_string_notes_fails_schema(self, service, loan_with_string_notes):
        """Old freeform string notes must fail rule_001 schema validation."""
        results = service.validate("loan", loan_with_string_notes, "quick")
        rule_001 = next((r for r in results if r['rule_id'] == 'rule_001_v1'), None)
        assert rule_001 is not None, "rule_001_v1 should be present"
        assert rule_001['status'] == 'FAIL', (
            "String notes field should fail schema validation under the new array definition"
        )


class TestEndToEnd:
    """End-to-end integration tests."""

    def test_complete_workflow(self, service, sample_loan):
        """Test a complete validation workflow."""
        # 1. Discover available rulesets
        rulesets = service.discover_rulesets()
        assert len(rulesets) > 0

        # 2. Discover rules for an entity
        rules = service.discover_rules("loan", sample_loan, "quick")
        assert len(rules) > 0

        # 3. Validate the entity
        results = service.validate("loan", sample_loan, "quick")
        assert len(results) > 0

        # 4. Check validation passed
        passed = any(r['status'] == 'PASS' for r in results)
        assert passed

    def test_batch_workflow(self, service, sample_loan):
        """Test batch validation workflow."""
        # Create multiple loans
        loans = []
        for i in range(1, 4):
            loan = sample_loan.copy()
            loan["id"] = f"LOAN-{i:05d}"
            loans.append(loan)

        # Batch validate
        results = service.batch_validate(loans, ["id"], "quick")

        # Should have result for each entity
        assert len(results) == 3

        # Each should have validation results
        for result in results:
            assert len(result['results']) > 0
