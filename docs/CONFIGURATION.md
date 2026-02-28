# Configuration

validation-lib uses a **two-tier configuration system** that separates infrastructure concerns (owned by the service team) from business logic concerns (owned by the rules team). Each tier can live locally or be served from a remote URL, enabling independent deployment of each.

---

## Tier 1 — Local config (`local-config.yaml`)

Bundled with the library at `validation_lib/local-config.yaml`. Owned by the **service team**. Points to the business config and coordination service config.

```yaml
# URI to business config (tier 2)
# Supports: relative paths, file://, http://, https://
business_config_uri: "https://raw.githubusercontent.com/judepayne/validation-logic/main/business-config.yaml"

# URI to coordination service config
coordination_service_config_uri: "coordination-service-config.yaml"

# Cache TTL: auto-refresh if logic cache is older than this (seconds)
logic_cache_max_age_seconds: 1800
```

### Batch parallelism

Two optional keys control parallel batch validation:

```yaml
# Enable parallel batch processing (default: false — opt-in)
batch_parallelism: false

# Number of worker processes (omit or null = os.cpu_count(); ignored when batch_parallelism is false)
batch_max_workers: 4
```

When `batch_parallelism: true`, `batch_validate()` and `batch_file_validate()` distribute entities across a `ProcessPoolExecutor` worker pool. Each worker runs its own `ValidationService` instance, so there is no shared state between workers. Results are always returned in input order.

Set `batch_parallelism: false` (the default) to use the original sequential code path with no worker processes — useful for debugging, environments where multiprocessing is unavailable, or workloads where batches are consistently small.

See [Production — Batch parallelism](PRODUCTION.md) for performance characteristics and lifecycle notes.

---

The `business_config_uri` is the key indirection point. It can point to:

| Value | Mode | Use case |
|---|---|---|
| `"../logic/business-config.yaml"` | Relative path | Local development (logic/ alongside the library) |
| `"file:///opt/validation/logic/business-config.yaml"` | Absolute path | Mounted volume, CI |
| `"https://cdn.example.com/logic/business-config.yaml"` | Remote URL | Production — logic team deploys independently |

When `business_config_uri` is a remote URL, the library derives the base URI from it and fetches the entire logic package (rules, helpers, schemas) into a local cache. See [Logic fetching and caching](#logic-fetching-and-caching) below.

---

## Tier 2 — Business config (`business-config.yaml`)

Lives inside the `logic/` directory. Owned by the **rules team**. Defines rulesets, rule assignments, and schema-to-helper mappings.

```yaml
# Base URI for remote logic files (omit for local development)
logic_base_uri: "https://raw.githubusercontent.com/judepayne/validation-logic/main"

# Files always required regardless of which rules are active
structural_files:
  - rules/base.py
  - entity_helpers/__init__.py
  - entity_helpers/version_registry.py
  - entity_helpers/read.py
  - entity_helpers/write.py
  - entity_helpers/convert.py
  - entity_helpers/conversions.py
  - schema_helpers/__init__.py
  - schema_helpers/schema_loader.py

# Rulesets — named groups of rules with metadata
rulesets:
  quick:
    metadata:
      description: "Essential validation checks for real-time inline validation"
      purpose: "Use during loan origination to catch critical errors before submission"
      author: "Data Quality Team"
      date: "2026-02-18"
    rules:
      "https://.../models/loan.schema.v1.0.0.json":
        - rule_id: rule_001_v1
        - rule_id: rule_002_v1
        - rule_id: rule_005_v1
      loan:                     # Fallback when $schema is absent
        - rule_id: rule_001_v1
        - rule_id: rule_002_v1

  thorough:
    rules:
      "https://.../models/loan.schema.v1.0.0.json":
        - rule_id: rule_001_v1
        - rule_id: rule_002_v1
        - rule_id: rule_003_v1
          children:
            - rule_id: rule_004_v1  # Only runs if rule_003 passes
        - rule_id: rule_005_v1

# Schema URL → entity helper class mapping
schema_to_helper_mapping:
  "https://.../models/loan.schema.v1.0.0.json": "loan_v1"
  "https://.../models/loan.schema.v2.0.0.json": "loan_v2"

# Fallback helper when $schema is absent
default_helpers:
  loan: "loan_v1"

# Schema version compatibility
version_compatibility:
  allow_minor_version_fallback: true   # v1.1.0 falls back to v1.0.0 helper
  strict_major_version: true           # unknown major version → error
```

### Rule routing

When `validate()` is called, the engine resolves which rules to run in this order:

1. Extract the `$schema` URL from `entity_data`
2. Look up the URL as a key in the active ruleset's `rules` map
3. If not found, fall back to the entity type key (e.g. `"loan"`)
4. If still not found, raise `ValueError`

This means you can have version-specific rule sets — the rules run against a v2 schema entity can differ from those run against a v1 entity — just by adding the new schema URL as a key.

---

## Tier 3 — Coordination service config

A separate YAML file referenced from `local-config.yaml` via `coordination_service_config_uri`. Like the business config, it can be local or remote — useful for centralising endpoint configuration across multiple deployments.

```yaml
enabled: false                    # Set to true when service is available
base_url: "http://localhost:8081"
timeout_ms: 5000
retry_attempts: 3
circuit_breaker_enabled: false    # Planned; not yet implemented
```

See [Production](PRODUCTION.md) for details on the coordination service.

---

## Logic fetching and caching

### Local mode (development)

When `business_config_uri` is a relative or `file://` path, the library resolves it to an absolute directory on disk and imports logic directly from there. No caching or fetching occurs. Changes to logic files take effect immediately (or after `reload_logic()`).

### Remote mode (production)

When `business_config_uri` is an `http://` or `https://` URL, the `LogicPackageFetcher` fetches the entire logic package at startup:

1. Fetches `business-config.yaml` from the URL
2. Derives the base URI (strips the filename)
3. Reads `structural_files` from the config to build the file list
4. Derives rule and helper filenames from the ruleset definitions
5. Fetches each file and caches it locally under `/tmp/validation-lib/logic/`

The local cache mirrors the remote `logic/` directory structure exactly, so all imports work unchanged.

### Cache directory

```
/tmp/validation-lib/
├── logic/                   ← cached logic package (rules, helpers, schemas)
│   ├── rules/
│   ├── entity_helpers/
│   ├── schema_helpers/
│   └── models/
└── config_<hash>.yaml       ← cached business config (keyed by source URI hash)
```

To force a full re-fetch, clear the cache:

```bash
rm -rf /tmp/validation-lib/
```

Or call it programmatically:

```python
service.reload_logic()
```

### Auto-refresh

The library refreshes the logic cache automatically at two points:

1. **At startup** — if the on-disk cache is older than `logic_cache_max_age_seconds` (default 1800 s), the constructor re-fetches before returning
2. **During a session** — before every API call, the in-memory config age is checked against the same limit; the check itself is debounced to run at most every 5 minutes to avoid overhead

This means all running instances of the library will pick up rule changes within the configured window without any restart or manual intervention.

### Immutability contract

The architecture relies on immutable filenames for rules and helpers:

- **Rules** — once published, a rule file is never edited in place. Changes produce a new file (`rule_001_v2.py`) with a corresponding `business-config.yaml` update.
- **Entity helpers** — same: breaking schema changes produce a new helper (`loan_v2.py`), not a modification of `loan_v1.py`.
- **Schemas** — published schemas are immutable; breaking changes increment the major version.
- **`business-config.yaml`** — the only file that changes in place. Editing it is how the rules team "deploys": add a rule, reorder a hierarchy, point a schema to a new helper.

This makes the cache safe for aggressive caching — immutable files can be cached indefinitely; only `business-config.yaml` needs freshness checks.
