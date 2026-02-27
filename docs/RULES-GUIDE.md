# Rules Guide

This document covers everything needed to write, organise, and version validation rules in the `validation-logic` package.

---

## Overview

Rules are Python classes that live in `validation-logic/rules/<entity_type>/`. Each rule file contains exactly one class named `Rule` that inherits from `ValidationRule`. The rule executor discovers and loads rules dynamically by filename — no registration or manifest is needed.

---

## Rule statuses

Every rule's `run()` method returns a `(status, message)` tuple:

| Status | Meaning | Children |
|---|---|---|
| `PASS` | Check passed | Run |
| `WARN` | Advisory condition found — not a failure | Run |
| `FAIL` | Check failed | Skipped (marked `NORUN`) |
| `NORUN` | Rule could not run (missing data, parent failed) | Skipped |
| `ERROR` | Unhandled exception during execution | Skipped |

`WARN` is useful for flagging conditions that warrant attention without blocking downstream processing. `NORUN` is the correct return when required fields are missing or unavailable — it signals "I couldn't check this" rather than "this is wrong".

---

## The `ValidationRule` base class

All rules inherit from `ValidationRule` in `rules/base.py` and must implement five abstract methods:

```python
from rules.base import ValidationRule

class Rule(ValidationRule):

    def validates(self) -> str:
        """Entity type this rule applies to: 'loan', 'facility', 'deal'."""
        return "loan"

    def required_data(self) -> list[str]:
        """
        Vocabulary terms for external data this rule needs.
        Return [] if no external data is needed.
        Examples: [], ["parent"], ["all_siblings"]
        """
        return []

    def description(self) -> str:
        """Plain English description of what this rule checks."""
        return "Loan must have a positive principal amount"

    def set_required_data(self, data: dict) -> None:
        """
        Called before run() to inject external data.
        Store as instance attributes for use in run().
        """
        self.parent = data.get("parent")

    def run(self) -> tuple[str, str]:
        """
        Execute the rule. self.entity is injected by the executor.
        Returns (status, message).
        """
        ...
```

The rule ID (`get_id()`) is injected automatically by the loader from the filename — do not override it.

---

## Entity helpers

Rules **must not** access entity data via raw dict paths. Instead, use `self.entity`, which is an entity helper instance injected by the rule executor before `run()` is called.

Entity helpers expose stable **logical property names** that are insulated from physical schema changes:

```python
def run(self) -> tuple[str, str]:
    # Correct — uses logical property names:
    if self.entity.principal <= 0:
        return ("FAIL", f"Principal must be positive, got {self.entity.principal}")

    if self.entity.maturity <= self.entity.inception:
        return ("FAIL", "Maturity date must be after origination date")

    return ("PASS", "")
```

Never do this:

```python
# Wrong — brittle, breaks when schema fields are renamed:
principal = self.entity._data["financial"]["principal_amount"]
```

### Handling missing fields

Not all fields are guaranteed to be present in every entity. Use `try/except AttributeError` and return `NORUN` when a required field is unavailable:

```python
try:
    principal = self.entity.principal
except AttributeError as e:
    return ("NORUN", f"Cannot access principal: {e}")

if principal <= 0:
    return ("FAIL", f"Principal must be positive, got {principal}")
return ("PASS", "")
```

### Logical property names for `loan_v1`

| Logical name | Physical path | Type conversion |
|---|---|---|
| `schema` | `$schema` | — |
| `id` | `id` | — |
| `reference` | `loan_number` | — |
| `facility` | `facility_id` | — |
| `client` | `client_id` | — |
| `principal` | `financial.principal_amount` | — |
| `balance` | `financial.outstanding_balance` | — |
| `currency` | `financial.currency` | — |
| `rate` | `financial.interest_rate` | — |
| `rate_type` | `financial.interest_type` | — |
| `inception` | `dates.origination_date` | `str` → `date` |
| `maturity` | `dates.maturity_date` | `str` → `date` |
| `first_payment` | `dates.first_payment_date` | `str` → `date` |
| `status` | `status` | — |

The full mapping is defined in `entity_helpers/loan_v1.json`. Browse it with `list_logic_files()` in the MCP server.

---

## Writing a new rule

### Step 1 — Create the rule file

Place the file in `validation-logic/rules/<entity_type>/`:

```
validation-logic/rules/loan/rule_006_v1.py
```

File naming: `rule_{number}_v{version}.py`. The rule ID is derived from the filename by stripping `.py` — so `rule_006_v1.py` becomes `rule_006_v1`.

```python
"""Check that loan currency is a supported currency."""

from rules.base import ValidationRule

SUPPORTED_CURRENCIES = {"USD", "GBP", "EUR", "JPY", "CHF"}

class Rule(ValidationRule):

    def validates(self) -> str:
        return "loan"

    def required_data(self) -> list[str]:
        return []

    def description(self) -> str:
        return "Loan currency must be a supported currency"

    def set_required_data(self, data: dict) -> None:
        pass

    def run(self) -> tuple[str, str]:
        currency = self.entity.currency
        if currency is None:
            return ("NORUN", "Currency field is absent")
        if currency not in SUPPORTED_CURRENCIES:
            return ("FAIL", f"Unsupported currency: {currency}. Supported: {sorted(SUPPORTED_CURRENCIES)}")
        return ("PASS", "")
```

### Step 2 — Register the rule in `business-config.yaml`

Add it to the appropriate ruleset(s) under the schema URL key(s) it applies to:

```yaml
rulesets:
  quick:
    rules:
      "https://.../loan.schema.v1.0.0.json":
        - rule_id: rule_001_v1
        - rule_id: rule_002_v1
        - rule_id: rule_006_v1    # ← add here
```

### Step 3 — Clear the cache and test

```bash
rm -rf /tmp/validation-lib/
pytest tests/
```

No other changes are needed. The rule loader discovers `rule_006_v1.py` by matching the `rule_id` to a filename.

---

## Hierarchical rules (parent-child)

Rules can be nested in `business-config.yaml`. A child rule only runs if its parent returns `PASS` or `WARN`:

```yaml
rulesets:
  thorough:
    rules:
      "https://.../loan.schema.v1.0.0.json":
        - rule_id: rule_003_v1       # Status validation
          children:
            - rule_id: rule_004_v1   # Balance constraints — only runs if rule_003 passes
```

If `rule_003_v1` returns `FAIL` or `NORUN`, `rule_004_v1` is automatically marked `NORUN` with the message `"Parent rule did not pass, rule skipped"`. This models prerequisites: don't check balance constraints if the status is invalid.

Child results are **nested** inside the parent's result dict under `"children"` — they do not appear at the top level of the results list. Code that inspects results must recurse into `"children"` to find all failures.

A rule can be a child in one ruleset and a top-level rule in another — the hierarchy is configuration, not code.

---

## Required data vocabulary

Rules that need data beyond the entity being validated declare vocabulary terms in `required_data()`:

```python
def required_data(self) -> list[str]:
    return ["parent"]   # Need the parent facility for this loan check
```

The coordination service (currently stubbed) fetches this data before `run()` is called, and injects it via `set_required_data()`. Supported terms:

| Term | Meaning |
|---|---|
| `parent` | Parent entity in the hierarchy (e.g. facility for a loan) |
| `all_children` | All child entities |
| `all_siblings` | Sibling entities sharing the same parent |
| `client_reference_data` | Client-level reference data |

If required data is unavailable (coordination service disabled or data missing), `set_required_data()` receives an empty dict and the rule should return `NORUN`.

---

## Schema versioning

### How version routing works

When `validate()` is called, the entity's `$schema` URL is used to select:
1. Which ruleset rows to run (via `business-config.yaml` schema URL keys)
2. Which entity helper class to instantiate (via `schema_to_helper_mapping`)

This means you can assign different rules to v1 and v2 entities, and each version gets the correct logical field mapping automatically.

### Adding a new schema version

1. **Create the schema file** — `validation-logic/models/loan.schema.v3.0.0.json` with a new `$id`
2. **Create a new entity helper** — `validation-logic/entity_helpers/loan_v3.json` mapping logical names to the new physical paths
3. **Register the mapping** in `business-config.yaml`:
   ```yaml
   schema_to_helper_mapping:
     "https://.../loan.schema.v3.0.0.json": "loan_v3"
   ```
4. **Add ruleset entries** for the new schema URL (can reuse existing rules):
   ```yaml
   rulesets:
     quick:
       rules:
         "https://.../loan.schema.v3.0.0.json":
           - rule_id: rule_001_v1
           - rule_id: rule_002_v1
   ```

Existing rules do not need to change. They continue to use logical property names, and `loan_v3.json` maps those same names to the new physical paths.

### Versioning rules vs. versioning schemas

Schema versions and rule versions are independent:

- `rule_001_v1` can be assigned to both schema v1 and schema v2 — the same rule code, just different helper mappings
- `rule_001_v2` would be a **new rule file** with different logic, co-existing with `rule_001_v1`; `business-config.yaml` controls which version is active for each schema

**Never edit a published rule file in place.** Changes are published as new versioned files. This preserves auditability and makes caching safe.

### Minor version fallback

With `allow_minor_version_fallback: true` in `business-config.yaml`, an entity with schema `v1.2.0` will automatically fall back to the `v1.0.0` helper if no `v1.2.0` mapping exists. This allows minor schema additions to be transparent to rules. Major version mismatches always raise an error (`strict_major_version: true`).
