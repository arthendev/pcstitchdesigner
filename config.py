"""Application configuration management with persistent storage."""

import json
import os
from pathlib import Path


class Config:
    """Manages application configuration with file persistence and versioning.
    
    Supports backwards-compatibility through version tracking. Future releases
    can check the version and migrate old configurations as needed.
    """

    # Current configuration file version
    CURRENT_VERSION = 1

    def __init__(self, config_dir=None):
        """Initialize configuration.
        
        Args:
            config_dir: Directory to store config file. If None, uses user's
                       home directory under .pc_stitch_designer/
        """
        if config_dir is None:
            config_dir = Path.home() / ".pc_stitch_designer"
        self._config_dir = Path(config_dir)
        self._config_file = self._config_dir / "config.json"

        # Initialize defaults
        self._data = {
            "version": self.CURRENT_VERSION,
            "recent_files": [],
            # Machine preferences
            "machine_model": "PFAFF Creative 7570",
            "machine_port": "",
            "machine_high_speed": False,
            # Stitch display preferences (9mm / MAXI)
            "stitch_line_color": "#000000",
            "stitch_line_width": "medium",
            "stitch_point_color": "#000000",
            "stitch_point_size": "medium",
            "stitch_grid_color": "#dcdcdc",
            "stitch_show_stitch_points": True,
            "stitch_show_grid": True,
            "embroidery_line_width": "medium",
            "embroidery_point_size": "medium",
            "embroidery_grid_color": "#dcdcdc",
            "embroidery_show_stitch_points": False,
            "embroidery_show_grid": False,
            # General preferences
            "auto_update_check": "weekly",
            "last_update_check": "",
            # Language preference
            "language": "system",
            # Extended preferences
            "log_communication": False,
        }

        # Try to load existing config
        self.load()

    def load(self):
        """Load configuration from file.
        
        If file doesn't exist, uses default configuration.
        If file version differs, maintains compatibility and updates to current version.
        """
        if not self._config_file.exists():
            return

        try:
            with open(self._config_file, "r") as f:
                data = json.load(f)

            # Version check for future compatibility
            file_version = data.get("version", 1)
            if file_version != self.CURRENT_VERSION:
                # Could implement migration logic here for future versions
                print(f"Config version {file_version} detected, current is {self.CURRENT_VERSION}")

            # Merge loaded data with defaults (allows new fields to be added)
            self._data.update(data)

        except (json.JSONDecodeError, IOError, OSError) as e:
            print(f"Warning: Could not load config file: {e}")

    def save(self):
        """Save configuration to file.
        
        Creates directory if it doesn't exist.
        """
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            with open(self._config_file, "w") as f:
                json.dump(self._data, f, indent=2)
        except (IOError, OSError) as e:
            print(f"Warning: Could not save config file: {e}")

    def get_recent_files(self):
        """Get list of recent files.
        
        Returns:
            List of file paths in order of recency (most recent first).
        """
        return self._data.get("recent_files", [])

    def add_recent_file(self, path):
        """Add or move file to top of recent files list.
        
        Args:
            path: File path to add.
        """
        recent = self._data.get("recent_files", [])
        # Remove if already in list
        if path in recent:
            recent.remove(path)
        # Add to beginning
        recent.insert(0, path)
        # Limit to 20 files
        self._data["recent_files"] = recent[:20]

    def clear_recent_files(self):
        """Clear the recent files list."""
        self._data["recent_files"] = []

    def set(self, key, value):
        """Set a configuration value.
        
        Args:
            key: Configuration key.
            value: Configuration value.
        """
        self._data[key] = value

    def get(self, key, default=None):
        """Get a configuration value.
        
        Args:
            key: Configuration key.
            default: Default value if key not found.
        
        Returns:
            Configuration value or default.
        """
        return self._data.get(key, default)

    # ── Preferences helpers ──

    def get_machine_preferences(self):
        """Return machine preference values as a dict."""
        return {
            "model": self._data.get("machine_model", "PFAFF Creative 7570"),
            "port": self._data.get("machine_port", ""),
            "high_speed": self._data.get("machine_high_speed", False),
        }

    def set_machine_preferences(self, model, port, high_speed):
        """Persist machine preferences."""
        self._data["machine_model"] = model
        self._data["machine_port"] = port
        self._data["machine_high_speed"] = bool(high_speed)

    def get_display_preferences(self):
        """Return display preference values as a dict."""
        return {
            "line_color": self._data.get("stitch_line_color", "#000000"),
            "line_width": self._data.get("stitch_line_width", "medium"),
            "point_color": self._data.get("stitch_point_color", "#000000"),
            "point_size": self._data.get("stitch_point_size", "big"),
            "grid_color": self._data.get("stitch_grid_color", "#dcdcdc"),
            "show_stitch_points": self._data.get("stitch_show_stitch_points", True),
            "show_grid": self._data.get("stitch_show_grid", True),
            "embroidery_line_width": self._data.get("embroidery_line_width", "medium"),
            "embroidery_point_size": self._data.get("embroidery_point_size", "medium"),
            "embroidery_grid_color": self._data.get("embroidery_grid_color", "#dcdcdc"),
            "embroidery_show_stitch_points": self._data.get("embroidery_show_stitch_points", False),
            "embroidery_show_grid": self._data.get("embroidery_show_grid", False),
        }

    def set_display_preferences(self, line_color, line_width, point_color,
                                point_size, grid_color, show_stitch_points,
                                show_grid, embroidery_line_width,
                                embroidery_point_size, embroidery_grid_color,
                                embroidery_show_stitch_points,
                                embroidery_show_grid):
        """Persist display preferences."""
        self._data["stitch_line_color"] = line_color
        self._data["stitch_line_width"] = line_width
        self._data["stitch_point_color"] = point_color
        self._data["stitch_point_size"] = point_size
        self._data["stitch_grid_color"] = grid_color
        self._data["stitch_show_stitch_points"] = bool(show_stitch_points)
        self._data["stitch_show_grid"] = bool(show_grid)
        self._data["embroidery_line_width"] = embroidery_line_width
        self._data["embroidery_point_size"] = embroidery_point_size
        self._data["embroidery_grid_color"] = embroidery_grid_color
        self._data["embroidery_show_stitch_points"] = bool(embroidery_show_stitch_points)
        self._data["embroidery_show_grid"] = bool(embroidery_show_grid)

    def get_general_preferences(self):
        """Return general preference values as a dict."""
        return {
            "update_check_frequency": self._data.get("auto_update_check", "weekly"),
            "language": self._data.get("language", "system"),
        }

    def set_general_preferences(self, update_check_frequency, language="system"):
        """Persist general preferences."""
        self._data["auto_update_check"] = update_check_frequency
        self._data["language"] = language

    def get_last_update_check(self):
        """Return ISO-format timestamp string of the last update check, or empty string."""
        return self._data.get("last_update_check", "")

    def set_last_update_check(self, timestamp_str):
        """Store the ISO-format timestamp of the last update check."""
        self._data["last_update_check"] = timestamp_str

    def get_extended_preferences(self):
        """Return extended preference values as a dict."""
        return {
            "log_communication": self._data.get("log_communication", False),
        }

    def set_extended_preferences(self, log_communication):
        """Persist extended preferences."""
        self._data["log_communication"] = bool(log_communication)
