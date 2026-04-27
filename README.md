# validation-lib

Python business data validation library with dynamic rule loading.

## Overview

validation-lib provides a flexible, config-driven validation framework for business data expressed as JSON. Rules extend well beyond JSON Schema validation — any arbitrary Python logic can be a rule, with a clear interface and entity helper abstraction layer that decouples rules from the physical data model. Business logic (`validation-logic`) is intentionally separated from the library itself, allowing the rules team and the service team to own and deploy their assets independently. Optional input adapter plugins can convert team/vendor-specific source formats into canonical entity JSON before validation runs.

## Features

- **Dynamic rule loading** — rules loaded from local paths or remote URLs, cached locally
- **Two-tier configuration** — infrastructure config separate from business logic config
- **JSON Schema validation** — first-class schema support as a built-in rule type
- **Custom Python rules** — arbitrary business logic, clean interface
- **Entity helpers** — logical field abstraction; rules are insulated from physical schema changes
- **Plugin input adapters** — optional one-item converters for non-canonical source formats
- **Schema versioning** — multiple schema versions coexist; rules route automatically
- **Hot reload** — update rules, helpers, schemas, and plugins without restarting the host application
- **Parallel batch validation** — `ProcessPoolExecutor` worker pool for `batch_validate()`; opt-in via `local-config.yaml`, near-linear throughput scaling with CPU count
- **JSONL batch file support** — validate JSON or JSONL item containers
- **JSON-RPC server** — use from any language over stdin/stdout

## Installation

```bash
pip install git+https://github.com/judepayne/validation-lib.git
```

For local development:

```bash
git clone https://github.com/judepayne/validation-lib.git
cd validation-lib && pip install -e .
```

## Quick Start

```python
from validation_lib import ValidationService

service = ValidationService()

response = service.validate("loan", {
    "$schema": "https://example.com/schemas/loan/v1.0.0",
    "id": "LOAN-001",
    "financial": {"principal_amount": 100000, "interest_rate": 0.045, ...},
    ...
}, "quick")

print(response["status"])
for result in response["results"]:
    print(f"{result['rule_id']}: {result['status']} — {result['message']}")

# With an input adapter plugin:
response = service.validate(
    "loan",
    vendor_payload,
    "quick",
    plugin_name="vendor_x_loan",
)
```

## Documentation

| Document | Contents |
|---|---|
| [API Reference](docs/API-REFERENCE.md) | All `ValidationService` methods — parameters, return types, examples |
| [Configuration](docs/CONFIGURATION.md) | Two-tier config system, local vs remote logic, cache behaviour |
| [Technical Design](docs/TECHNICAL-DESIGN.md) | Architecture, module map, validation flow, entity helper system |
| [Rules Guide](docs/RULES-GUIDE.md) | Writing rules, statuses, entity helpers, hierarchy, schema versioning |
| [Plugins](docs/PLUGINS.md) | Writing source-format adapter plugins and handling `PLUGIN_FAIL` |
| [JSON-RPC Server](docs/JSONRPC-SERVER.md) | Running the server, protocol, error codes, client examples |
| [Production](docs/PRODUCTION.md) | Deployment patterns, coordination service, performance, security |

## License

MIT © Jude Payne 2026
