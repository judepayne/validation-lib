# Plugins

Plugins are source-format adapters. They convert one raw input item into one canonical entity dict containing `$schema`, then validation-lib runs the normal schema/rule flow.

Plugins are useful when a team cannot easily produce the canonical JSON shape required by validation rules. The team can provide a small Python adapter in `validation-logic/plugins/` instead.

---

## Non-goals

Plugins are not validation rules. They do not participate in rule hierarchy, rule discovery, rule statuses, or rule ownership.

Plugins do not parse whole datasets or files. A plugin converts exactly one input item into one canonical entity. Dataset parsing, CSV splitting, XML multi-record parsing, Parquet reading, and streaming ingestion remain outside validation-lib.

---

## Plugin interface

Every plugin file must define a class named exactly `Plugin`:

```python
from plugins.base import PluginError, ValidationPlugin


class Plugin(ValidationPlugin):
    def convert(self, input_data):
        if not isinstance(input_data, dict):
            raise PluginError("Vendor payload must be a dict")

        return {
            "$schema": "https://raw.githubusercontent.com/judepayne/validation-logic/main/models/loan.schema.v1.0.0.json",
            "id": input_data["vendor_id"],
            "loan_number": input_data["loan_ref"],
            "facility_id": input_data["facility_ref"],
            "financial": {
                "principal_amount": input_data["amount"],
                "currency": input_data["currency"],
                "interest_rate": input_data["rate"],
            },
            "dates": {
                "origination_date": input_data["origination"],
                "maturity_date": input_data["maturity"],
            },
            "status": input_data["status"],
        }
```

`convert()` receives only the raw input payload. It does not receive the ruleset, config loader, validation engine, coordination data, or any validation context. This keeps plugins reusable outside validation-lib.

The plugin output must be a dict and must include `$schema`. The existing validation flow uses `$schema` for schema/helper/rule routing.

---

## Plugin errors

Raise `PluginError` when source data cannot be converted:

```python
if "amount" not in input_data:
    raise PluginError("Vendor payload missing required field: amount")
```

Validation-lib catches conversion failures and returns object-level `PLUGIN_FAIL`:

```python
{
    "status": "PLUGIN_FAIL",
    "entity_type": "loan",
    "ruleset": "quick",
    "results": [],
    "plugin_name": "vendor_x_loan",
    "plugin_message": "Vendor payload missing required field: amount"
}
```

Validation-lib also returns `PLUGIN_FAIL` with standard messages if a plugin returns a non-dict or a dict missing `$schema`.

---

## Configuration

Plugins are declared in `validation-logic/business-config.yaml`:

```yaml
plugins:
  vendor_x_loan:
    file: plugins/vendor_x_loan.py
    entity_type: loan
```

`entity_type` identifies the canonical entity type the plugin produces. Plugins do not declare a target schema; the returned entity’s `$schema` controls version-specific routing.

`plugins/base.py` and `plugins/__init__.py` must be listed in `structural_files` so remote logic fetching caches the base interface.

---

## Calling plugins

Single object:

```python
response = service.validate(
    "loan",
    vendor_payload,
    "quick",
    plugin_name="vendor_x_loan",
)
```

Batch:

```python
response = service.batch_validate(
    [
        {"correlation_id": "row-001", "data": vendor_payload_1},
        {"correlation_id": "row-002", "data": vendor_payload_2},
    ],
    "quick",
    plugin_name="vendor_x_loan",
)
```

Only one plugin can be specified per validation call. If a caller has multiple source formats, it should split them into separate calls.

---

## JSONL batch files

`batch_file_validate()` supports JSONL as a simple item container:

```jsonl
{"correlation_id": "row-001", "data": {"vendor_id": "LOAN-00001", "amount": 100000}}
{"correlation_id": "row-002", "data": "<Loan><Id>LOAN-00002</Id></Loan>"}
```

Each line is one item. The plugin receives the `data` value for that item.

---

## Trust model

Plugins are executable Python loaded from trusted `validation-logic`, the same trust boundary as rules. Do not load plugins from untrusted sources.
