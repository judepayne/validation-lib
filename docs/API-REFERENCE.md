# API Reference

`ValidationService` is the single public entry point for all validation operations:

```python
from validation_lib import ValidationService

service = ValidationService()
```

The constructor loads bundled infrastructure config, resolves `validation-logic`, adds it to the runtime import path, and initializes rule and plugin loaders.

---

## Methods

### `validate(entity_type, entity_data, ruleset_name, plugin_name=None) → Dict`

Validate one object against a named ruleset.

If `plugin_name` is omitted, `entity_data` must be canonical entity JSON. If `plugin_name` is supplied, `entity_data` is raw source data and the named plugin converts it into canonical entity JSON before rules run.

| Name | Type | Description |
|---|---|---|
| `entity_type` | `str` | Target entity type, e.g. `"loan"`. |
| `entity_data` | `Any` | Canonical entity dict, or raw plugin input when `plugin_name` is supplied. |
| `ruleset_name` | `str` | Ruleset to run, e.g. `"quick"` or `"thorough"`. |
| `plugin_name` | `Optional[str]` | Optional source-format adapter registered in `business-config.yaml`. |

Returns an object-level envelope:

```python
{
    "status": "PASS",          # PASS | WARN | FAIL | NORUN | ERROR | PLUGIN_FAIL
    "entity_type": "loan",
    "ruleset": "quick",
    "results": [
        {
            "rule_id": "rule_001_v1",
            "description": "Entity data must conform to its declared JSON schema",
            "status": "PASS",  # rule-level PASS | WARN | FAIL | NORUN | ERROR
            "message": "",
            "execution_time_ms": 12.5,
            "children": []
        }
    ]
}
```

When plugin conversion fails, rules do not run:

```python
{
    "status": "PLUGIN_FAIL",
    "entity_type": "loan",
    "ruleset": "quick",
    "results": [],
    "plugin_name": "vendor_x_loan",
    "plugin_message": "Vendor loan payload missing required field(s): amount"
}
```

Example:

```python
response = service.validate("loan", loan_data, "quick")
if response["status"] == "FAIL":
    print("At least one rule failed")
```

Plugin example:

```python
response = service.validate(
    "loan",
    vendor_payload,
    "quick",
    plugin_name="vendor_x_loan",
)
```

---

### `batch_validate(items, ruleset_name, plugin_name=None) → Dict`

Validate multiple independent items. Each item is processed separately; one item failing validation or plugin conversion does not stop later items. Unexpected per-item validation exceptions are returned as item-level `ERROR` envelopes with `error_message`.

| Name | Type | Description |
|---|---|---|
| `items` | `list` | List of item dicts containing `data` and optional `correlation_id`. |
| `ruleset_name` | `str` | Ruleset to run for all items. |
| `plugin_name` | `Optional[str]` | Optional plugin applied independently to every item. |

Recommended item shape:

```python
{
    "correlation_id": "row-001",
    "data": raw_or_canonical_payload,
}
```

`correlation_id` is caller-provided and opaque. It is echoed in the corresponding result item so callers can match output back to input, even when plugin conversion fails. If omitted, validation-lib synthesizes `item-1`, `item-2`, etc.

Returns a batch envelope:

```python
{
    "status": "COMPLETED",
    "ruleset": "quick",
    "plugin_name": "vendor_x_loan",  # present only when supplied
    "items": [
        {
            "correlation_id": "row-001",
            "status": "PASS",
            "entity_type": "loan",
            "ruleset": "quick",
            "results": [...]
        },
        {
            "correlation_id": "row-002",
            "status": "PLUGIN_FAIL",
            "entity_type": "loan",
            "ruleset": "quick",
            "results": [],
            "plugin_name": "vendor_x_loan",
            "plugin_message": "Plugin output missing required $schema field"
        }
    ]
}
```

Example:

```python
response = service.batch_validate(
    [
        {"correlation_id": "row-1", "data": loan1},
        {"correlation_id": "row-2", "data": loan2},
    ],
    "quick",
)
```

---

### `batch_file_validate(file_uri, ruleset_name, plugin_name=None) → Dict`

Load items from a JSON or JSONL file and delegate to `batch_validate()`.

| Name | Type | Description |
|---|---|---|
| `file_uri` | `str` | URI of a `.json` or `.jsonl` file — `file://`, `http://`, or `https://`. |
| `ruleset_name` | `str` | Ruleset to run. |
| `plugin_name` | `Optional[str]` | Optional plugin applied independently to each loaded item. |

Supported formats:

- `.json`: one item, a list of items, one canonical/source object, or a list of objects.
- `.jsonl`: one JSON object per nonblank line.

JSONL example:

```jsonl
{"correlation_id": "row-001", "data": {"vendor_id": "LOAN-00001", "amount": 100000}}
{"correlation_id": "row-002", "data": "<Loan><Id>LOAN-00002</Id></Loan>"}
```

If a loaded object lacks `data`, validation-lib wraps it as `{"data": object}`. Missing correlation IDs are synthesized (`item-N` for JSON, `line-N` for JSONL).

Example:

```python
response = service.batch_file_validate(
    "file:///data/vendor-loans.jsonl",
    "quick",
    plugin_name="vendor_x_loan",
)
```

---

### `discover_rules(entity_type, entity_data, ruleset_name) → Dict`

Return metadata for every rule applicable to a given entity type and ruleset. `entity_data` should be canonical entity data, or a minimal dict containing `$schema` for schema-version routing.

---

### `discover_rulesets() → Dict`

Return metadata and statistics for all configured rulesets.

---

### `reload_logic() → None`

Force an immediate re-fetch of all business logic from the configured source, replacing the local cache. This also clears dynamic imports for rules, helpers, schemas, and plugins so updated code is loaded on subsequent calls.

---

### `close() → None`

Shut down the worker process pool and release worker processes immediately. Safe to call multiple times and safe when batch parallelism is disabled.

---

### `get_cache_age() → Optional[float]`

Return the age of the local logic cache in seconds, or `None` if no cache exists.

---

## Object-level statuses

| Status | Meaning |
|---|---|
| `PASS` | All rule results passed. |
| `WARN` | At least one rule returned `WARN`, and no rule failed or errored. |
| `FAIL` | At least one rule returned `FAIL`. |
| `NORUN` | No rules ran, or the highest rule status is `NORUN`. |
| `ERROR` | At least one rule raised an unhandled exception, or a batch item hit an unexpected validation exception. Batch item `ERROR` envelopes may include `error_message`. |
| `PLUGIN_FAIL` | Plugin conversion failed; validation rules did not run. |

Rule-level statuses remain `PASS`, `WARN`, `FAIL`, `NORUN`, and `ERROR`. `PLUGIN_FAIL` exists only at the object level.
