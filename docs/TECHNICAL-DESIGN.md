# Technical Design

This document describes the internal architecture of validation-lib: how the modules relate, how a validation call flows through the system, and how the entity helper and caching systems work.

---

## Architecture overview

```
Your Application
      │
      │  from validation_lib import ValidationService
      ▼
┌─────────────────────────────────────────────────┐
│  ValidationService  (api.py)                    │
│  • public entry point                           │
│  • mid-session staleness checks                 │
│  • coordinates config, fetcher, engine, proxy   │
└────┬────────────────────────────┬───────────────┘
     │                            │
     ▼                            ▼
ConfigLoader              LogicPackageFetcher
(config_loader.py)        (logic_fetcher.py)
• loads local-config.yaml • fetches logic/ from
• loads business-config     local path or remote URL
• caches remote configs   • caches to /tmp/validation-lib/logic/
     │
     ▼
ValidationEngine  (validation_engine.py)
• rule routing (schema URL → ruleset → rule list)
• entity helper injection
• orchestrates rule loading and execution
     │                    │
     ▼                    ▼
RuleLoader           RuleExecutor
(rule_loader.py)     (rule_executor.py)
• discovers rule     • runs rules hierarchically
  files by ID        • handles parent-child dependencies
• dynamic import     • captures timing per rule
• caches classes     • returns structured results
     │
     ▼
RuleFetcher (rule_fetcher.py)
• fetches individual rule files
  from URI (local path or https://)
• SHA256-keyed disk cache
```

The **coordination proxy** (`coordination_proxy.py`) sits alongside `ValidationEngine` and is responsible for fetching external data (parent entities, reference data) needed by rules that declare `required_data`. It is currently stubbed — see [Production](PRODUCTION.md).

---

## Module map

| Module | Responsibility |
|---|---|
| `api.py` | `ValidationService` — public API, auto-refresh, batching, file loading |
| `config_loader.py` | Two-tier config loading; caches remote configs by URI hash |
| `logic_fetcher.py` | `LogicPackageFetcher` — fetches and caches the full `logic/` package |
| `validation_engine.py` | `ValidationEngine` — rule routing, entity helper injection, discover/validate orchestration |
| `rule_loader.py` | `RuleLoader` — dynamic import of `Rule` classes by rule ID |
| `rule_executor.py` | `RuleExecutor` — hierarchical rule execution with timing |
| `rule_fetcher.py` | `RuleFetcher` — URI-based fetch and cache of individual rule `.py` files |
| `coordination_proxy.py` | `CoordinationProxy` — stub for fetching cross-entity data |
| `jsonrpc_server.py` | JSON-RPC 2.0 server wrapping `ValidationService` |
| `__init__.py` | Package entry point — exports `ValidationService` |
| `__main__.py` | `python -m validation_lib.jsonrpc_server` entry point |

---

## Validation flow

A call to `service.validate("loan", entity_data, "quick")` follows this path:

```
1. ValidationService.validate()
   │
   ├─ Mid-session staleness check (debounced to every 5 min)
   │
   ├─ CoordinationProxy.get_associated_data()   ← Phase 1: get required data
   │    Currently returns {} (stub)
   │
   └─ ValidationEngine.validate()               ← Phase 2: execute rules
        │
        ├─ _determine_entity_type(entity_data)
        │    Reads "$schema" field → looks up schema_to_helper_mapping
        │    Falls back to default_helpers if "$schema" absent
        │
        ├─ _get_rules_for_ruleset(schema_url, ruleset_name)
        │    Looks up schema URL key in ruleset rules map
        │    Falls back to entity type key if not found
        │
        ├─ create_entity_helper(entity_type, entity_data, track_access=False)
        │    VersionRegistry resolves schema → helper class → instantiates
        │
        └─ RuleExecutor.execute(rule_configs, entity_helper, required_data)
             │
             For each rule in the config list:
             ├─ RuleLoader.load_rule(rule_id, entity_type)
             │    → finds rule file, imports module, returns Rule class
             │
             ├─ rule.run()  with timing
             │
             ├─ If PASS or WARN → recurse into children
             └─ If FAIL, NORUN, or ERROR → mark children NORUN
```

**Result structure** (one dict per rule, nested for children):

```python
{
    "rule_id": "rule_003_v1",
    "description": "Status validation",
    "status": "PASS",
    "message": "",
    "execution_time_ms": 1.3,
    "children": [
        {
            "rule_id": "rule_004_v1",
            "description": "Balance constraints",
            "status": "FAIL",
            "message": "Paid-off loan must have zero outstanding balance",
            "execution_time_ms": 0.8,
            "children": []
        }
    ]
}
```

Note: child rule failures are **nested inside the parent's result dict**, not present at the top level of the results list. Code that inspects results must recurse into `children` to find all failures.

---

## Entity helper system

### The problem

Rules that access raw JSON paths (`entity_data["financial"]["principal_amount"]`) break whenever the data model is restructured. With many rules, a single field rename cascades into many code changes.

### The solution

Entity helpers provide **stable logical properties** that map to physical JSON paths. Rules always access data via the helper, never via raw dict access:

```python
# In a rule — stable across all schema versions:
if self.entity.principal <= 0:
    return ("FAIL", "Principal must be positive")
```

### Version-specific helpers

Each major schema version has its own helper class. The mapping lives in `business-config.yaml`:

```yaml
schema_to_helper_mapping:
  "https://.../loan.schema.v1.0.0.json": "loan_v1"
  "https://.../loan.schema.v2.0.0.json": "loan_v2"
```

Example mappings for `LoanV1` (schema v1.0.0):

| Logical property | Physical path |
|---|---|
| `reference` | `loan_number` |
| `facility` | `facility_id` |
| `principal` | `financial.principal_amount` |
| `balance` | `financial.outstanding_balance` |
| `rate` | `financial.interest_rate` |
| `inception` | `dates.origination_date` |
| `maturity` | `dates.maturity_date` |
| `status` | `status` |

If the schema evolves (e.g. `loan_number` → `reference_number`), only `loan_v2.py` changes — rules are untouched.

### Version registry

`VersionRegistry` (a singleton in `entity_helpers/version_registry.py`) routes entity data to the correct helper class. Resolution order:

1. Exact `$schema` URL match in `schema_to_helper_mapping`
2. Minor version fallback: `v1.2.0` → nearest `v1.x.0` mapping (if `allow_minor_version_fallback: true`)
3. Default helper by entity type (when `$schema` is absent)
4. `ValueError` if no match

### Field access tracking

Helpers optionally record which properties a rule accessed during execution — used by `discover_rules()` to populate `field_dependencies`:

```python
helper = create_entity_helper("loan", entity_data, track_access=True)
# ... rule executes ...
deps = helper.get_accesses()
# [("principal", "financial.principal_amount"), ("balance", "financial.outstanding_balance")]
```

This enables model-change impact analysis: "which rules would break if we rename this field?"

---

## Logic package fetching

`LogicPackageFetcher` resolves the `logic/` directory at startup, either locally or remotely.

**Local mode** (`business_config_uri` is a path): resolves to an absolute directory on disk and uses it directly. No network calls, no caching. Suitable for development.

**Remote mode** (`business_config_uri` is `http://` or `https://`):

1. Fetches `business-config.yaml` from the URL
2. Computes the base URI (strips the filename)
3. Reads `structural_files` from the config
4. Derives additional file paths from ruleset rule IDs and `schema_to_helper_mapping`
5. Fetches each file and writes it to `/tmp/validation-lib/logic/`, mirroring the remote structure
6. Sets `logic_dir` to the cache root so `sys.path` and all imports work unchanged

The `STRUCTURAL_FILES` list in `logic_fetcher.py` is a hard-coded manifest of files that must always be fetched regardless of the config (base classes, `__init__` modules). It must be updated whenever a new structural file is added to `validation-logic`.

---

## `sys.path` and dynamic imports

Because `validation-logic` is not a Python package (no `setup.py`, not pip-installable), the library adds `logic_dir` to `sys.path` at engine initialisation time. This allows rules and helpers to import from each other naturally:

```python
# Inside a rule file — resolved at runtime via sys.path:
from rules.base import ValidationRule
from entity_helpers import create_entity_helper
from schema_helpers import load_schema
```

These imports are **unresolvable at static analysis time** — LSP errors about `entity_helpers` and `rules.*` are expected and harmless. They resolve correctly at runtime once `sys.path` is configured.

On `reload_logic()`, the engine cleans up stale `sys.path` entries and invalidates cached modules in `sys.modules` so the fresh logic is imported cleanly.
