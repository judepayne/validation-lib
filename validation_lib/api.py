"""
Public API for validation-lib

This is the "front door" - the main entry point for all validation operations.
"""

import os
import time
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from typing import Optional

from .config_loader import ConfigLoader
from .logic_fetcher import LogicPackageFetcher
from .validation_engine import ValidationEngine
from .coordination_proxy import CoordinationProxy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level worker state and task functions
#
# These must live at module level (not inside the class) so they are picklable
# by the multiprocessing 'spawn' context used for ProcessPoolExecutor workers.
# ---------------------------------------------------------------------------

# One ValidationService instance per worker process, created by _init_worker().
_worker_service: Optional["ValidationService"] = None


def _init_worker() -> None:
    """
    Worker process initializer for ProcessPoolExecutor.

    Creates a single ValidationService in worker mode for this process.
    Worker mode disables auto-refresh so workers never touch the shared
    /tmp cache independently — only the main process manages cache freshness.
    Called once per worker process at pool creation time.
    """
    global _worker_service
    _worker_service = ValidationService(_worker_mode=True)


def _validate_entity(entity: dict, id_fields: list, ruleset_name: str) -> dict:
    """
    Per-entity validation task executed in a worker process.

    Replicates the per-entity logic from batch_validate(), using the
    worker-local ValidationService instance. Must be a module-level function
    to be picklable by the spawn context.

    Args:
        entity: Entity data dict.
        id_fields: Field names used to build the entity identifier.
        ruleset_name: Ruleset to run.

    Returns:
        Per-entity result dict with entity_id, entity_type, and results.
    """
    global _worker_service
    assert _worker_service is not None, (
        "_validate_entity called outside a worker process — "
        "_worker_service was not initialised by _init_worker()"
    )
    entity_type = _worker_service._determine_entity_type(entity)
    schema_url = entity.get("$schema", "")
    required_terms = _worker_service.engine.get_required_data(
        entity_type, schema_url, ruleset_name
    )
    required_data = _worker_service.coordination_proxy.get_associated_data(
        entity_type, entity, required_terms
    )
    validation_results = _worker_service.engine.validate(
        entity_type, entity, ruleset_name, required_data
    )
    entity_id = _worker_service._extract_id(entity, id_fields)
    return {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "results": validation_results,
    }


class ValidationService:
    """
    Main validation service class.

    Provides business data validation with dynamic rule loading from local or remote sources.

    Auto-refresh: Configs are automatically reloaded when stale (configurable intervals).

    Example:
        from validation_lib import ValidationService

        service = ValidationService()
        results = service.validate("loan", loan_data, "quick")

        # Reload logic (useful in dev or to refresh from remote)
        service.reload_logic()

        # Check cache age (useful for monitoring)
        age = service.get_cache_age()
        if age and age > 3600:  # Older than 1 hour
            service.reload_logic()
    """

    # Debounce interval: how often the mid-session staleness check runs (hardcoded, seconds)
    CHECK_INTERVAL = 300  # Check every 5 minutes

    def __init__(self, _worker_mode: bool = False):
        """
        Initialize validation service with bundled configuration.

        The service automatically:
        1. Loads bundled local-config.yaml
        2. Fetches/caches business logic (rules, schemas, helpers)
        3. Initializes the validation engine
        4. Reloads logic from source if the disk cache is older than
           logic_cache_max_age_seconds (from local-config.yaml, default 1800s)
        5. Creates the worker process pool if batch_parallelism is enabled
           (skipped when _worker_mode=True)

        Args:
            _worker_mode: Internal flag — set True only by _init_worker() when
                creating a ValidationService inside a pool worker process. Disables
                auto-refresh and pool creation so workers never touch the shared
                cache independently.

        Raises:
            RuntimeError: If config loading or logic fetching fails
        """
        self._worker_mode = _worker_mode
        self._pool: Optional[ProcessPoolExecutor] = None
        self._initialize()

        # At startup, reload if the on-disk logic cache is stale.
        # Skipped in worker mode — workers trust the cache as-is.
        if not self._worker_mode:
            cache_age = self.logic_fetcher.get_cache_age()
            if cache_age is not None and cache_age > self._max_age:
                logger.info(
                    f"Logic cache stale at startup ({cache_age:.0f}s > {self._max_age}s), reloading"
                )
                self.reload_logic()
                return  # reload_logic() calls _create_pool(); don't double-create

        self._create_pool()

    def _initialize(self):
        """Internal initialization logic (used by __init__ and reload_logic)."""
        # Load bundled config
        self.config_loader = ConfigLoader()

        # Read max cache age from config (used at startup and in mid-session checks)
        self._max_age = self.config_loader.get_logic_cache_max_age()

        # Initialize coordination proxy for fetching associated data
        self.coordination_proxy = CoordinationProxy(
            self.config_loader.get_coordination_service_config()
        )

        # Fetch/cache logic from configured location
        self.logic_fetcher = LogicPackageFetcher()
        logic_dir = self.logic_fetcher.resolve_logic_dir(
            self.config_loader.local_config_path
        )

        # Initialize validation engine
        self.engine = ValidationEngine(
            config_loader=self.config_loader, logic_dir=logic_dir
        )

        # Track last freshness check time
        self._last_check_time = time.time()

    def _create_pool(self) -> None:
        """
        Create the ProcessPoolExecutor worker pool for batch validation.

        No-op when:
        - Running in worker mode (_worker_mode=True)
        - batch_parallelism is false in local-config.yaml

        Uses an explicit 'spawn' context for cross-platform safety. On Linux
        the default is 'fork', which can cause deadlocks when the host process
        uses threads (e.g. the MCP server). 'spawn' is consistent on all
        platforms and avoids this class of issue.

        Workers are lazy — they are not actually spawned until the first
        submit() call, so pool creation itself is near-instant.
        """
        if self._worker_mode:
            return
        if not self.config_loader.get_batch_parallelism():
            return
        max_workers = self.config_loader.get_batch_max_workers()
        ctx = multiprocessing.get_context("spawn")
        self._pool = ProcessPoolExecutor(
            max_workers=max_workers,  # None → os.cpu_count()
            mp_context=ctx,
            initializer=_init_worker,
        )
        logger.debug(
            f"Batch worker pool created (max_workers={max_workers or os.cpu_count()})"
        )

    def _check_and_reload_if_stale(self):
        """
        Check config freshness and reload if stale (debounced).

        Checks at most every CHECK_INTERVAL seconds.
        Reloads if business config or coordination config exceeds max age.
        No-op in worker mode — workers never manage cache freshness.
        """
        if self._worker_mode:
            return
        now = time.time()

        # Debounce: Only check every CHECK_INTERVAL seconds
        if now - self._last_check_time < self.CHECK_INTERVAL:
            return

        self._last_check_time = now

        # Check business config age
        business_age = self.config_loader.get_business_config_age()
        if business_age and business_age > self._max_age:
            logger.info(
                f"Business config stale ({business_age:.0f}s > {self._max_age}s), reloading"
            )
            self.reload_logic()
            return

        # Check coordination config age
        coord_age = self.config_loader.get_coordination_config_age()
        if coord_age and coord_age > self._max_age:
            logger.info(
                f"Coordination config stale ({coord_age:.0f}s > {self._max_age}s), reloading"
            )
            self.reload_logic()
            return

    def validate(self, entity_type, entity_data, ruleset_name):
        """
        Validate a single entity against business rules.

        Args:
            entity_type: Type of entity (e.g., "loan", "facility")
            entity_data: Entity data dict (must include $schema field for schema validation)
            ruleset_name: Ruleset to use (e.g., "quick", "thorough")

        Returns:
            List of validation result dicts, each containing:
                - rule_id: Rule identifier
                - description: Rule description
                - status: "PASS", "FAIL", "WARN", "NORUN", or "ERROR"
                - message: Failure message (if status is FAIL or ERROR)
                - execution_time_ms: Execution time
                - children: Nested child rule results (if hierarchical)

        Raises:
            ValueError: If entity_type or ruleset_name is invalid
            RuntimeError: If validation execution fails critically

        Example:
            results = service.validate("loan", {
                "$schema": "https://example.com/schemas/loan/v1.0.0",
                "id": "LOAN-001",
                "principal_amount": 100000,
                ...
            }, "quick")

            for result in results:
                if result['status'] == 'FAIL':
                    print(f"{result['rule_id']}: {result['message']}")
        """
        # Auto-refresh stale configs
        self._check_and_reload_if_stale()

        # Get schema URL from entity data
        schema_url = entity_data.get("$schema", "")

        # Phase 1: Get required data for this validation
        required_terms = self.engine.get_required_data(
            entity_type, schema_url, ruleset_name
        )

        # Phase 2: Fetch required data from coordination service
        required_data = self.coordination_proxy.get_associated_data(
            entity_type, entity_data, required_terms
        )

        # Phase 3: Execute validation
        return self.engine.validate(
            entity_type, entity_data, ruleset_name, required_data
        )

    def discover_rules(self, entity_type, entity_data, ruleset_name):
        """
        Discover available validation rules for an entity type.

        Returns metadata about rules without executing them. Useful for understanding
        what validations will run and what data they require.

        Args:
            entity_type: Type of entity (e.g., "loan")
            entity_data: Sample entity data dict (used for schema detection)
            ruleset_name: Ruleset to query (e.g., "quick", "thorough")

        Returns:
            Dict mapping rule_id to rule metadata:
                - rule_id: Rule identifier
                - entity_type: Entity type this rule validates
                - description: Human-readable description
                - required_data: List of additional data dependencies
                - field_dependencies: Fields this rule accesses
                - applicable_schemas: Schema URLs this rule applies to

        Example:
            rules = service.discover_rules("loan", sample_loan, "quick")
            for rule_id, metadata in rules.items():
                print(f"{rule_id}: {metadata['description']}")
                print(f"  Required fields: {metadata['field_dependencies']}")
        """
        # Auto-refresh stale configs
        self._check_and_reload_if_stale()

        return self.engine.discover_rules(entity_type, entity_data, ruleset_name)

    def discover_rulesets(self):
        """
        Discover all available rulesets with metadata and statistics.

        Returns:
            Dict mapping ruleset_name to ruleset info:
                - metadata: Ruleset metadata (description, purpose, author, date)
                - stats: Statistics (total_rules, supported_entities, supported_schemas)

        Example:
            rulesets = service.discover_rulesets()
            for name, info in rulesets.items():
                print(f"{name}: {info['metadata']['description']}")
                print(f"  Total rules: {info['stats']['total_rules']}")
        """
        # Auto-refresh stale configs
        self._check_and_reload_if_stale()

        return self.engine.discover_rulesets()

    def batch_validate(self, entities, id_fields, ruleset_name):
        """
        Validate multiple entities in a single operation.

        Orchestrates validation across multiple entities, extracting entity types
        from each entity's $schema field.

        Args:
            entities: List of entity dicts (each must have $schema field)
            id_fields: List of field names to use for entity identification in results
            ruleset_name: Ruleset to use for all entities

        Returns:
            List of per-entity validation results, each containing:
                - entity_id: Extracted entity identifier
                - entity_type: Detected entity type
                - results: List of validation results (same format as validate())

        Raises:
            ValueError: If entities is empty or entity types can't be determined
            RuntimeError: If batch validation fails

        Example:
            results = service.batch_validate([
                {"$schema": "...", "id": "LOAN-001", ...},
                {"$schema": "...", "id": "LOAN-002", ...}
            ], ["id"], "quick")

            for entity_result in results:
                print(f"Entity {entity_result['entity_id']}:")
                for rule_result in entity_result['results']:
                    print(f"  {rule_result['rule_id']}: {rule_result['status']}")
        """
        # Auto-refresh stale configs
        self._check_and_reload_if_stale()

        if self._pool is not None:
            # Parallel path: distribute entities across worker processes.
            # Futures are submitted and collected in input order, preserving
            # result ordering regardless of which worker finishes first.
            futures = [
                self._pool.submit(_validate_entity, entity, id_fields, ruleset_name)
                for entity in entities
            ]
            return [f.result() for f in futures]

        # Sequential fallback: pool disabled or batch_parallelism is false.
        results = []
        for entity in entities:
            # Determine entity type from $schema or other hints
            entity_type = self._determine_entity_type(entity)

            # Get schema URL
            schema_url = entity.get("$schema", "")

            # Get required data for this validation
            required_terms = self.engine.get_required_data(
                entity_type, schema_url, ruleset_name
            )
            required_data = self.coordination_proxy.get_associated_data(
                entity_type, entity, required_terms
            )

            # Validate the entity
            validation_results = self.engine.validate(
                entity_type, entity, ruleset_name, required_data
            )

            # Extract entity ID
            entity_id = self._extract_id(entity, id_fields)

            results.append(
                {
                    "entity_id": entity_id,
                    "entity_type": entity_type,
                    "results": validation_results,
                }
            )

        return results

    def batch_file_validate(self, file_uri, entity_types, id_fields, ruleset_name):
        """
        Validate entities loaded from a file.

        Loads entities from file URI (local or remote), then performs batch validation.

        Args:
            file_uri: URI to file containing entities (file://, http://, https://)
            entity_types: List of entity types in the file
            id_fields: List of field names to use for entity identification
            ruleset_name: Ruleset to use

        Returns:
            List of per-entity validation results (same format as batch_validate())

        Raises:
            RuntimeError: If file loading or validation fails

        Example:
            results = service.batch_file_validate(
                "file:///data/loans.json",
                ["loan"],
                ["id"],
                "thorough"
            )
        """
        # Load entities from file
        entities = self._load_entities_from_file(file_uri)

        # Use batch_validate to process them
        return self.batch_validate(entities, id_fields, ruleset_name)

    def reload_logic(self):
        """
        Reload business logic from source.

        Performs a full reload:
        1. Clears cache directory
        2. Re-fetches logic from source (local path or remote URL)
        3. Reloads business-config.yaml
        4. Re-imports all rule modules and entity helpers (hot reload)

        Useful for:
        - Development: Pick up rule changes without restarting
        - Production: Refresh logic from remote URL after updates

        Raises:
            RuntimeError: If logic fetch or reload fails

        Example:
            # In development - pick up local changes
            service.reload_logic()

            # In production - refresh from remote after deploy
            if service.get_cache_age() > 3600:  # Older than 1 hour
                service.reload_logic()
        """
        # Shut down the worker pool before clearing the cache.
        # shutdown(wait=True) blocks until any in-flight batch completes,
        # ensuring no worker is mid-validation when the cache is wiped.
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

        # Clear cache
        self.logic_fetcher.clear_cache()

        # Re-initialize everything
        self._initialize()

        # Recreate the pool so workers pick up the fresh logic from the
        # newly populated cache.
        self._create_pool()

    def get_cache_age(self):
        """
        Get age of cached logic in seconds.

        Returns the time since the logic cache was created/last updated.
        Returns None if logic hasn't been cached yet.

        Returns:
            float: Age in seconds, or None if not cached

        Example:
            age = service.get_cache_age()
            if age is None:
                print("Logic not cached yet")
            elif age > 3600:  # 1 hour
                print(f"Cache is {age/3600:.1f} hours old, consider reloading")
                service.reload_logic()
            else:
                print(f"Cache is {age:.0f} seconds old")
        """
        return self.logic_fetcher.get_cache_age()

    def _determine_entity_type(self, entity):
        """
        Determine entity type from entity data.

        Tries multiple strategies:
        1. Extract from $schema URL
        2. Use explicit entity_type field
        3. Fallback to config defaults

        Args:
            entity: Entity data dict

        Returns:
            Entity type string

        Raises:
            ValueError: If entity type cannot be determined
        """
        # Strategy 1: Extract from $schema URL
        schema_url = entity.get("$schema")
        if schema_url:
            entity_type = self._extract_entity_type_from_schema(schema_url)
            if entity_type:
                return entity_type

        # Strategy 2: Explicit entity_type field
        if "entity_type" in entity:
            return entity["entity_type"]

        # Strategy 3: Try to infer from known schemas
        # (Could check schema_to_helper_mapping in config)
        raise ValueError(
            "Cannot determine entity type - entity must have $schema or entity_type field"
        )

    def _extract_entity_type_from_schema(self, schema_url):
        """
        Extract entity type from schema URL.

        Example:
            "https://example.com/schemas/loan/v1.0.0" → "loan"

        Args:
            schema_url: Schema URL string

        Returns:
            Entity type string, or None if cannot extract
        """
        from urllib.parse import urlparse

        if not schema_url or urlparse(schema_url).scheme not in ("http", "https"):
            return None

        # Parse URL path: .../schemas/loan/v1.0.0 → "loan"
        path = urlparse(schema_url).path
        segments = [s for s in path.split("/") if s]

        # Look for version-like segment and take the one before it
        for i, segment in enumerate(segments):
            if segment.startswith("v") and "." in segment:
                if i > 0:
                    return segments[i - 1]

        # Fallback: second-to-last segment
        if len(segments) >= 2:
            return segments[-2]

        return None

    def _extract_id(self, entity, id_fields):
        """
        Extract entity identifier from entity data.

        Args:
            entity: Entity data dict
            id_fields: List of field names to try

        Returns:
            String identifier (concatenated if multiple fields)
        """
        id_parts = []
        for field in id_fields:
            if field in entity:
                id_parts.append(str(entity[field]))

        if not id_parts:
            return "unknown"

        return "-".join(id_parts)

    def close(self) -> None:
        """
        Shut down the worker process pool cleanly.

        Call this when you are done with a ValidationService instance to release
        worker processes immediately rather than waiting for garbage collection.
        Safe to call multiple times or when batch_parallelism is disabled.

        Example:
            service = ValidationService()
            try:
                results = service.batch_validate(entities, ["id"], "quick")
            finally:
                service.close()
        """
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def _load_entities_from_file(self, file_uri):
        """
        Load entities from file URI.

        Supports:
        - file:// URIs (local files)
        - http://, https:// URIs (remote files)

        Args:
            file_uri: URI to file

        Returns:
            List of entity dicts

        Raises:
            RuntimeError: If file loading fails
        """
        import json
        import urllib.request
        from pathlib import Path
        from urllib.parse import urlparse, unquote

        MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

        parsed = urlparse(file_uri)

        try:
            if parsed.scheme == "file":
                # Local file — resolve to canonical path to prevent traversal via encoded '..'
                file_path = Path(unquote(parsed.path)).resolve()
                if not file_path.is_file():
                    raise ValueError(
                        f"File not found or not a regular file: {file_path}"
                    )
                with open(file_path) as f:
                    data = json.load(f)
            elif parsed.scheme in ("http", "https"):
                # Remote file — with timeout and bounded read
                with urllib.request.urlopen(file_uri, timeout=30) as response:
                    raw = response.read(MAX_FILE_SIZE + 1)
                    if len(raw) > MAX_FILE_SIZE:
                        raise RuntimeError(
                            f"Remote file exceeds {MAX_FILE_SIZE // (1024 * 1024)} MB limit"
                        )
                    data = json.loads(raw.decode("utf-8"))
            else:
                raise ValueError(f"Unsupported URI scheme: {parsed.scheme}")

            # Handle both single entity and list of entities
            if isinstance(data, list):
                return data
            else:
                return [data]

        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to load entities from {file_uri}: {e}") from e
