"""Animation window: step-by-step stitch preview with playback controls."""

import os

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QProgressBar, QSlider,
    QPushButton, QLabel, QSizePolicy, QWidget,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter, QPen, QBrush, QColor, QIcon


_ICONS = os.path.join(os.path.dirname(__file__), "icons")


class AnimationCanvas(QWidget):
    """Read-only canvas that renders the stitch pattern up to *visible_count* points."""

    MARGIN = 16

    COLOR_BG = QColor(255, 255, 255)
    COLOR_GRID = QColor(220, 220, 220)
    COLOR_BORDER = QColor(0, 80, 200)
    COLOR_LINE = QColor(0, 0, 0)
    COLOR_POINT = QColor(0, 0, 0)
    COLOR_FIRST = QColor(0, 200, 0)    # green — first stitch
    COLOR_HEAD = QColor(255, 140, 0)   # orange — current animation head
    COLOR_LAST = QColor(200, 0, 0)     # red — final stitch (when complete)

    def __init__(self, pattern, parent=None):
        super().__init__(parent)
        self._pattern = pattern
        self._visible_count = 0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(300, 150)

    def set_visible_count(self, n):
        self._visible_count = n
        self.update()

    # ── helpers ──

    def _compute_scale(self):
        """Scale so the pattern canvas fits inside the widget."""
        cw = self._pattern.CANVAS_WIDTH
        ch = self._pattern.CANVAS_HEIGHT
        if cw == 0 or ch == 0:
            return 1.0
        avail_w = max(1, self.width() - 2 * self.MARGIN)
        avail_h = max(1, self.height() - 2 * self.MARGIN)
        return max(1.0, min(avail_w / cw, avail_h / ch))

    def _offsets(self, scale):
        """Return (off_x, off_y) to centre the drawing area in the widget."""
        draw_w = self._pattern.CANVAS_WIDTH * scale
        draw_h = self._pattern.CANVAS_HEIGHT * scale
        off_x = (self.width() - draw_w) / 2
        off_y = (self.height() - draw_h) / 2
        return off_x, off_y

    def _to_screen(self, cx, cy, scale, off_x, off_y):
        sx = off_x + cx * scale
        sy = off_y + (self._pattern.CANVAS_HEIGHT - cy) * scale
        return int(sx), int(sy)

    # ── painting ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.COLOR_BG)

        scale = self._compute_scale()
        off_x, off_y = self._offsets(scale)

        def ts(cx, cy):
            return self._to_screen(cx, cy, scale, off_x, off_y)

        cw = self._pattern.CANVAS_WIDTH
        ch = self._pattern.CANVAS_HEIGHT

        # Grid (skip for very large canvases to avoid sluggishness)
        if scale >= 2.0:
            grid_pen = QPen(self.COLOR_GRID, 1)
            painter.setPen(grid_pen)
            for x in range(cw + 1):
                painter.drawLine(*ts(x, ch), *ts(x, 0))
            for y in range(ch + 1):
                painter.drawLine(*ts(0, y), *ts(cw, y))

        # Border
        painter.setPen(QPen(self.COLOR_BORDER, 2))
        x0, y0 = ts(0, 0)
        x1, y1 = ts(cw, ch)
        painter.drawRect(x1, y1, x0 - x1, y0 - y1)

        n = min(self._visible_count, len(self._pattern.points))
        if n == 0:
            painter.end()
            return

        # Lines between visible points
        if n >= 2:
            painter.setPen(QPen(self.COLOR_LINE, 2))
            for i in range(n - 1):
                x1c, y1c = self._pattern.points[i]
                x2c, y2c = self._pattern.points[i + 1]
                painter.drawLine(*ts(x1c, y1c), *ts(x2c, y2c))

        # Points
        r = max(2, int(3 * min(scale / 4.0, 1.5)))
        painter.setPen(Qt.NoPen)
        total = len(self._pattern.points)
        for i in range(n):
            xc, yc = self._pattern.points[i]
            sx, sy = ts(xc, yc)
            if i == 0 and n > 1:
                painter.setBrush(QBrush(self.COLOR_FIRST))
            elif i == n - 1 and n == total:
                painter.setBrush(QBrush(self.COLOR_LAST))
            elif i == n - 1:
                painter.setBrush(QBrush(self.COLOR_HEAD))
            else:
                painter.setBrush(QBrush(self.COLOR_POINT))
            painter.drawEllipse(sx - r, sy - r, 2 * r, 2 * r)

        painter.end()


class AnimationWindow(QDialog):
    """Stitch animation preview window."""

    def __init__(self, pattern, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Animate Stitching")
        self.resize(860, 580)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowMinimizeButtonHint
        )

        self._pattern = pattern
        self._current_step = 0
        self._playing = False
        self._speed = 30  # stitches per second

        # Timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        # ── Widgets ──

        self._canvas = AnimationCanvas(pattern)

        self._progress = QProgressBar()
        self._progress.setMinimum(0)
        self._progress.setMaximum(max(1, len(pattern.points)))
        self._progress.setValue(0)
        self._progress.setFormat("Stitch %v / %m")
        self._progress.setMinimumWidth(200)

        self._speed_label = QLabel(f"Speed: {self._speed} st/s")
        self._speed_slider = QSlider(Qt.Horizontal)
        self._speed_slider.setMinimum(5)
        self._speed_slider.setMaximum(100)
        self._speed_slider.setValue(self._speed)
        self._speed_slider.setTickInterval(10)
        self._speed_slider.setTickPosition(QSlider.TicksBelow)
        self._speed_slider.setMinimumWidth(120)
        self._speed_slider.valueChanged.connect(self._on_speed_changed)

        self._btn_to_start = QPushButton(QIcon(os.path.join(_ICONS, "player_tostart.svg")), "")
        self._btn_to_start.setToolTip("To Start")
        self._btn_to_start.clicked.connect(self._go_to_start)

        self._btn_play = QPushButton(QIcon(os.path.join(_ICONS, "player_play.svg")), "")
        self._btn_play.setToolTip("Play")
        self._btn_play.clicked.connect(self._toggle_play)

        self._btn_to_end = QPushButton(QIcon(os.path.join(_ICONS, "player_toend.svg")), "")
        self._btn_to_end.setToolTip("To End")
        self._btn_to_end.clicked.connect(self._go_to_end)

        for btn in (self._btn_to_start, self._btn_play, self._btn_to_end):
            btn.setFixedSize(36, 36)

        # ── Layouts ──

        controls = QHBoxLayout()
        controls.addWidget(self._progress, stretch=3)
        controls.addSpacing(12)
        controls.addWidget(self._speed_label)
        controls.addWidget(self._speed_slider, stretch=1)
        controls.addSpacing(12)
        controls.addWidget(self._btn_to_start)
        controls.addWidget(self._btn_play)
        controls.addWidget(self._btn_to_end)

        root = QVBoxLayout(self)
        root.addWidget(self._canvas, stretch=1)
        root.addLayout(controls)

        self._refresh()

    # ── Internal helpers ──

    def _total(self):
        return len(self._pattern.points)

    def _refresh(self):
        self._canvas.set_visible_count(self._current_step)
        self._progress.setMaximum(max(1, self._total()))
        self._progress.setValue(self._current_step)

    def _set_playing(self, playing):
        self._playing = playing
        if playing:
            icon_name = "player_pause.svg"
            tip = "Pause"
            self._timer.start(max(1, 1000 // self._speed))
        else:
            icon_name = "player_play.svg"
            tip = "Play"
            self._timer.stop()
        self._btn_play.setIcon(QIcon(os.path.join(_ICONS, icon_name)))
        self._btn_play.setToolTip(tip)

    # ── Slots ──

    def _on_speed_changed(self, value):
        self._speed = value
        self._speed_label.setText(f"Speed: {value} st/s")
        if self._playing:
            self._timer.setInterval(max(1, 1000 // self._speed))

    def _toggle_play(self):
        if self._playing:
            self._set_playing(False)
        else:
            # At start or end → restart from beginning
            if self._current_step == 0 or self._current_step >= self._total():
                self._current_step = 0
                self._refresh()
            self._set_playing(True)

    def _go_to_start(self):
        self._set_playing(False)
        self._current_step = 0
        self._refresh()

    def _go_to_end(self):
        self._set_playing(False)
        self._current_step = self._total()
        self._refresh()

    def _on_tick(self):
        if self._current_step < self._total():
            self._current_step += 1
            self._refresh()
        if self._current_step >= self._total():
            self._set_playing(False)

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)
