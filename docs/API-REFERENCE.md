# API Reference

`ValidationService` is the single public entry point for all validation operations. Import it from the package root:

```python
from validation_lib import ValidationService

service = ValidationService()
```

The constructor loads the bundled `local-config.yaml`, fetches business logic from the configured URI, and populates the local cache. If the on-disk cache already exists and is fresh enough (see [Configuration](CONFIGURATION.md)), the fetch is skipped.

---

## Methods

### `validate(entity_type, entity_data, ruleset_name) → List[Dict]`

Validate a single entity against a named ruleset.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `entity_type` | `str` | Entity type, e.g. `"loan"` |
| `entity_data` | `dict` | Entity data dict; must include a `"$schema"` field |
| `ruleset_name` | `str` | Ruleset to run, e.g. `"quick"` or `"thorough"` |

**Returns** a list of rule result dicts, one per top-level rule:

```python
[
    {
        "rule_id": "rule_001_v1",
        "description": "JSON Schema validation",
        "status": "PASS",          # PASS | WARN | FAIL | NORUN | ERROR
        "message": "",
        "execution_time_ms": 12.5,
        "children": [...]          # nested results for child rules
    },
    ...
]
```

**Example**

```python
results = service.validate("loan", {
    "$schema": "https://example.com/schemas/loan/v1.0.0",
    "id": "LOAN-001",
    "financial": {"principal_amount": 100000, "interest_rate": 0.045},
    ...
}, "quick")

failures = [r for r in results if r["status"] == "FAIL"]
```

---

### `batch_validate(entities, id_fields, ruleset_name) → List[Dict]`

Validate multiple entities in a single call.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `entities` | `list` | List of entity dicts, each with a `"$schema"` field |
| `id_fields` | `list` | Field name(s) used to identify each entity in results, e.g. `["id"]` |
| `ruleset_name` | `str` | Ruleset to run |

**Returns** a list of per-entity result dicts:

```python
[
    {
        "entity_id": "LOAN-001",
        "entity_type": "loan",
        "results": [...]    # same structure as validate()
    },
    ...
]
```

**Example**

```python
results = service.batch_validate(
    [loan1, loan2, loan3],
    id_fields=["id"],
    ruleset_name="quick"
)
```

---

### `batch_file_validate(file_uri, entity_types, id_fields, ruleset_name) → List[Dict]`

Validate entities loaded from a file URI.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `file_uri` | `str` | URI of a JSON file — `file://`, `http://`, or `https://` |
| `entity_types` | `list` | Entity types present in the file, e.g. `["loan"]` |
| `id_fields` | `list` | Field name(s) used to identify each entity |
| `ruleset_name` | `str` | Ruleset to run |

**Returns** the same per-entity list as `batch_validate()`.

**Example**

```python
results = service.batch_file_validate(
    "file:///data/loans.json",
    entity_types=["loan"],
    id_fields=["id"],
    ruleset_name="thorough"
)
```

---

### `discover_rules(entity_type, entity_data, ruleset_name) → Dict`

Return metadata for every rule applicable to a given entity type and ruleset.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `entity_type` | `str` | Entity type |
| `entity_data` | `dict` | Entity data dict (used to determine schema version; only `"$schema"` is needed) |
| `ruleset_name` | `str` | Ruleset to inspect |

**Returns** a dict mapping `rule_id` → metadata:

```python
{
    "rule_001_v1": {
        "rule_id": "rule_001_v1",
        "entity_type": "loan",
        "description": "JSON Schema validation",
        "required_data": [],
        "field_dependencies": [
            ["principal", "financial.principal_amount"],
            ...
        ],
        "applicable_schemas": ["https://example.com/schemas/loan/v1.0.0"]
    },
    ...
}
```

`field_dependencies` is populated by running each rule with access-tracking enabled and recording which logical properties (and their physical paths) were touched.

**Example**

```python
rules = service.discover_rules("loan", {"$schema": schema_url}, "thorough")
for rule_id, meta in rules.items():
    print(f"{rule_id}: {meta['description']}")
    print(f"  Fields: {meta['field_dependencies']}")
```

---

### `discover_rulesets() → Dict`

Return metadata and statistics for all configured rulesets.

**Returns**

```python
{
    "quick": {
        "metadata": {
            "description": "Essential checks for real-time validation",
            "purpose": "...",
            "author": "...",
            "date": "..."
        },
        "stats": {
            "total_rules": 2,
            "supported_entities": ["loan"],
            "supported_schemas": ["https://example.com/schemas/loan/v1.0.0"]
        }
    },
    "thorough": { ... }
}
```

**Example**

```python
rulesets = service.discover_rulesets()
for name, info in rulesets.items():
    print(f"{name}: {info['metadata']['description']} ({info['stats']['total_rules']} rules)")
```

---

### `reload_logic() → None`

Force an immediate re-fetch of all business logic from the configured source, replacing the local cache. Use this to pick up rule changes without restarting the host process.

```python
service.reload_logic()
```

After `reload_logic()` returns, all subsequent validation calls use the freshly downloaded logic.

---

### `get_cache_age() → Optional[float]`

Return the age of the local logic cache in seconds, or `None` if no cache exists.

```python
age = service.get_cache_age()
if age is not None:
    print(f"Cache is {age / 60:.0f} minutes old")
```

---

## Rule result statuses

| Status | Meaning |
|---|---|
| `PASS` | Rule ran and the check passed. Child rules will run. |
| `WARN` | Rule ran and found a advisory condition. Child rules still run. |
| `FAIL` | Rule ran and the check failed. |
| `NORUN` | Rule was skipped — either a parent rule did not pass, or required data was unavailable. |
| `ERROR` | Rule raised an unhandled exception. The `message` field contains the traceback. |
