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

STITCH_DISPLAY_DEFAULTS = {
    "line_color": "#000000",
    "line_width": "medium",
    "point_color": "#000000",
    "point_size": "medium",
    "grid_color": "#dcdcdc",
    "show_stitch_points": True,
    "show_grid": True,
}

EMBROIDERY_DISPLAY_DEFAULTS = {
    "line_width": "medium",
    "point_size": "medium",
    "grid_color": "#dcdcdc",
    "show_stitch_points": False,
    "show_grid": False,
}

MACHINE_MODELS = [
    "PFAFF Creative 7570",
    # "PFAFF Creative 7560",
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

        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._on_model_changed()

    def _on_model_changed(self):
        is_1475 = self._model_combo.currentText() == "PFAFF Creative 1475 CD"
        if is_1475:
            self._high_speed_cb.setChecked(False)
        self._high_speed_cb.setEnabled(not is_1475)

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

        stitch_group = QGroupBox("9 mm / MAXI Stitches")
        stitch_form = QFormLayout(stitch_group)
        stitch_form.setVerticalSpacing(8)
        stitch_form.setHorizontalSpacing(8)

        # Line color + width
        self._line_color_btn = ColorButton(prefs.get("line_color", STITCH_DISPLAY_DEFAULTS["line_color"]))
        self._line_width_combo = QComboBox()
        for opt in ("fine", "medium", "thick", "very thick"):
            self._line_width_combo.addItem(opt.capitalize(), opt)
        self._select_combo(self._line_width_combo, prefs.get("line_width", STITCH_DISPLAY_DEFAULTS["line_width"]))
        line_row = QHBoxLayout()
        line_row.setSpacing(6)
        line_row.addWidget(self._line_color_btn)
        line_row.addWidget(self._line_width_combo)
        line_row.addStretch()
        stitch_form.addRow("Line:", line_row)

        # Stitch point color + size
        self._point_color_btn = ColorButton(prefs.get("point_color", STITCH_DISPLAY_DEFAULTS["point_color"]))
        self._point_size_combo = QComboBox()
        for opt in ("small", "medium", "large"):
            self._point_size_combo.addItem(opt.capitalize(), opt)
        self._select_combo(self._point_size_combo, prefs.get("point_size", STITCH_DISPLAY_DEFAULTS["point_size"]))
        self._show_stitch_points_cb = QCheckBox("Show by default")
        self._show_stitch_points_cb.setChecked(
            bool(prefs.get("show_stitch_points", STITCH_DISPLAY_DEFAULTS["show_stitch_points"]))
        )
        point_row = QHBoxLayout()
        point_row.setSpacing(6)
        point_row.addWidget(self._point_color_btn)
        point_row.addWidget(self._point_size_combo)
        point_row.addWidget(self._show_stitch_points_cb)
        point_row.addStretch()
        stitch_form.addRow("Stitch Points:", point_row)

        # Grid color
        self._grid_color_btn = ColorButton(prefs.get("grid_color", STITCH_DISPLAY_DEFAULTS["grid_color"]))
        self._show_grid_cb = QCheckBox("Show by default")
        self._show_grid_cb.setChecked(bool(prefs.get("show_grid", STITCH_DISPLAY_DEFAULTS["show_grid"])))
        grid_row = QHBoxLayout()
        grid_row.setSpacing(6)
        grid_row.addWidget(self._grid_color_btn)
        grid_row.addWidget(self._show_grid_cb)
        grid_row.addStretch()
        stitch_form.addRow("Grid:", grid_row)

        outer.addWidget(stitch_group)

        embroidery_group = QGroupBox("Embroidery")
        embroidery_form = QFormLayout(embroidery_group)
        embroidery_form.setVerticalSpacing(8)
        embroidery_form.setHorizontalSpacing(8)

        self._embroidery_line_width_combo = QComboBox()
        for opt in ("fine", "medium", "thick", "very thick"):
            self._embroidery_line_width_combo.addItem(opt.capitalize(), opt)
        self._select_combo(
            self._embroidery_line_width_combo,
            prefs.get("embroidery_line_width", EMBROIDERY_DISPLAY_DEFAULTS["line_width"]),
        )
        embroidery_line_row = QHBoxLayout()
        embroidery_line_row.setSpacing(6)
        embroidery_line_row.addWidget(self._embroidery_line_width_combo)
        embroidery_line_row.addStretch()
        embroidery_form.addRow("Line:", embroidery_line_row)

        self._embroidery_point_size_combo = QComboBox()
        for opt in ("small", "medium", "large"):
            self._embroidery_point_size_combo.addItem(opt.capitalize(), opt)
        self._select_combo(
            self._embroidery_point_size_combo,
            prefs.get("embroidery_point_size", EMBROIDERY_DISPLAY_DEFAULTS["point_size"]),
        )
        self._embroidery_show_stitch_points_cb = QCheckBox("Show by default")
        self._embroidery_show_stitch_points_cb.setChecked(
            bool(
                prefs.get(
                    "embroidery_show_stitch_points",
                    EMBROIDERY_DISPLAY_DEFAULTS["show_stitch_points"],
                )
            )
        )
        embroidery_point_row = QHBoxLayout()
        embroidery_point_row.setSpacing(6)
        embroidery_point_row.addWidget(self._embroidery_point_size_combo)
        embroidery_point_row.addWidget(self._embroidery_show_stitch_points_cb)
        embroidery_point_row.addStretch()
        embroidery_form.addRow("Stitch Points:", embroidery_point_row)

        self._embroidery_grid_color_btn = ColorButton(
            prefs.get("embroidery_grid_color", EMBROIDERY_DISPLAY_DEFAULTS["grid_color"])
        )
        self._embroidery_show_grid_cb = QCheckBox("Show by default")
        self._embroidery_show_grid_cb.setChecked(
            bool(prefs.get("embroidery_show_grid", EMBROIDERY_DISPLAY_DEFAULTS["show_grid"]))
        )
        embroidery_grid_row = QHBoxLayout()
        embroidery_grid_row.setSpacing(6)
        embroidery_grid_row.addWidget(self._embroidery_grid_color_btn)
        embroidery_grid_row.addWidget(self._embroidery_show_grid_cb)
        embroidery_grid_row.addStretch()
        embroidery_form.addRow("Grid:", embroidery_grid_row)

        outer.addWidget(embroidery_group)

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
        self._line_color_btn.set_color(STITCH_DISPLAY_DEFAULTS["line_color"])
        self._select_combo(self._line_width_combo, STITCH_DISPLAY_DEFAULTS["line_width"])
        self._point_color_btn.set_color(STITCH_DISPLAY_DEFAULTS["point_color"])
        self._select_combo(self._point_size_combo, STITCH_DISPLAY_DEFAULTS["point_size"])
        self._grid_color_btn.set_color(STITCH_DISPLAY_DEFAULTS["grid_color"])
        self._show_stitch_points_cb.setChecked(STITCH_DISPLAY_DEFAULTS["show_stitch_points"])
        self._show_grid_cb.setChecked(STITCH_DISPLAY_DEFAULTS["show_grid"])
        self._select_combo(self._embroidery_line_width_combo, EMBROIDERY_DISPLAY_DEFAULTS["line_width"])
        self._select_combo(self._embroidery_point_size_combo, EMBROIDERY_DISPLAY_DEFAULTS["point_size"])
        self._embroidery_grid_color_btn.set_color(EMBROIDERY_DISPLAY_DEFAULTS["grid_color"])
        self._embroidery_show_stitch_points_cb.setChecked(
            EMBROIDERY_DISPLAY_DEFAULTS["show_stitch_points"]
        )
        self._embroidery_show_grid_cb.setChecked(EMBROIDERY_DISPLAY_DEFAULTS["show_grid"])

    def values(self) -> dict:
        return {
            "line_color": self._line_color_btn.color(),
            "line_width": self._line_width_combo.currentData(),
            "point_color": self._point_color_btn.color(),
            "point_size": self._point_size_combo.currentData(),
            "grid_color": self._grid_color_btn.color(),
            "show_stitch_points": self._show_stitch_points_cb.isChecked(),
            "show_grid": self._show_grid_cb.isChecked(),
            "embroidery_line_width": self._embroidery_line_width_combo.currentData(),
            "embroidery_point_size": self._embroidery_point_size_combo.currentData(),
            "embroidery_grid_color": self._embroidery_grid_color_btn.color(),
            "embroidery_show_stitch_points": self._embroidery_show_stitch_points_cb.isChecked(),
            "embroidery_show_grid": self._embroidery_show_grid_cb.isChecked(),
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
            show_stitch_points=d["show_stitch_points"],
            show_grid=d["show_grid"],
            embroidery_line_width=d["embroidery_line_width"],
            embroidery_point_size=d["embroidery_point_size"],
            embroidery_grid_color=d["embroidery_grid_color"],
            embroidery_show_stitch_points=d["embroidery_show_stitch_points"],
            embroidery_show_grid=d["embroidery_show_grid"],
        )
        self._config.save()
        self.accept()
