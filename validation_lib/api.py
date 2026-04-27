"""
Public API for validation-lib.

This is the front door for validation operations.
"""

import json
import logging
import multiprocessing
import os
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from .config_loader import ConfigLoader
from .coordination_proxy import CoordinationProxy
from .logic_fetcher import LogicPackageFetcher
from .plugin_loader import PluginLoader
from .results import build_plugin_fail_envelope, build_validation_envelope
from .validation_engine import ValidationEngine

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

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


def _validate_item(item: dict, ruleset_name: str, plugin_name: str = None) -> dict:
    """
    Per-item validation task executed in a worker process.

    Args:
        item: Normalized batch item with correlation_id and data.
        ruleset_name: Ruleset to run.
        plugin_name: Optional plugin name.

    Returns:
        Per-item object-level validation envelope.
    """
    global _worker_service
    assert _worker_service is not None, (
        "_validate_item called outside a worker process — "
        "_worker_service was not initialised by _init_worker()"
    )
    return _worker_service._validate_batch_item(item, ruleset_name, plugin_name)


class ValidationService:
    """
    Main validation service class.

    Provides business data validation with dynamic rule and plugin loading from
    local or remote sources.
    """

    # Debounce interval: how often the mid-session staleness check runs (seconds)
    CHECK_INTERVAL = 300

    def __init__(self, _worker_mode: bool = False):
        """
        Initialize validation service with bundled configuration.

        Args:
            _worker_mode: Internal flag used only for batch worker processes.

        Raises:
            RuntimeError: If config loading or logic fetching fails.
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

    def _initialize(self) -> None:
        """Internal initialization logic used by __init__ and reload_logic."""
        self.config_loader = ConfigLoader()
        self._max_age = self.config_loader.get_logic_cache_max_age()
        self.coordination_proxy = CoordinationProxy(
            self.config_loader.get_coordination_service_config()
        )
        self.logic_fetcher = LogicPackageFetcher(
            cache_root=self.config_loader.cache_dir
        )
        if self._worker_mode and self.config_loader.get_logic_base_uri():
            # Worker processes must not fetch or mutate the shared cache.
            # The parent process populates it before submitting batch work.
            logic_dir = str(self.config_loader.cache_dir / "logic")
        else:
            logic_dir = self.logic_fetcher.resolve_logic_dir(
                self.config_loader.local_config_path
            )
        self.engine = ValidationEngine(
            config_loader=self.config_loader, logic_dir=logic_dir
        )
        self.plugin_loader = PluginLoader(self.config_loader.get_business_config())
        self._last_check_time = time.time()

    def _create_pool(self) -> None:
        """
        Create the ProcessPoolExecutor worker pool for batch validation.

        No-op in worker mode or when batch_parallelism is false.
        """
        if self._worker_mode:
            return
        if not self.config_loader.get_batch_parallelism():
            return
        max_workers = self.config_loader.get_batch_max_workers()
        ctx = multiprocessing.get_context("spawn")
        self._pool = ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=ctx,
            initializer=_init_worker,
        )
        logger.debug(
            f"Batch worker pool created (max_workers={max_workers or os.cpu_count()})"
        )

    def _check_and_reload_if_stale(self) -> None:
        """
        Check config freshness and reload if stale (debounced).

        Checks at most every CHECK_INTERVAL seconds. No-op in worker mode.
        """
        if self._worker_mode:
            return
        now = time.time()
        if now - self._last_check_time < self.CHECK_INTERVAL:
            return

        self._last_check_time = now

        business_age = self.config_loader.get_business_config_age()
        if business_age and business_age > self._max_age:
            logger.info(
                f"Business config stale ({business_age:.0f}s > {self._max_age}s), reloading"
            )
            self.reload_logic()
            return

        coord_age = self.config_loader.get_coordination_config_age()
        if coord_age and coord_age > self._max_age:
            logger.info(
                f"Coordination config stale ({coord_age:.0f}s > {self._max_age}s), reloading"
            )
            self.reload_logic()
            return

    def validate(
        self,
        entity_type: str,
        entity_data: Any,
        ruleset_name: str,
        plugin_name: str = None,
    ) -> Dict[str, Any]:
        """
        Validate a single entity against business rules.

        Args:
            entity_type: Type of entity (e.g., "loan").
            entity_data: Canonical entity dict, or raw plugin input when
                plugin_name is supplied.
            ruleset_name: Ruleset to use (e.g., "quick", "thorough").
            plugin_name: Optional input adapter plugin name.

        Returns:
            Object-level validation envelope with status, entity_type, ruleset,
            and hierarchical rule results. Plugin conversion failures return
            status "PLUGIN_FAIL" with plugin_message and empty results.
        """
        self._check_and_reload_if_stale()

        canonical_entity = entity_data
        if plugin_name is not None:
            self.plugin_loader.validate_plugin_for_entity(plugin_name, entity_type)
            canonical_entity, plugin_fail = self._convert_with_plugin(
                plugin_name, entity_type, ruleset_name, entity_data
            )
            if plugin_fail is not None:
                return plugin_fail

        results = self._validate_canonical_entity(
            entity_type, canonical_entity, ruleset_name
        )
        return build_validation_envelope(entity_type, ruleset_name, results)

    def discover_rules(self, entity_type, entity_data, ruleset_name):
        """
        Discover available validation rules for an entity type.

        Returns metadata about rules without executing them.
        """
        self._check_and_reload_if_stale()
        return self.engine.discover_rules(entity_type, entity_data, ruleset_name)

    def discover_rulesets(self):
        """
        Discover all available rulesets with metadata and statistics.

        Returns:
            Dict mapping ruleset name to metadata and statistics.
        """
        self._check_and_reload_if_stale()
        return self.engine.discover_rulesets()

    def batch_validate(
        self, items: List[Dict[str, Any]], ruleset_name: str, plugin_name: str = None
    ) -> Dict[str, Any]:
        """
        Validate multiple independent items in a single operation.

        Args:
            items: List of batch item dicts. Each item should contain "data"
                and may contain "correlation_id".
            ruleset_name: Ruleset to use for all items.
            plugin_name: Optional plugin applied independently to each item.

        Returns:
            Batch envelope with status "COMPLETED" and per-item envelopes.
        """
        self._check_and_reload_if_stale()
        normalized_items = self._normalize_batch_items(items)

        if plugin_name is not None:
            # Validate plugin config and importability once before submitting
            # work. In batch there is no requested entity_type parameter, so
            # per-item plugin failures use this declared entity type.
            self.plugin_loader.get_plugin_config(plugin_name)
            self.plugin_loader.load_plugin(plugin_name)

        if self._pool is not None:
            futures = [
                self._pool.submit(_validate_item, item, ruleset_name, plugin_name)
                for item in normalized_items
            ]
            item_results = [future.result() for future in futures]
        else:
            item_results = [
                self._validate_batch_item(item, ruleset_name, plugin_name)
                for item in normalized_items
            ]

        response = {
            "status": "COMPLETED",
            "ruleset": ruleset_name,
            "items": item_results,
        }
        if plugin_name is not None:
            response["plugin_name"] = plugin_name
        return response

    def batch_file_validate(
        self, file_uri: str, ruleset_name: str, plugin_name: str = None
    ) -> Dict[str, Any]:
        """
        Validate items loaded from a JSON or JSONL file.

        Args:
            file_uri: URI to JSON/JSONL file (file://, http://, https://).
            ruleset_name: Ruleset to use.
            plugin_name: Optional plugin applied to each loaded item.

        Returns:
            Batch validation envelope.
        """
        items = self._load_items_from_file(file_uri)
        return self.batch_validate(items, ruleset_name, plugin_name=plugin_name)

    def reload_logic(self):
        """
        Reload business logic from source.

        Clears cache, refetches logic, reinitializes loaders, and recreates the
        worker pool so workers pick up fresh code.
        """
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

        self.logic_fetcher.clear_cache()
        self._clear_config_cache()
        self._initialize()
        self._create_pool()

    def _clear_config_cache(self) -> None:
        """Delete cached remote config files before a forced reload."""
        for cache_file in self.config_loader.cache_dir.glob("config_*.yaml"):
            cache_file.unlink()

    def get_cache_age(self):
        """
        Get age of cached logic in seconds.

        Returns:
            float age in seconds, or None if logic has not been cached.
        """
        return self.logic_fetcher.get_cache_age()

    def _validate_canonical_entity(
        self, entity_type: str, entity_data: dict, ruleset_name: str
    ) -> List[Dict[str, Any]]:
        """Run the existing canonical validation flow and return rule results."""
        if not isinstance(entity_data, dict):
            raise ValueError("Canonical entity data must be a dict")

        schema_url = entity_data.get("$schema", "")
        required_terms = self.engine.get_required_data(
            entity_type, schema_url, ruleset_name
        )
        required_data = self.coordination_proxy.get_associated_data(
            entity_type, entity_data, required_terms
        )
        return self.engine.validate(entity_type, entity_data, ruleset_name, required_data)

    def _validate_batch_item(
        self, item: Dict[str, Any], ruleset_name: str, plugin_name: str = None
    ) -> Dict[str, Any]:
        """Validate one normalized batch item and return an item envelope."""
        correlation_id = item["correlation_id"]
        data = item["data"]
        plugin_entity_type = None

        try:
            if plugin_name is not None:
                plugin_config = self.plugin_loader.get_plugin_config(plugin_name)
                plugin_entity_type = plugin_config["entity_type"]
                data, plugin_fail = self._convert_with_plugin(
                    plugin_name, plugin_entity_type, ruleset_name, data
                )
                if plugin_fail is not None:
                    plugin_fail["correlation_id"] = correlation_id
                    return plugin_fail

            entity_type = self._determine_entity_type(data)
            results = self._validate_canonical_entity(entity_type, data, ruleset_name)
            envelope = build_validation_envelope(entity_type, ruleset_name, results)
            envelope["correlation_id"] = correlation_id
            return envelope
        except Exception as e:
            return {
                "correlation_id": correlation_id,
                "status": "ERROR",
                "entity_type": plugin_entity_type or "unknown",
                "ruleset": ruleset_name,
                "results": [],
                "error_message": f"{type(e).__name__}: {e}",
            }

    def _convert_with_plugin(
        self,
        plugin_name: str,
        entity_type: str,
        ruleset_name: str,
        input_data: Any,
    ):
        """Run a plugin conversion and return (converted_entity, fail_envelope)."""
        plugin = self.plugin_loader.load_plugin(plugin_name)
        plugin_error = self.plugin_loader.get_plugin_error_class()

        try:
            converted = plugin.convert(input_data)
        except plugin_error as e:
            return None, build_plugin_fail_envelope(
                entity_type, ruleset_name, plugin_name, str(e)
            )
        except Exception as e:
            return None, build_plugin_fail_envelope(
                entity_type,
                ruleset_name,
                plugin_name,
                f"Plugin execution failed: {type(e).__name__}: {e}",
            )

        if not isinstance(converted, dict):
            return None, build_plugin_fail_envelope(
                entity_type,
                ruleset_name,
                plugin_name,
                "Plugin output must be a dict containing a $schema field",
            )

        if "$schema" not in converted:
            return None, build_plugin_fail_envelope(
                entity_type,
                ruleset_name,
                plugin_name,
                "Plugin output missing required $schema field",
            )

        return converted, None

    def _normalize_batch_items(
        self, items: List[Dict[str, Any]], default_prefix: str = "item"
    ) -> List[Dict[str, Any]]:
        """Normalize user-supplied batch items to correlation_id/data dicts."""
        if not isinstance(items, list):
            raise ValueError("items must be a list")

        normalized = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise ValueError("Each batch item must be a dict")

            if "data" in item:
                data = item["data"]
                correlation_id = item.get("correlation_id")
            else:
                data = item
                correlation_id = item.get("correlation_id")
                if correlation_id is not None:
                    data = {k: v for k, v in item.items() if k != "correlation_id"}

            if correlation_id is None:
                correlation_id = f"{default_prefix}-{index}"

            normalized.append(
                {
                    "correlation_id": str(correlation_id),
                    "data": data,
                }
            )

        return normalized

    def _determine_entity_type(self, entity):
        """
        Determine entity type from canonical entity data.

        Raises:
            ValueError: If entity type cannot be determined.
        """
        if not isinstance(entity, dict):
            raise ValueError("Cannot determine entity type from non-dict entity data")

        schema_url = entity.get("$schema")
        if schema_url:
            entity_type = self._extract_entity_type_from_schema(schema_url)
            if entity_type:
                return entity_type

        if "entity_type" in entity:
            return entity["entity_type"]

        raise ValueError(
            "Cannot determine entity type - entity must have $schema or entity_type field"
        )

    def _extract_entity_type_from_schema(self, schema_url):
        """Extract entity type from schema URL."""
        parsed = urlparse(schema_url)
        if not schema_url or parsed.scheme not in ("http", "https", "file"):
            return None

        segments = [s for s in parsed.path.split("/") if s]

        if segments and segments[-1].endswith(".json"):
            filename = segments[-1]
            entity_type = filename.split(".")[0]
            if entity_type:
                return entity_type

        if "/schemas/" in parsed.path:
            parts = parsed.path.split("/schemas/")
            if len(parts) >= 2:
                return parts[1].split("/")[0]

        for i, segment in enumerate(segments):
            if segment.startswith("v") and "." in segment and i > 0:
                return segments[i - 1]

        if len(segments) >= 2:
            return segments[-2]
        return None

    def close(self) -> None:
        """
        Shut down the worker process pool cleanly.

        Safe to call multiple times or when batch parallelism is disabled.
        """
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def _load_items_from_file(self, file_uri):
        """
        Load normalized batch items from a JSON or JSONL file URI.

        Supports file://, http://, and https:// URIs.
        """
        parsed = urlparse(file_uri)
        try:
            content = self._read_file_uri(file_uri, parsed)
            suffix = Path(unquote(parsed.path)).suffix.lower()
            if suffix == ".jsonl":
                return self._parse_jsonl_items(content)
            return self._parse_json_items(content)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to load items from {file_uri}: {e}") from e

    def _read_file_uri(self, file_uri: str, parsed) -> str:
        """Read a local or remote file URI into text with a size cap."""
        if parsed.scheme == "file":
            file_path = Path(unquote(parsed.path)).resolve()
            if not file_path.is_file():
                raise ValueError(f"File not found or not a regular file: {file_path}")
            if file_path.stat().st_size > MAX_FILE_SIZE:
                raise RuntimeError(
                    f"Local file exceeds {MAX_FILE_SIZE // (1024 * 1024)} MB limit"
                )
            return file_path.read_text()

        if parsed.scheme in ("http", "https"):
            with urllib.request.urlopen(file_uri, timeout=30) as response:
                raw = response.read(MAX_FILE_SIZE + 1)
                if len(raw) > MAX_FILE_SIZE:
                    raise RuntimeError(
                        f"Remote file exceeds {MAX_FILE_SIZE // (1024 * 1024)} MB limit"
                    )
                return raw.decode("utf-8")

        raise ValueError(f"Unsupported URI scheme: {parsed.scheme}")

    def _parse_json_items(self, content: str) -> List[Dict[str, Any]]:
        """Parse JSON content and normalize to batch items."""
        data = json.loads(content)
        if isinstance(data, list):
            return self._normalize_batch_items(data)
        return self._normalize_batch_items([data])

    def _parse_jsonl_items(self, content: str) -> List[Dict[str, Any]]:
        """Parse JSONL content and normalize to batch items."""
        parsed_items = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSONL on line {line_number}: {e}") from e
            if not isinstance(parsed, dict):
                raise ValueError(f"JSONL line {line_number} must be a JSON object")
            if "correlation_id" not in parsed:
                parsed["correlation_id"] = f"line-{len(parsed_items) + 1}"
            parsed_items.append(parsed)
        return self._normalize_batch_items(parsed_items, default_prefix="line")
