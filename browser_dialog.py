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
from model import ELEM_STITCH, ELEM_AUTO, ELEM_COLOR, ELEM_TRIM, elem_has_coords


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
            painter.drawText(draw_rect, Qt.AlignCenter, self.tr("Select a .pcd or .pcq file"))
            return

        elements = self._pattern.elements
        coord_elems = [(e[1], e[2]) for e in elements if elem_has_coords(e)]
        if not coord_elems:
            painter.setPen(QColor(120, 120, 120))
            painter.drawText(draw_rect, Qt.AlignCenter, self.tr("No stitch points"))
            return

        bounds = self._pattern.get_stitch_bounds()
        if bounds is None:
            painter.setPen(QColor(120, 120, 120))
            painter.drawText(draw_rect, Qt.AlignCenter, self.tr("No stitch points"))
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

        # Draw connecting lines, respecting color changes and trims
        current_color_idx = 0
        trim_pending = False
        last_sx, last_sy = None, None
        for elem in elements:
            kind = elem[0]
            if kind == ELEM_COLOR:
                current_color_idx = elem[1]
                continue
            if kind == ELEM_TRIM:
                trim_pending = True
                continue
            if not elem_has_coords(elem):
                continue

            x, y = elem[1], elem[2]
            sx, sy = to_screen(x, y)

            if last_sx is not None and not trim_pending:
                if self._pattern.has_palette and current_color_idx < len(self._pattern.colors):
                    r, g, b = self._pattern.colors[current_color_idx]
                    line_color = QColor(r, g, b)
                else:
                    line_color = QColor(0, 0, 0)
                painter.setPen(QPen(line_color, 1))
                painter.drawLine(int(last_sx), int(last_sy), int(sx), int(sy))

            last_sx, last_sy = sx, sy
            trim_pending = False

        # Draw start/end markers for stitch formats on top of lines.
        if self._pattern.stitch_type in ("9mm", "MAXI") and coord_elems:
            marker_radius = 3
            marker_outline = QPen(QColor(255, 255, 255), 1)
            painter.setPen(marker_outline)

            start_x, start_y = to_screen(coord_elems[0][0], coord_elems[0][1])
            painter.setBrush(QColor(0, 180, 0))
            painter.drawEllipse(
                int(start_x - marker_radius),
                int(start_y - marker_radius),
                marker_radius * 2,
                marker_radius * 2,
            )

            end_x, end_y = to_screen(coord_elems[-1][0], coord_elems[-1][1])
            painter.setBrush(QColor(220, 0, 0))
            painter.drawEllipse(
                int(end_x - marker_radius),
                int(end_y - marker_radius),
                marker_radius * 2,
                marker_radius * 2,
            )


class PatternBrowserDialog(QFileDialog):
    """Open-file dialog extended with a live stitch pattern preview panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Open Stitch Pattern"))
        self.resize(1100, 600)
        self.setFileMode(QFileDialog.ExistingFile)
        self.setNameFilter("All Supported Files (*.pcd *.pcq *.pcs);;Stitch Files (*.pcd *.pcq);;9mm Stitch Files (*.pcd);;MAXI Stitch Files (*.pcq);;Embroidery Files (*.pcs);;All Files (*)")
        self.selectNameFilter("All Supported Files (*.pcd *.pcq *.pcs)")
        self.setOption(QFileDialog.DontUseNativeDialog, True)

        self._preview_widget = PatternPreviewWidget(self)
        self._size_label = QLabel(self.tr("Size: -"), self)
        self._size_label.setWordWrap(True)
        self._info_label = QLabel(self.tr("Select a .pcd, .pcq, or .pcs file"), self)
        self._info_label.setWordWrap(True)

        preview_panel = QWidget(self)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(6, 6, 6, 6)
        preview_layout.setSpacing(8)
        preview_layout.addWidget(QLabel(self.tr("Preview"), self))
        preview_layout.addWidget(self._preview_widget, 1)
        preview_layout.addWidget(self._size_label)
        preview_layout.addWidget(self._info_label)

        layout = self.layout()
        if isinstance(layout, QGridLayout):
            layout.addWidget(preview_panel, 0, layout.columnCount(), layout.rowCount(), 1)

        self.currentChanged.connect(self._on_current_changed)

    def _on_current_changed(self, path):
        if not path or not os.path.isfile(path):
            self._preview_widget.clear()
            self._size_label.setText(self.tr("Size: -"))
            self._info_label.setText(self.tr("Select a .pcd, .pcq, or .pcs file"))
            return

        ext = os.path.splitext(path)[1].lower()
        if ext not in (".pcd", ".pcq", ".pcs"):
            self._preview_widget.clear()
            self._size_label.setText(self.tr("Size: -"))
            self._info_label.setText(self.tr("Unsupported file type"))
            return

        try:
            pattern = file_io.load_pattern(path)
        except Exception as exc:
            self._preview_widget.clear()
            self._size_label.setText(self.tr("Size: -"))
            self._info_label.setText(self.tr("Preview unavailable: {0}").format(exc))
            return

        self._preview_widget.set_pattern(pattern)
        width_mm, height_mm = pattern.get_stitch_size_mm()
        self._size_label.setText(self.tr("Size: {0:.2f} x {1:.2f} mm").format(width_mm, height_mm))
        info_text = (
            self.tr("Type: {0}").format(pattern.stitch_type)
            + self.tr(", Elements: {0}").format(len(pattern.elements))
        )
        if pattern.has_palette:
            info_text += self.tr(", Colors: {0}").format(len(pattern.colors))
        self._info_label.setText(info_text)

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
