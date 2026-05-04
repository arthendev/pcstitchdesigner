"""Animation window: step-by-step stitch preview with playback controls."""

import os
import time

debug = 0

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QProgressBar, QSlider,
    QPushButton, QLabel, QSizePolicy, QWidget,
)
from PyQt5.QtCore import Qt, QTimer, QPoint
from PyQt5.QtGui import QPainter, QPen, QBrush, QColor, QIcon, QPixmap, QPolygon


_ICONS = os.path.join(os.path.dirname(__file__), "icons")


class AnimationCanvas(QWidget):
    """Read-only canvas that renders the stitch pattern up to *visible_count* points."""

    MARGIN = 16
    LABEL_HEIGHT = 20   # pixels reserved above the border for the size label
    UNITS_PER_MM = 6    # coordinate units per millimetre

    COLOR_BG = QColor(255, 255, 255)
    COLOR_BORDER = QColor(0, 80, 200)
    COLOR_LABEL = QColor(80, 80, 80)
    COLOR_LINE = QColor(0, 0, 0)

    def __init__(self, pattern, parent=None):
        super().__init__(parent)
        self._pattern = pattern
        self._visible_count = 0

        # Cached geometry — invalidated on resize
        self._screen_pts = []    # [(sx, sy), ...] for every point in pattern
        self._bbox_vals = None   # (bx0, by0, bx1, by1)
        self._scale = 1.0
        self._off_x = 0.0
        self._off_y = 0.0

        # Incremental line backing pixmap
        self._lines_pixmap = None   # QPixmap with lines drawn up to _lines_count points
        self._lines_count = 0

        # Per-palette-colour pen cache  {color_index: QPen}
        self._color_pens = {}

        # Pre-built pens / brushes (avoid allocation inside paintEvent)
        self._pen_line   = QPen(self.COLOR_LINE,   2)
        self._pen_border = QPen(self.COLOR_BORDER, 1)
        self._pen_label  = QPen(self.COLOR_LABEL,  1)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(300, 150)

    def set_visible_count(self, n):
        self._visible_count = n
        self.update()

    def resizeEvent(self, event):
        self._invalidate_cache()
        super().resizeEvent(event)

    # ── cache management ──

    def _invalidate_cache(self):
        self._screen_pts = []
        self._bbox_vals = None
        self._lines_pixmap = None
        self._lines_count = 0
        self._color_pens = {}

    def _bbox(self):
        """Return cached (bx0, by0, bx1, by1) bounding box of all pattern points."""
        if self._bbox_vals is not None:
            return self._bbox_vals
        bounds = self._pattern.get_stitch_bounds()
        if bounds is None:
            self._bbox_vals = (0, 0, 6, 6)
            return self._bbox_vals
        bx0, by0, bx1, by1 = bounds
        if bx0 == bx1:
            bx0 = max(0, bx0 - 1); bx1 += 1
        if by0 == by1:
            by0 = max(0, by0 - 1); by1 += 1
        self._bbox_vals = (bx0, by0, bx1, by1)
        return self._bbox_vals

    def _ensure_screen_pts(self):
        """Pre-compute screen coordinates for all pattern points (once per resize)."""
        if self._screen_pts or self.width() == 0 or self.height() == 0:
            return
        bx0, by0, bx1, by1 = self._bbox()
        pw, ph = bx1 - bx0, by1 - by0
        avail_w = max(1, self.width() - 2 * self.MARGIN)
        avail_h = max(1, self.height() - 2 * self.MARGIN - self.LABEL_HEIGHT)
        self._scale = min(avail_w / pw, avail_h / ph)
        draw_w = pw * self._scale
        draw_h = ph * self._scale
        self._off_x = (self.width() - draw_w) / 2
        remaining_h = self.height() - 2 * self.MARGIN - self.LABEL_HEIGHT
        self._off_y = self.MARGIN + self.LABEL_HEIGHT + max(0.0, (remaining_h - draw_h) / 2)
        off_x, off_y, scale = self._off_x, self._off_y, self._scale
        self._screen_pts = [
            (int(off_x + (cx - bx0) * scale), int(off_y + (by1 - cy) * scale))
            for cx, cy in self._pattern.points
        ]

    def _pen_for_segment(self, seg_idx):
        """Return the QPen for segment seg_idx (the line from point seg_idx to seg_idx+1)."""
        if not self._pattern.has_palette:
            return self._pen_line
        color_idx = self._pattern.get_point_color_index(seg_idx)
        if color_idx not in self._color_pens:
            r, g, b = self._pattern.colors[color_idx]
            self._color_pens[color_idx] = QPen(QColor(r, g, b), 2)
        return self._color_pens[color_idx]

    def _draw_segments(self, painter, from_seg, to_seg):
        """Draw line segments [from_seg, to_seg) onto painter.

        Segments are grouped into polylines by colour and split at jump stitches.
        Segment i connects screen points i and i+1.
        """
        pts = self._screen_pts
        jump = self._pattern.jump_stitches
        run = []
        run_pen = None
        for i in range(from_seg, to_seg):
            # A jump at point i+1 means no line from i to i+1
            if (i + 1) in jump:
                if len(run) >= 2:
                    painter.setPen(run_pen)
                    painter.drawPolyline(QPolygon([QPoint(sx, sy) for sx, sy in run]))
                run = []
                run_pen = None
                continue
            pen = self._pen_for_segment(i)
            if not run:
                run = [pts[i], pts[i + 1]]
                run_pen = pen
            elif pen is run_pen:
                run.append(pts[i + 1])
            else:
                # Colour change — flush and start new run, overlapping at junction
                if len(run) >= 2:
                    painter.setPen(run_pen)
                    painter.drawPolyline(QPolygon([QPoint(sx, sy) for sx, sy in run]))
                run = [pts[i], pts[i + 1]]
                run_pen = pen
        if len(run) >= 2:
            painter.setPen(run_pen)
            painter.drawPolyline(QPolygon([QPoint(sx, sy) for sx, sy in run]))

    def _ensure_lines_pixmap(self, n):
        """Incrementally extend the backing pixmap to cover n visible points."""
        size = self.size()
        # Full rebuild if pixmap is stale or we're seeking backward
        if (self._lines_pixmap is None
                or self._lines_pixmap.size() != size
                or n < self._lines_count):
            self._lines_pixmap = QPixmap(size)
            self._lines_pixmap.fill(Qt.transparent)
            self._lines_count = 0

        if n >= 2 and self._lines_count < n:
            # Draw only the new segments onto the pixmap
            from_seg = max(0, self._lines_count - 1)
            to_seg   = n - 1
            p = QPainter(self._lines_pixmap)
            p.setRenderHint(QPainter.Antialiasing)
            self._draw_segments(p, from_seg, to_seg)
            p.end()

        self._lines_count = n

    # ── painting ──

    def paintEvent(self, event):
        self._ensure_screen_pts()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.COLOR_BG)

        bx0, by0, bx1, by1 = self._bbox()
        pw, ph = bx1 - bx0, by1 - by0
        scale  = self._scale
        off_x  = self._off_x
        off_y  = self._off_y

        # Pre-compute border corners once
        sx0  = int(off_x)
        sy_top = int(off_y)
        sx1  = int(off_x + pw * scale)
        sy_bot = int(off_y + ph * scale)

        # Size label above the border
        w_mm = pw / self.UNITS_PER_MM
        h_mm = ph / self.UNITS_PER_MM
        label = f"{w_mm:.1f} \u00d7 {h_mm:.1f} mm"
        painter.setPen(self._pen_label)
        fm = painter.fontMetrics()
        label_w = fm.boundingRect(label).width()
        label_x = int(off_x + (pw * scale - label_w) / 2)
        label_y = sy_top - 4
        painter.drawText(label_x, label_y, label)

        # Border — very thin 1 px line
        painter.setPen(self._pen_border)
        painter.drawRect(sx0, sy_top, sx1 - sx0, sy_bot - sy_top)

        n = min(self._visible_count, len(self._pattern.points))
        if n == 0 or not self._screen_pts:
            painter.end()
            return

        # Lines — blit incremental backing pixmap (O(1) per new frame)
        if n >= 2:
            self._ensure_lines_pixmap(n)
            painter.drawPixmap(0, 0, self._lines_pixmap)

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
        self._play_start_time = None   # wall-clock time when play began
        self._play_start_step = 0      # step count when play began

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
        self._speed_slider.setMaximum(50)
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

        self._debug_label = QLabel("")
        self._debug_label.setAlignment(Qt.AlignCenter)
        self._debug_label.setVisible(False)

        root = QVBoxLayout(self)
        root.addWidget(self._canvas, stretch=1)
        root.addWidget(self._debug_label)
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
            if debug:
                self._play_start_time = time.monotonic()
                self._play_start_step = self._current_step
                self._debug_label.setVisible(False)
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
            if debug and self._play_start_time is not None:
                elapsed = time.monotonic() - self._play_start_time
                stitches = self._current_step - self._play_start_step
                avg_ms = (elapsed / stitches * 1000) if stitches else 0
                self._debug_label.setText(
                    f"[debug] Total time: {elapsed:.3f} s  |  "
                    f"Stitches animated: {stitches}  |  "
                    f"Avg per stitch: {avg_ms:.3f} ms"
                )
                self._debug_label.setVisible(True)
                self._play_start_time = None

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)
