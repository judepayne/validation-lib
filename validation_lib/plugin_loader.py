"""Plugin loading for source-format input adapters."""

import importlib
from pathlib import Path
from typing import Any, Dict


class PluginLoader:
    """Load plugin classes declared in business configuration."""

    def __init__(self, config: dict):
        """
        Initialize plugin loader.

        Args:
            config: Business configuration dict.
        """
        self.config = config
        self.loaded_plugins = {}

    def get_plugin_config(self, plugin_name: str) -> Dict[str, Any]:
        """
        Return plugin configuration by name.

        Args:
            plugin_name: Name from business-config.yaml plugins section.

        Returns:
            Plugin config dict.

        Raises:
            ValueError: If plugin is unknown or config is malformed.
        """
        plugins = self.config.get("plugins", {})
        if plugin_name not in plugins:
            raise ValueError(f"Unknown plugin: {plugin_name}")

        plugin_config = plugins[plugin_name]
        if not isinstance(plugin_config, dict):
            raise ValueError(f"Plugin config for {plugin_name} must be a mapping")
        if not plugin_config.get("file"):
            raise ValueError(f"Plugin config for {plugin_name} missing required file")
        if not plugin_config.get("entity_type"):
            raise ValueError(
                f"Plugin config for {plugin_name} missing required entity_type"
            )
        return plugin_config

    def validate_plugin_for_entity(self, plugin_name: str, entity_type: str) -> dict:
        """
        Validate that a plugin is declared for the requested entity type.

        Args:
            plugin_name: Plugin name from config.
            entity_type: Requested entity type.

        Returns:
            Plugin config dict.

        Raises:
            ValueError: If the plugin targets a different entity type.
        """
        plugin_config = self.get_plugin_config(plugin_name)
        plugin_entity_type = plugin_config["entity_type"]
        if plugin_entity_type != entity_type:
            raise ValueError(
                f"Plugin {plugin_name} is declared for entity_type "
                f"{plugin_entity_type}, not {entity_type}"
            )
        return plugin_config

    def load_plugin(self, plugin_name: str):
        """
        Load and instantiate a plugin by name.

        Args:
            plugin_name: Plugin name from config.

        Returns:
            Plugin instance.

        Raises:
            ValueError: If config is invalid.
            ImportError: If module import fails.
            AttributeError: If class Plugin is not defined.
        """
        if plugin_name in self.loaded_plugins:
            plugin_class = self.loaded_plugins[plugin_name]
            return plugin_class()

        plugin_config = self.get_plugin_config(plugin_name)
        module_name = self._module_name_from_file(plugin_config["file"])

        module = importlib.import_module(module_name)
        if not hasattr(module, "Plugin"):
            raise AttributeError(
                f"Plugin class 'Plugin' not found in module {module_name}"
            )

        plugin_class = getattr(module, "Plugin")
        self.loaded_plugins[plugin_name] = plugin_class
        return plugin_class()

    def get_plugin_error_class(self):
        """
        Return the runtime PluginError class from cached validation logic.

        Returns:
            PluginError class.
        """
        module = importlib.import_module("plugins.base")
        return getattr(module, "PluginError")

    def _module_name_from_file(self, file_path: str) -> str:
        """Convert plugins/vendor.py to plugins.vendor."""
        path = Path(file_path)
        parts = path.parts
        if len(parts) < 2 or parts[0] != "plugins" or path.suffix != ".py":
            raise ValueError(
                f"Plugin file must be a Python file under plugins/: {file_path}"
            )
        return ".".join(path.with_suffix("").parts)
