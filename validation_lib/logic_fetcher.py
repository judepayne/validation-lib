"""Logic package fetching and caching for remote logic/ directory."""

import os
import shutil
import time
import urllib.request
import urllib.parse
import yaml
from pathlib import Path
from typing import Set, Optional


class LogicPackageFetcher:
    """Fetches and caches the entire logic/ package from a remote URL.

    Smart detection:
    - Local path (e.g. ../logic/business-config.yaml): resolve to directory, use directly
    - Remote URL (e.g. https://example.com/logic/business-config.yaml): fetch all
      required files into cache, return cache path

    The file list is derived from business-config.yaml — no manifest needed.
    """

    # Structural files always required for imports to work
    STRUCTURAL_FILES = [
        "rules/base.py",
        "entity_helpers/__init__.py",
        "entity_helpers/version_registry.py",
    ]

    # Hardcoded cache directory for validation-lib
    CACHE_DIR = Path("/tmp/validation-lib/logic")

    def __init__(self):
        """Initialize logic fetcher with fixed cache directory."""
        self.cache_dir = self.CACHE_DIR

    def resolve_logic_dir(self, local_config_path: str) -> str:
        """Resolve logic directory from local config.

        Reads local-config.yaml to find business_config_uri, then:
        - If local path: return the parent directory
        - If remote URL: fetch all logic files into cache, return cache path

        Args:
            local_config_path: Path to local-config.yaml

        Returns:
            Absolute path to logic directory (local or cached)
        """
        # Load local config to get business_config_uri
        with open(local_config_path) as f:
            local_config = yaml.safe_load(f)

        # Get business_config_uri - construct from new structure or use legacy direct value
        logic_dir_location = local_config.get("logic_directory_location")
        business_config_filename = local_config.get("business_config_filename", "business-config.yaml")

        if logic_dir_location:
            # New structure: construct URI from logic_directory_location + business_config_filename
            separator = '/' if not logic_dir_location.endswith('/') else ''
            business_config_uri = f"{logic_dir_location}{separator}{business_config_filename}"
        else:
            # Backward compatibility: direct business_config_uri
            business_config_uri = local_config.get("business_config_uri")

        if not business_config_uri:
            # No business config URI — legacy mode, no logic dir resolution
            return None

        # Determine if local or remote
        parsed = urllib.parse.urlparse(business_config_uri)

        if not parsed.scheme or parsed.scheme == 'file':
            # Local path — resolve to absolute directory
            return self._resolve_local(business_config_uri, local_config_path)
        elif parsed.scheme in ('http', 'https'):
            # Remote URL — fetch into cache
            return self._resolve_remote(business_config_uri)
        else:
            raise ValueError(
                f"Unsupported URI scheme: {parsed.scheme} in {business_config_uri}")

    def _resolve_local(self, uri: str, local_config_path: str) -> str:
        """Resolve local business_config_uri to logic directory path."""
        parsed = urllib.parse.urlparse(uri)

        if parsed.scheme == 'file':
            # file:// URI — extract path
            config_file = urllib.parse.unquote(parsed.path)
        else:
            # Relative path — resolve relative to local config directory
            config_dir = os.path.dirname(os.path.abspath(local_config_path))
            config_file = os.path.join(config_dir, uri)

        # Return the directory containing business-config.yaml
        return os.path.dirname(os.path.abspath(config_file))

    def _resolve_remote(self, business_config_uri: str) -> str:
        """Fetch logic package from remote URL into cache.

        Args:
            business_config_uri: Remote URL to business-config.yaml

        Returns:
            Path to cached logic directory
        """
        # Base URI = business_config_uri minus the filename
        base_uri = business_config_uri.rsplit('/', 1)[0]
        if not base_uri.endswith('/'):
            base_uri += '/'

        # Load the business config (fetch it)
        config_content = self._fetch_uri(business_config_uri)
        business_config = yaml.safe_load(config_content)

        # Derive all required files
        required_files = self.derive_required_files(business_config)

        # Set up cache directory
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Write the business config itself
        config_cache_path = self.cache_dir / "business-config.yaml"
        config_cache_path.write_text(config_content)

        # Fetch each required file
        for rel_path in required_files:
            file_url = f"{base_uri}{rel_path}"
            cache_path = self.cache_dir / rel_path
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                content = self._fetch_uri(file_url)
                cache_path.write_text(content)
            except RuntimeError as e:
                # Log but don't fail — some files may be optional
                import sys
                print(f"Warning: Failed to fetch {file_url}: {e}",
                      file=sys.stderr)

        return str(self.cache_dir)

    @staticmethod
    def derive_required_files(business_config: dict) -> Set[str]:
        """Derive all required file paths from business configuration.

        Parses business-config.yaml to determine what files the logic/
        package contains. No manifest needed.

        Args:
            business_config: Parsed business-config.yaml dict

        Returns:
            Set of relative file paths (e.g. {'rules/loan/rule_001_v1.py'})
        """
        files = set()

        # 1. Structural files (always needed)
        files.update(LogicPackageFetcher.STRUCTURAL_FILES)

        # 2. Rule files from all rulesets
        rulesets = business_config.get('rulesets', {})
        for ruleset_name, ruleset_data in rulesets.items():
            rules_section = ruleset_data.get('rules', {})
            for schema_or_type, rules_list in rules_section.items():
                if not isinstance(rules_list, list):
                    continue
                # Extract entity type from schema URL or use key directly
                entity_type = LogicPackageFetcher._extract_entity_type(
                    schema_or_type)
                # Collect rule IDs (including nested children)
                rule_ids = LogicPackageFetcher._collect_rule_ids(rules_list)
                for rule_id in rule_ids:
                    files.add(f"rules/{entity_type}/{rule_id}.py")

        # 3. Helper files from schema_to_helper_mapping
        for _schema_url, helper_ref in business_config.get(
                'schema_to_helper_mapping', {}).items():
            module_name = helper_ref.split('.')[0]  # "loan_v1.LoanV1" → "loan_v1"
            files.add(f"entity_helpers/{module_name}.py")

        # 4. Helper files from default_helpers
        for _entity_type, helper_ref in business_config.get(
                'default_helpers', {}).items():
            module_name = helper_ref.split('.')[0]
            files.add(f"entity_helpers/{module_name}.py")

        return files

    @staticmethod
    def _extract_entity_type(schema_or_type: str) -> str:
        """Extract entity type from schema URL or plain entity type key.

        Examples:
            "https://raw.githubusercontent.com/.../models/loan.schema.v1.0.0.json" → "loan"
            "https://bank.example.com/schemas/loan/v1.0.0" → "loan"
            "loan" → "loan"
            "facility" → "facility"
        """
        if schema_or_type.startswith('http'):
            path = urllib.parse.urlparse(schema_or_type).path
            segments = [s for s in path.split('/') if s]

            # Filename-based versioning: loan.schema.v1.0.0.json → "loan"
            if segments and segments[-1].endswith('.json'):
                filename = segments[-1]
                entity_type = filename.split('.')[0]
                if entity_type:
                    return entity_type

            # Path-based versioning: .../schemas/loan/v1.0.0 → "loan"
            for i, segment in enumerate(segments):
                if segment.startswith('v') and '.' in segment:
                    if i > 0:
                        return segments[i - 1]

            # Fallback: second-to-last segment
            if len(segments) >= 2:
                return segments[-2]
            return segments[-1] if segments else 'unknown'
        return schema_or_type

    @staticmethod
    def _collect_rule_ids(rules_list: list) -> Set[str]:
        """Recursively collect all rule_ids from a rules list, including children."""
        ids = set()
        for rule in rules_list:
            if isinstance(rule, dict) and 'rule_id' in rule:
                ids.add(rule['rule_id'])
                if 'children' in rule:
                    ids.update(
                        LogicPackageFetcher._collect_rule_ids(rule['children']))
        return ids

    def _fetch_uri(self, uri: str) -> str:
        """Fetch content from HTTP/HTTPS URI."""
        try:
            with urllib.request.urlopen(uri) as response:
                return response.read().decode('utf-8')
        except Exception as e:
            raise RuntimeError(f"Failed to fetch {uri}: {e}")

    def get_cache_age(self) -> Optional[float]:
        """Get age of cached logic in seconds.

        Returns:
            Age in seconds since cache was created/modified, or None if not cached

        Example:
            age = fetcher.get_cache_age()
            if age and age > 3600:  # Older than 1 hour
                fetcher.clear_cache()
        """
        if not self.cache_dir.exists():
            return None

        cache_time = os.path.getmtime(str(self.cache_dir))
        return time.time() - cache_time

    def clear_cache(self):
        """Delete entire cache directory.

        Used by reload_logic() to force fresh fetch of logic.
        """
        if self.cache_dir.exists():
            shutil.rmtree(str(self.cache_dir))
