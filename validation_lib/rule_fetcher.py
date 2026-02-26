"""Rule fetching and caching for remote rule files."""

import hashlib
import importlib.util
import urllib.request
import urllib.parse
from pathlib import Path


class RuleFetcher:
    """Fetches and caches rule files from URIs."""

    def __init__(self, cache_dir: str = "/tmp/validation-cache/rules"):
        """
        Initialize rule fetcher.

        Args:
            cache_dir: Directory for caching rule files
        """
        self.cache_dir = Path(cache_dir)
        # Directory is created lazily — only when a remote rule is actually fetched.

    def fetch_rule(self, rule_uri: str) -> Path:
        """
        Fetch rule file from URI (with caching).

        Logic:
        1. If file:// or relative path → resolve to absolute path, return directly
        2. If http(s):// → check cache, fetch if missing, return cached path

        Args:
            rule_uri: Rule URI or path

        Returns:
            Path to rule file on local filesystem
        """
        parsed = urllib.parse.urlparse(rule_uri)

        # Handle relative paths (backward compat)
        if not parsed.scheme:
            # Relative path - resolve and return
            return Path(rule_uri).resolve()

        # Handle file:// URIs
        if parsed.scheme == "file":
            path = urllib.parse.unquote(parsed.path)
            return Path(path).resolve()

        # Handle http(s):// URIs - cache them
        # TODO: Add integrity verification (e.g. hash pinning) before executing
        # remote code. Currently we trust the HTTPS source blindly.
        if parsed.scheme in ("http", "https"):
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_key = hashlib.sha256(rule_uri.encode()).hexdigest()
            cache_path = self.cache_dir / f"{cache_key}.py"

            if cache_path.exists():
                # Use cached version
                return cache_path
            else:
                # Fetch and cache
                content = self._fetch_uri(rule_uri)
                cache_path.write_text(content)
                return cache_path

        raise ValueError(f"Unsupported URI scheme: {parsed.scheme} in {rule_uri}")

    def _fetch_uri(self, uri: str) -> str:
        """Fetch content from HTTP/HTTPS URI."""
        try:
            with urllib.request.urlopen(uri, timeout=10) as response:
                return response.read().decode("utf-8")
        except Exception as e:
            raise RuntimeError(f"Failed to fetch rule from {uri}: {e}") from e

    def load_rule_module(self, rule_uri: str, rule_id: str):
        """
        Load Python module from rule URI.

        Args:
            rule_uri: Rule URI
            rule_id: Rule ID (for module naming)

        Returns:
            Loaded Python module
        """
        rule_path = self.fetch_rule(rule_uri)

        # Dynamically load module
        module_name = f"rules.dynamic.{rule_id}"
        spec = importlib.util.spec_from_file_location(module_name, rule_path)
        if spec is None or spec.loader is None:
            raise ImportError(
                f"Cannot create module spec for rule {rule_id} at {rule_path}"
            )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        return module
