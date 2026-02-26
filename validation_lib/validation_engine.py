import os
import sys
from pathlib import Path
from typing import List, Dict, Any

from .rule_loader import RuleLoader
from .rule_executor import RuleExecutor


class ValidationEngine:
    """Core validation business logic, independent of transport"""

    def __init__(self, config_loader, logic_dir: str):
        """
        Initialize validation engine with configuration and logic directory.

        Args:
            config_loader: ConfigLoader instance
            logic_dir: Absolute path to logic directory containing rules, helpers, schemas

        Raises:
            ValueError: If logic_dir doesn't exist or is invalid
        """
        from .rule_fetcher import RuleFetcher

        self.config_loader = config_loader
        self.config = config_loader.get_business_config()
        self.logic_dir = Path(logic_dir)

        # Verify logic directory exists BEFORE trying to import from it
        if not self.logic_dir.exists():
            raise ValueError(f"Logic directory not found: {logic_dir}")

        # Clean stale logic entries from sys.path and sys.modules on reload
        stale_paths = [
            p
            for p in sys.path
            if "/validation-lib/" not in p
            and "/logic" in p
            and p != str(self.logic_dir)
        ]
        for p in stale_paths:
            sys.path.remove(p)
        stale_modules = [
            m
            for m in sys.modules
            if m.startswith(("entity_helpers", "rules.", "schema_helpers"))
        ]
        for m in stale_modules:
            del sys.modules[m]

        # Add logic_dir to sys.path so entity_helpers and rules can be imported
        if str(self.logic_dir) not in sys.path:
            sys.path.insert(0, str(self.logic_dir))

        # Now we can import from entity_helpers
        from entity_helpers.version_registry import get_registry
        from entity_helpers import create_entity_helper

        # Store for later use
        self._create_entity_helper = create_entity_helper

        # Initialize rule fetcher
        cache_dir = config_loader.cache_dir / "rules"
        self.rule_fetcher = RuleFetcher(cache_dir=str(cache_dir))

        # Initialize rule loader with config and fetcher
        self.rule_loader = RuleLoader(
            self.config, self.config_loader, self.rule_fetcher
        )

        # Initialize entity helper registry with config_loader
        get_registry(config_loader)

    def get_required_data(
        self, entity_type: str, schema_url: str, ruleset_name: str
    ) -> List[str]:
        """
        Phase 1: Introspect rules and return required data.

        Returns list of vocabulary terms needed for validation.

        Args:
            entity_type: Type of entity ("loan", "facility", "deal")
            schema_url: The schema URL declaring the entity's version
            ruleset_name: Rule set to use (e.g., "quick", "thorough")

        Returns:
            List of vocabulary terms (e.g., ["parent", "all_siblings"])
        """
        # Load rules based on config and ruleset
        rule_configs = self._get_rules_for_ruleset(
            entity_type, ruleset_name, schema_url
        )
        rules = self.rule_loader.load_rules(rule_configs)

        # Collect all required_data from all rules
        required = set()
        for rule in rules:
            required.update(rule.required_data())

        return list(required)

    def validate(
        self,
        entity_type: str,
        entity_data: dict,
        ruleset_name: str,
        required_data: dict,
    ) -> List[Dict[str, Any]]:
        """
        Phase 2: Execute rules and return hierarchical results.

        Returns structured results matching config hierarchy.

        Args:
            entity_type: Type of entity ("loan", "facility", "deal")
            entity_data: The entity data to validate
            ruleset_name: Rule set to use (e.g., "quick", "thorough")
            required_data: Additional data fetched from coordination service

        Returns:
            List of hierarchical result dicts with structure:
            [{
                "rule_id": str,
                "description": str,
                "status": "PASS" | "FAIL" | "NORUN",
                "message": str,
                "execution_time_ms": int,
                "children": [...]
            }, ...]
        """
        # Load rules
        schema_url = entity_data.get("$schema")
        rule_configs = self._get_rules_for_ruleset(
            entity_type, ruleset_name, schema_url
        )
        rules = self.rule_loader.load_rules(rule_configs)

        # Execute with hierarchy
        executor = RuleExecutor(rules, entity_data, required_data)
        results = executor.execute_hierarchical(rule_configs)

        return results

    def discover_rules(
        self, entity_type: str, entity_data: dict, ruleset_name: str
    ) -> Dict[str, Dict]:
        """
        Discover all rules and their comprehensive metadata.

        Args:
            entity_type: Type of entity ("loan", "facility", "deal")
            entity_data: Entity data (used for schema version routing)
            ruleset_name: Rule set to use (e.g., "quick", "thorough")

        Returns:
            Dict mapping rule_id to rule metadata including:
            - rule_id: Unique identifier
            - entity_type: What entity type this rule validates
            - description: Human-readable business purpose
            - required_data: External data vocabulary terms needed
            - field_dependencies: List of (logical, physical) field tuples
            - applicable_schemas: List of schema URLs this rule applies to
        """
        # Get schema URL for routing
        schema_url = entity_data.get("$schema")

        # Load rules for this entity/schema/ruleset
        rule_configs = self._get_rules_for_ruleset(
            entity_type, ruleset_name, schema_url
        )
        rules = self.rule_loader.load_rules(rule_configs)

        # Build comprehensive metadata for each rule
        result = {}

        for rule in rules:
            rule_id = rule.get_id()

            # Create entity helper with access tracking
            # Import here since it's only available after logic_dir is in sys.path
            from entity_helpers import create_entity_helper

            helper = create_entity_helper(entity_type, entity_data, track_access=True)

            # Execute rule to capture field accesses
            rule.entity = helper
            rule.set_required_data({})
            try:
                rule.run()  # Execute to trigger field accesses
            except Exception:
                pass  # Ignore errors - we only care about field access patterns

            # Collect metadata
            result[rule_id] = {
                "rule_id": rule_id,
                "entity_type": rule.validates(),
                "description": rule.description(),
                "required_data": rule.required_data(),
                "field_dependencies": helper.get_accesses(),
                "applicable_schemas": self._get_applicable_schemas(
                    rule_id, entity_type, ruleset_name
                ),
            }

        return result

    def discover_rulesets(self) -> Dict[str, Dict]:
        """
        Discover available rulesets with metadata and statistics.

        Returns:
            Dict mapping ruleset_name to {metadata, stats} where:
            - metadata: Dict with description, purpose, author, date
            - stats: Dict with rules_by_schema, total_rules, supported_entities, supported_schemas
        """
        rulesets_config = self.config.get("rulesets", {})
        result = {}

        for ruleset_name, ruleset_data in rulesets_config.items():
            metadata = ruleset_data.get("metadata", {}).copy()
            rules_section = ruleset_data.get("rules", {})
            stats = self._compute_ruleset_stats(rules_section)

            result[ruleset_name] = {"metadata": metadata, "stats": stats}

        return result

    def _compute_ruleset_stats(self, rules_section: Dict[str, List]) -> Dict[str, Any]:
        """
        Compute statistics for a ruleset.

        Args:
            rules_section: The rules section from config (maps schema URLs to rule lists)

        Returns:
            Dict with rules_by_schema, total_rules, supported_entities, supported_schemas
        """
        total_rules = 0
        rules_by_schema = {}
        supported_schemas = []
        supported_entities = set()

        for schema_url, rule_list in rules_section.items():
            # Add to supported schemas
            supported_schemas.append(schema_url)

            # Extract entity type from schema URL
            entity_type = self._extract_entity_from_schema(schema_url)
            if entity_type:
                supported_entities.add(entity_type)

            # Count rules recursively (including children)
            rule_count = self._count_rules_recursive(rule_list)
            rules_by_schema[schema_url] = rule_count
            total_rules += rule_count

        return {
            "rules_by_schema": rules_by_schema,
            "total_rules": total_rules,
            "supported_entities": sorted(list(supported_entities)),
            "supported_schemas": supported_schemas,
        }

    def _count_rules_recursive(self, rules_list: List[Dict]) -> int:
        """
        Recursively count rules including nested children.

        Args:
            rules_list: List of rule config dicts

        Returns:
            Total count of rules including all nested children
        """
        count = len(rules_list)

        for rule_config in rules_list:
            if "children" in rule_config:
                count += self._count_rules_recursive(rule_config["children"])

        return count

    def _extract_entity_from_schema(self, schema_url: str) -> str:
        """
        Extract entity type from schema URL.

        Example: "https://bank.example.com/schemas/loan/v1.0.0" -> "loan"

        Args:
            schema_url: Schema URL string

        Returns:
            Entity type string, or empty string if not found
        """
        try:
            # Schema URLs typically have format: .../schemas/{entity_type}/v{version}
            if "/schemas/" in schema_url:
                parts = schema_url.split("/schemas/")
                if len(parts) >= 2:
                    entity_part = parts[1].split("/")[0]
                    return entity_part
        except Exception:
            pass

        return ""

    def _get_applicable_schemas(
        self, rule_id: str, entity_type: str, ruleset_name: str
    ) -> List[str]:
        """
        Find all schema URLs that include this rule.

        Args:
            rule_id: The rule identifier
            entity_type: Entity type
            ruleset_name: Rule set name

        Returns:
            List of schema URLs where this rule is configured
        """
        rules_config = (
            self.config.get("rulesets", {}).get(ruleset_name, {}).get("rules", {})
        )
        applicable = []

        for key, rule_list in rules_config.items():
            # Check if this is a schema URL (not just entity type)
            if key.startswith("http"):
                # Check if rule_id is in this schema's rule list (recursing into children)
                if self._rule_in_list(rule_id, rule_list):
                    applicable.append(key)

        return applicable

    def _rule_in_list(self, rule_id: str, rule_list: List[Dict]) -> bool:
        """Check if rule_id appears anywhere in a rule list, including nested children."""
        for r in rule_list:
            if r.get("rule_id") == rule_id:
                return True
            if "children" in r and self._rule_in_list(rule_id, r["children"]):
                return True
        return False

    def _get_rules_for_ruleset(
        self, entity_type: str, ruleset_name: str, schema_url: str = None
    ) -> List[Dict[str, Any]]:
        """
        Extract rule configs for given entity type, rule set, and optional schema version.

        Args:
            entity_type: Type of entity ("loan", "facility", "deal")
            ruleset_name: Rule set to use (e.g., "quick", "thorough")
            schema_url: Optional schema URL to get version-specific rules

        Returns:
            List of rule config dicts from config file
        """
        rules_config = (
            self.config.get("rulesets", {}).get(ruleset_name, {}).get("rules", {})
        )

        # Try schema_url first (version-specific)
        if schema_url and schema_url in rules_config:
            return rules_config[schema_url]

        # Fallback to entity_type (backward compatibility)
        return rules_config.get(entity_type, [])
