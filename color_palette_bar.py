"""Vertical color palette toolbar showing thread colors from the active pattern."""

from PyQt5.QtWidgets import QToolBar, QLabel
from PyQt5.QtGui import QPixmap, QColor
from PyQt5.QtCore import Qt

_SWATCH_SIZE = 28  # pixels per color swatch


class ColorPaletteBar(QToolBar):
    """Vertical toolbar displaying a square swatch for each palette color.

    Hidden by default; call :meth:`set_colors` to populate and show, or
    call ``setVisible(False)`` to hide.
    """

    def __init__(self, parent=None):
        super().__init__("Color Palette", parent)
        self.setMovable(False)
        self.setOrientation(Qt.Vertical)
        self.setVisible(False)

    def set_colors(self, colors):
        """Populate the bar with color swatches.

        Args:
            colors: list of ``(r, g, b)`` tuples (values 0–255).
        """
        self.clear()
        for idx, (r, g, b) in enumerate(colors):
            lbl = QLabel()
            lbl.setFixedSize(_SWATCH_SIZE, _SWATCH_SIZE)
            px = QPixmap(_SWATCH_SIZE, _SWATCH_SIZE)
            px.fill(QColor(r, g, b))
            lbl.setPixmap(px)
            lbl.setToolTip(self.tr("Color {0}: RGB({1}, {2}, {3})").format(idx + 1, r, g, b))
            self.addWidget(lbl)
