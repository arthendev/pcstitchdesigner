"""Preferences dialog with Machine and Display tabs."""

import serial.tools.list_ports

from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QTabWidget, QWidget,
    QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QComboBox, QPushButton, QCheckBox, QColorDialog,
    QSizePolicy,
)
from PyQt5.QtGui import QColor, QPixmap, QIcon
from PyQt5.QtCore import Qt


# ── Display defaults ────────────────────────────────────────────────────────

DISPLAY_DEFAULTS = {
    "line_color": "#000000",
    "line_width": "medium",
    "point_color": "#000000",
    "point_size": "medium",
    "grid_color": "#dcdcdc",
}

MACHINE_MODELS = [
    "PFAFF Creative 7570",
    "PFAFF Creative 7560",
    "PFAFF Creative 7550",
    "PFAFF Creative 1475 CD",
]


# ── Helpers ─────────────────────────────────────────────────────────────────

def _color_icon(hex_color: str, size: int = 20) -> QIcon:
    """Return a solid-color QIcon for the given hex color string."""
    px = QPixmap(size, size)
    px.fill(QColor(hex_color))
    return QIcon(px)


def _list_ports():
    """Return list of (device, description) tuples for available serial ports."""
    ports = serial.tools.list_ports.comports()
    return [(p.device, p.description) for p in sorted(ports, key=lambda p: p.device)]


# ── Color button ─────────────────────────────────────────────────────────────

class ColorButton(QPushButton):
    """A button that displays a color swatch and opens a color picker on click."""

    def __init__(self, color: str = "#000000", parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(28, 22)
        self.setToolTip("Click to choose color")
        self._refresh_icon()
        self.clicked.connect(self._pick_color)

    def color(self) -> str:
        return self._color

    def set_color(self, hex_color: str):
        self._color = hex_color
        self._refresh_icon()

    def _refresh_icon(self):
        self.setIcon(_color_icon(self._color, 16))
        self.setIconSize(self.sizeHint())

    def _pick_color(self):
        chosen = QColorDialog.getColor(QColor(self._color), self, "Select Color")
        if chosen.isValid():
            self.set_color(chosen.name())


# ── Machine tab ──────────────────────────────────────────────────────────────

class MachineTab(QWidget):
    def __init__(self, prefs: dict, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        layout.setVerticalSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # Machine model
        self._model_combo = QComboBox()
        for m in MACHINE_MODELS:
            self._model_combo.addItem(m)
        idx = self._model_combo.findText(prefs.get("model", MACHINE_MODELS[0]))
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        layout.addRow("Machine Model:", self._model_combo)

        # COM port
        port_layout = QHBoxLayout()
        self._port_combo = QComboBox()
        self._port_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setFixedWidth(70)
        self._refresh_btn.clicked.connect(self._refresh_ports)
        port_layout.addWidget(self._port_combo)
        port_layout.addWidget(self._refresh_btn)
        layout.addRow("COM Port:", port_layout)
        self._saved_port = prefs.get("port", "")
        self._refresh_ports()

        # High-speed checkbox
        self._high_speed_cb = QCheckBox("High-speed")
        self._high_speed_cb.setChecked(bool(prefs.get("high_speed", False)))
        layout.addRow("", self._high_speed_cb)

    def _refresh_ports(self):
        """Reload available serial ports into the combo box."""
        self._port_combo.clear()
        self._port_combo.addItem("(None)", "")
        ports = _list_ports()
        for device, description in ports:
            label = f"{device} - {description}" if description and description != device else device
            self._port_combo.addItem(label, device)
        # Restore selection
        self._select_port(self._saved_port)

    def _select_port(self, port: str):
        for i in range(self._port_combo.count()):
            if self._port_combo.itemData(i) == port:
                self._port_combo.setCurrentIndex(i)
                return
        self._port_combo.setCurrentIndex(0)

    def values(self) -> dict:
        return {
            "model": self._model_combo.currentText(),
            "port": self._port_combo.currentData() or "",
            "high_speed": self._high_speed_cb.isChecked(),
        }


# ── Display tab ───────────────────────────────────────────────────────────────

class DisplayTab(QWidget):
    def __init__(self, prefs: dict, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        group = QGroupBox("9 mm / MAXI Stitches")
        form = QFormLayout(group)
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(8)

        # Line color
        self._line_color_btn = ColorButton(prefs.get("line_color", DISPLAY_DEFAULTS["line_color"]))
        form.addRow("Line color:", self._line_color_btn)

        # Line width
        self._line_width_combo = QComboBox()
        for opt in ("fine", "medium", "thick"):
            self._line_width_combo.addItem(opt.capitalize(), opt)
        self._select_combo(self._line_width_combo, prefs.get("line_width", DISPLAY_DEFAULTS["line_width"]))
        form.addRow("Line width:", self._line_width_combo)

        # Stitch points color
        self._point_color_btn = ColorButton(prefs.get("point_color", DISPLAY_DEFAULTS["point_color"]))
        form.addRow("Stitch Points Color:", self._point_color_btn)

        # Stitch points size
        self._point_size_combo = QComboBox()
        for opt in ("small", "medium", "big"):
            self._point_size_combo.addItem(opt.capitalize(), opt)
        self._select_combo(self._point_size_combo, prefs.get("point_size", DISPLAY_DEFAULTS["point_size"]))
        form.addRow("Stitch Points Size:", self._point_size_combo)

        # Grid color
        self._grid_color_btn = ColorButton(prefs.get("grid_color", DISPLAY_DEFAULTS["grid_color"]))
        form.addRow("Grid color:", self._grid_color_btn)

        outer.addWidget(group)

        # Restore defaults button
        restore_btn = QPushButton("Restore Defaults")
        restore_btn.clicked.connect(self._restore_defaults)
        restore_layout = QHBoxLayout()
        restore_layout.addStretch()
        restore_layout.addWidget(restore_btn)
        outer.addLayout(restore_layout)
        outer.addStretch()

    @staticmethod
    def _select_combo(combo: QComboBox, value: str):
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    def _restore_defaults(self):
        self._line_color_btn.set_color(DISPLAY_DEFAULTS["line_color"])
        self._select_combo(self._line_width_combo, DISPLAY_DEFAULTS["line_width"])
        self._point_color_btn.set_color(DISPLAY_DEFAULTS["point_color"])
        self._select_combo(self._point_size_combo, DISPLAY_DEFAULTS["point_size"])
        self._grid_color_btn.set_color(DISPLAY_DEFAULTS["grid_color"])

    def values(self) -> dict:
        return {
            "line_color": self._line_color_btn.color(),
            "line_width": self._line_width_combo.currentData(),
            "point_color": self._point_color_btn.color(),
            "point_size": self._point_size_combo.currentData(),
            "grid_color": self._grid_color_btn.color(),
        }


# ── Preferences dialog ────────────────────────────────────────────────────────

class PreferencesDialog(QDialog):
    """Preferences dialog with Machine and Display tabs."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(400)
        self._config = config

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Tab widget
        self._tabs = QTabWidget()
        self._machine_tab = MachineTab(config.get_machine_preferences())
        self._display_tab = DisplayTab(config.get_display_preferences())
        self._tabs.addTab(self._machine_tab, "Machine")
        self._tabs.addTab(self._display_tab, "Display")
        layout.addWidget(self._tabs)

        # OK / Cancel
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        m = self._machine_tab.values()
        self._config.set_machine_preferences(
            model=m["model"],
            port=m["port"],
            high_speed=m["high_speed"],
        )
        d = self._display_tab.values()
        self._config.set_display_preferences(
            line_color=d["line_color"],
            line_width=d["line_width"],
            point_color=d["point_color"],
            point_size=d["point_size"],
            grid_color=d["grid_color"],
        )
        self._config.save()
        self.accept()
