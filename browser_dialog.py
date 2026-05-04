"""File browser dialog with live stitch pattern preview."""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QFileDialog,
    QDialog,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

import file_io


class PatternPreviewWidget(QWidget):
    """Lightweight stitch pattern preview renderer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pattern = None
        self.setMinimumSize(340, 240)

    def clear(self):
        self._pattern = None
        self.update()

    def set_pattern(self, pattern):
        self._pattern = pattern
        self.update()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(250, 250, 250))

        draw_rect = self.rect().adjusted(12, 12, -12, -12)
        if draw_rect.width() <= 0 or draw_rect.height() <= 0:
            return

        if self._pattern is None:
            painter.setPen(QColor(120, 120, 120))
            painter.drawText(draw_rect, Qt.AlignCenter, "Select a .pcd or .pcq file")
            return

        points = self._pattern.points
        if not points:
            painter.setPen(QColor(120, 120, 120))
            painter.drawText(draw_rect, Qt.AlignCenter, "No stitch points")
            return

        bounds = self._pattern.get_stitch_bounds()
        if bounds is None:
            painter.setPen(QColor(120, 120, 120))
            painter.drawText(draw_rect, Qt.AlignCenter, "No stitch points")
            return

        min_x, min_y, max_x, max_y = bounds
        stitch_w = max_x - min_x
        stitch_h = max_y - min_y

        draw_units_w = max(1.0, float(stitch_w))
        draw_units_h = max(1.0, float(stitch_h))

        scale = min(draw_rect.width() / draw_units_w, draw_rect.height() / draw_units_h)
        if scale <= 0:
            return

        pattern_w = draw_units_w * scale
        pattern_h = draw_units_h * scale
        origin_x = draw_rect.left() + (draw_rect.width() - pattern_w) / 2
        origin_y = draw_rect.top() + (draw_rect.height() - pattern_h) / 2

        def to_screen(x, y):
            if stitch_w == 0:
                sx = origin_x + pattern_w / 2
            else:
                sx = origin_x + (x - min_x) * scale

            if stitch_h == 0:
                sy = origin_y + pattern_h / 2
            else:
                sy = origin_y + (max_y - y) * scale
            return sx, sy

        border_pen = QPen(QColor(0, 80, 200), 1)
        painter.setPen(border_pen)
        painter.drawRect(int(origin_x), int(origin_y), int(pattern_w), int(pattern_h))

        if len(points) >= 2:
            line_pen = QPen(QColor(0, 0, 0), 1)
            painter.setPen(line_pen)
            for idx in range(len(points) - 1):
                x1, y1 = points[idx]
                x2, y2 = points[idx + 1]
                sx1, sy1 = to_screen(x1, y1)
                sx2, sy2 = to_screen(x2, y2)
                painter.drawLine(int(sx1), int(sy1), int(sx2), int(sy2))


class PatternBrowserDialog(QFileDialog):
    """Open-file dialog extended with a live stitch pattern preview panel."""

    def __init__(self, parent=None):
        super().__init__(parent, "Open Stitch Pattern")
        self.resize(1100, 600)
        self.setFileMode(QFileDialog.ExistingFile)
        self.setNameFilter("Stitch Files (*.pcd *.pcq);;All Files (*)")
        self.selectNameFilter("Stitch Files (*.pcd *.pcq)")
        self.setOption(QFileDialog.DontUseNativeDialog, True)

        self._preview_widget = PatternPreviewWidget(self)
        self._info_label = QLabel("Select a .pcd or .pcq file", self)
        self._info_label.setWordWrap(True)

        preview_panel = QWidget(self)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(6, 6, 6, 6)
        preview_layout.setSpacing(8)
        preview_layout.addWidget(QLabel("Preview", self))
        preview_layout.addWidget(self._preview_widget, 1)
        preview_layout.addWidget(self._info_label)

        layout = self.layout()
        if isinstance(layout, QGridLayout):
            layout.addWidget(preview_panel, 0, layout.columnCount(), layout.rowCount(), 1)

        self.currentChanged.connect(self._on_current_changed)

    def _on_current_changed(self, path):
        if not path or not os.path.isfile(path):
            self._preview_widget.clear()
            self._info_label.setText("Select a .pcd or .pcq file")
            return

        ext = os.path.splitext(path)[1].lower()
        if ext not in (".pcd", ".pcq"):
            self._preview_widget.clear()
            self._info_label.setText("Unsupported file type")
            return

        try:
            pattern = file_io.load_pattern(path)
        except Exception as exc:
            self._preview_widget.clear()
            self._info_label.setText(f"Preview unavailable: {exc}")
            return

        self._preview_widget.set_pattern(pattern)
        width_mm, height_mm = pattern.get_stitch_size_mm()
        self._info_label.setText(
            f"Type: {pattern.stitch_type}"
            f", Size: {width_mm:.2f} x {height_mm:.2f} mm"
            f", Stitches: {len(pattern.points)}"
        )

    @staticmethod
    def getOpenFileName(parent=None, directory=""):
        dialog = PatternBrowserDialog(parent)
        if directory:
            dialog.setDirectory(directory)

        if dialog.exec_() == QDialog.Accepted:
            selected = dialog.selectedFiles()
            if selected:
                return selected[0], dialog.selectedNameFilter()

        return "", ""
