"""Vertical color palette toolbar showing thread colors from the active pattern."""

from PyQt5.QtWidgets import QToolBar, QLabel
from PyQt5.QtGui import QPixmap, QColor
from PyQt5.QtCore import Qt, pyqtSignal

_SWATCH_SIZE = 28  # pixels per color swatch


class _ColorSwatch(QLabel):
    """Square color patch that reports left clicks.

    QLabel has no ``clicked`` signal, and a bare swatch is all the palette
    needs — no button chrome, so the colours stay the visual focus.
    """

    clicked = pyqtSignal()

    def __init__(self, rgb, parent=None):
        super().__init__(parent)
        r, g, b = rgb
        self.setFixedSize(_SWATCH_SIZE, _SWATCH_SIZE)
        pixmap = QPixmap(_SWATCH_SIZE, _SWATCH_SIZE)
        pixmap.fill(QColor(r, g, b))
        self.setPixmap(pixmap)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ColorPaletteBar(QToolBar):
    """Vertical toolbar displaying a square swatch for each palette color.

    Hidden by default; call :meth:`set_colors` to populate and show, or
    call ``setVisible(False)`` to hide.

    Signals:
        color_clicked (int): Emitted with the zero-based palette index when the
            user clicks a swatch.
    """

    color_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__("Color Palette", parent)
        self.setMovable(False)
        self.setOrientation(Qt.Vertical)
        self.setVisible(False)
        self._colors = []

    def set_colors(self, colors):
        """Populate the bar with color swatches.

        Args:
            colors: list of ``(r, g, b)`` tuples (values 0–255).
        """
        self._colors = [tuple(c) for c in colors]
        self.clear()
        for idx, rgb in enumerate(self._colors):
            r, g, b = rgb
            swatch = _ColorSwatch(rgb)
            swatch.setCursor(Qt.PointingHandCursor)
            swatch.setToolTip(
                self.tr("Color {0}: RGB({1}, {2}, {3}) — click to change")
                .format(idx + 1, r, g, b)
            )
            swatch.clicked.connect(
                lambda index=idx: self.color_clicked.emit(index)
            )
            self.addWidget(swatch)
