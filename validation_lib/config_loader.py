"""Two-tier configuration loading with URI fetching and caching."""

import os
import time
import yaml
import hashlib
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional

try:
    from importlib.resources import files
except ImportError:
    # Python < 3.9 fallback
    from importlib_resources import files


class ConfigLoader:
    """Handles two-tier configuration: local config + business config."""

    # Hardcoded cache directory for validation-lib
    CACHE_DIR = Path("/tmp/validation-lib")

    def __init__(self):
        """
        Initialize config loader with bundled local-config.yaml.

        The local config is bundled in the validation_lib package.
        No parameters needed - configuration is always from the bundled file.
        """
        # Load bundled local-config.yaml from package
        # Use importlib.resources to find the bundled config file
        config_file = files("validation_lib").joinpath("local-config.yaml")
        self.local_config_path = str(config_file)

        self.cache_dir = self.CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Load local config from the bundled file
        with config_file.open("r") as f:
            self.local_config = yaml.safe_load(f)

        # Get business_config_uri - construct from new structure or use legacy direct value
        logic_dir_location = self.local_config.get("logic_directory_location")
        business_config_filename = self.local_config.get(
            "business_config_filename", "business-config.yaml"
        )

        if logic_dir_location:
            # New structure: construct URI from logic_directory_location + business_config_filename
            separator = "/" if not logic_dir_location.endswith("/") else ""
            business_config_uri = (
                f"{logic_dir_location}{separator}{business_config_filename}"
            )
        else:
            # Backward compatibility: direct business_config_uri
            business_config_uri = self.local_config.get("business_config_uri")

        # Load business config (may be remote)
        if business_config_uri:
            self.business_config = self._load_config_from_uri(business_config_uri)
            self.business_config_loaded_at = time.time()
        else:
            # Backward compatibility: if no business_config_uri, treat local config as business config
            self.business_config = self.local_config
            self.business_config_loaded_at = time.time()

        # Load coordination service config (may be remote)
        coordination_service_config_uri = self.local_config.get(
            "coordination_service_config_uri"
        )
        if coordination_service_config_uri:
            self.coordination_service_config = self._load_config_from_uri(
                coordination_service_config_uri
            )
            self.coordination_service_config_loaded_at = time.time()
        else:
            # No coordination service config - use empty dict (disabled)
            self.coordination_service_config = {"enabled": False}
            self.coordination_service_config_loaded_at = time.time()

    def _load_yaml(self, path: str) -> Dict[str, Any]:
        """Load YAML file from disk."""
        with open(path) as f:
            return yaml.safe_load(f)

    def _load_config_from_uri(self, uri: str) -> Dict[str, Any]:
        """
        Load config from URI (with caching).

        Supports:
        - Relative paths - ../business-config.yaml
        - file:// - Local filesystem (absolute paths)
        - https:// - Remote HTTP/HTTPS
        - http:// - Remote HTTP

        Args:
            uri: Config URI or relative path

        Returns:
            Parsed YAML config
        """
        parsed = urllib.parse.urlparse(uri)

        # Handle relative paths (no scheme)
        if not parsed.scheme or parsed.scheme == "":
            # Relative path - resolve relative to local config directory
            config_dir = os.path.dirname(os.path.abspath(self.local_config_path))
            path = os.path.join(config_dir, uri)
            return self._load_yaml(path)

        if parsed.scheme == "file":
            # Local file - load directly (absolute path)
            path = urllib.parse.unquote(parsed.path)
            return self._load_yaml(path)

        elif parsed.scheme in ("http", "https"):
            # Remote file - cache it
            cache_key = hashlib.sha256(uri.encode()).hexdigest()
            cache_path = self.cache_dir / f"config_{cache_key}.yaml"

            if cache_path.exists():
                # Use cached version
                return self._load_yaml(str(cache_path))
            else:
                # Fetch and cache
                content = self._fetch_uri(uri)
                cache_path.write_text(content)
                return yaml.safe_load(content)

        else:
            raise ValueError(f"Unsupported URI scheme: {parsed.scheme} in {uri}")

    def _fetch_uri(self, uri: str) -> str:
        """Fetch content from HTTP/HTTPS URI."""
        try:
            with urllib.request.urlopen(uri, timeout=10) as response:
                return response.read().decode("utf-8")
        except Exception as e:
            raise RuntimeError(f"Failed to fetch config from {uri}: {e}") from e

    def get_business_config(self) -> Dict[str, Any]:
        """Get business configuration (tier 2)."""
        return self.business_config

    def get_local_config(self) -> Dict[str, Any]:
        """Get local configuration (tier 1)."""
        return self.local_config

    def get_coordination_service_config(self) -> Dict[str, Any]:
        """Get coordination service configuration."""
        return self.coordination_service_config

    def get_business_config_age(self) -> Optional[float]:
        """
        Get age of business config in seconds since it was loaded.

        Returns:
            Age in seconds, or None if not loaded
        """
        if hasattr(self, "business_config_loaded_at"):
            return time.time() - self.business_config_loaded_at
        return None

    def get_coordination_config_age(self) -> Optional[float]:
        """
        Get age of coordination service config in seconds since it was loaded.

        Returns:
            Age in seconds, or None if not loaded
        """
        if hasattr(self, "coordination_service_config_loaded_at"):
            return time.time() - self.coordination_service_config_loaded_at
        return None

    def get_business_config_uri(self) -> Optional[str]:
        """Construct business_config_uri from logic_directory_location + business_config_filename.

        Falls back to legacy business_config_uri for backward compatibility.
        """
        # New structure: construct from base + filename
        logic_dir = self.local_config.get("logic_directory_location")
        config_filename = self.local_config.get(
            "business_config_filename", "business-config.yaml"
        )

        if logic_dir:
            # Construct URI: {logic_directory_location}/{business_config_filename}
            separator = "/" if not logic_dir.endswith("/") else ""
            return f"{logic_dir}{separator}{config_filename}"

        # Backward compatibility: direct business_config_uri
        return self.local_config.get("business_config_uri")

    def get_logic_base_uri(self) -> Optional[str]:
        """Derive logic base URI by stripping filename from business_config_uri.

        Returns:
            Base URI (e.g. 'https://example.com/logic/') or None if local path
        """
        uri = self.get_business_config_uri()
        if not uri:
            return None

        parsed = urllib.parse.urlparse(uri)
        if parsed.scheme in ("http", "https"):
            # Remote — strip filename to get base
            base = uri.rsplit("/", 1)[0]
            return base + "/" if not base.endswith("/") else base
        return None

    def get_logic_cache_max_age(self) -> int:
        """Get maximum logic cache age in seconds before automatic refresh.

        Reads logic_cache_max_age_seconds from local-config.yaml.
        Defaults to 1800 (30 minutes) if not set.
        """
        return int(self.local_config.get("logic_cache_max_age_seconds", 1800))

    def get_rules_base_uri(self) -> Optional[str]:
        """Get rules base URI from business config."""
        return self.business_config.get("rules_base_uri")

    def resolve_rule_uri(self, entity_type: str, rule_id: str) -> str:
        """
        Resolve rule URI from entity type and rule ID.

        Logic:
        1. If rules_base_uri exists in business config: use it (remote rules)
        2. Otherwise: construct from logic_directory_location + rules_directory

        Args:
            entity_type: Entity type (loan, facility, deal)
            rule_id: Rule ID (rule_001_v1)

        Returns:
            Absolute URI or path to rule file
        """
        base_uri = self.get_rules_base_uri()
        rule_filename = f"{rule_id}.py"

        if base_uri:
            # Remote rules via rules_base_uri in business config
            if not base_uri.endswith("/"):
                base_uri += "/"
            return f"{base_uri}{entity_type}/{rule_filename}"

        # Local rules: construct from logic_directory_location + rules_directory
        logic_dir = self.local_config.get("logic_directory_location")
        rules_subdir = self.local_config.get("rules_directory", "rules")

        if logic_dir:
            # New structure: {logic_directory_location}/{rules_directory}/{entity_type}/{rule_id}.py
            separator = "/" if not logic_dir.endswith("/") else ""
            return f"{logic_dir}{separator}{rules_subdir}/{entity_type}/{rule_filename}"

        # Backward compatibility fallback
        rules_dir = self.local_config.get("master_rules_directory", "../logic/rules")
        return f"{rules_dir}/{entity_type}/{rule_filename}"
