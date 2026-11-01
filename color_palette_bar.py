"""Vertical color palette toolbar showing thread colors from the active pattern."""

from PyQt5.QtWidgets import QToolBar, QLabel
from PyQt5.QtGui import QPainter, QPixmap, QColor
from PyQt5.QtCore import Qt, pyqtSignal

_SWATCH_SIZE = 28  # pixels per color swatch
# A swatch can match the window background exactly (white thread on a white
# background), and then only this outline shows where the swatch is.
_SWATCH_BORDER_RGB = (0, 0, 0)


class _ColorSwatch(QLabel):
    """Square color patch that reports left clicks.

    QLabel has no ``clicked`` signal, and a bare swatch is all the palette
    needs — no button chrome, so the colours stay the visual focus. The 1-pixel
    black outline is the one exception: it keeps a swatch distinguishable from
    the bar behind it.
    """

    clicked = pyqtSignal()

    def __init__(self, rgb, bottom_border=True, parent=None):
        """Create the swatch.

        Args:
            rgb: ``(r, g, b)`` tuple of the colour to show.
            bottom_border: Draw the bottom edge too. Pass False for a swatch
                that is followed by another one, whose top edge already draws
                the shared separator — two outlines there would read as 2 px.
        """
        super().__init__(parent)
        r, g, b = rgb
        self.setFixedSize(_SWATCH_SIZE, _SWATCH_SIZE)
        pixmap = QPixmap(_SWATCH_SIZE, _SWATCH_SIZE)
        pixmap.fill(QColor(r, g, b))
        painter = QPainter(pixmap)
        painter.setPen(QColor(*_SWATCH_BORDER_RGB))
        far = _SWATCH_SIZE - 1
        painter.drawLine(0, 0, far, 0)       # top
        painter.drawLine(0, 0, 0, far)       # left
        painter.drawLine(far, 0, far, far)   # right
        if bottom_border:
            painter.drawLine(0, far, far, far)
        painter.end()
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
            swatch = _ColorSwatch(rgb,
                                  bottom_border=idx == len(self._colors) - 1)
            swatch.setCursor(Qt.PointingHandCursor)
            swatch.setToolTip(
                self.tr("Color {0}: RGB({1}, {2}, {3}) — click to change")
                .format(idx + 1, r, g, b)
            )
            swatch.clicked.connect(
                lambda index=idx: self.color_clicked.emit(index)
            )
            self.addWidget(swatch)
