"""Animation window: step-by-step stitch preview with playback controls."""

import bisect
import math
import os
import time

debug = 0

from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QVBoxLayout, QHBoxLayout, QProgressBar, QSlider,
    QPushButton, QLabel, QSizePolicy, QWidget, QStyle, QStyleOptionProgressBar,
)
from PyQt5.QtCore import Qt, QTimer, QPoint, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QBrush, QColor, QIcon, QPixmap, QPolygon
from config import Config
from model import ELEM_COLOR, elem_has_coords, is_embroidery_type


_ICONS = os.path.join(os.path.dirname(__file__), "icons")

# Playback speed range. The slider itself is logarithmic: embroidery hoops hold
# thousands of stitches, so their range reaches 1000 st/s while the sewing-machine
# style designs (9mm / MAXI) stay at the familiar 100 st/s ceiling.
SPEED_MIN = 5
SPEED_MAX_STANDARD = 100
SPEED_MAX_EMBROIDERY = 1000
SPEED_SLIDER_STEPS = 100   # slider positions between SPEED_MIN and the upper bound

# Up to ~20 st/s one stitch per timer tick looks fine. Faster playback would need
# sub-10 ms timers (unreliable in Qt), so the period is capped and several stitches
# are painted per tick: 50 st/s -> 5 stitches / 100 ms, 100 st/s -> 10 stitches / 100 ms.
TICK_MS = 100
STITCHES_PER_TICK_THRESHOLD = 20


class ClickableProgressBar(QProgressBar):
    """QProgressBar that emits *step_requested* when the user clicks or drags.

    Only the painted bar groove is interactive; the text area is ignored.
    """

    step_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._seeking = False

    def _groove_rect(self):
        """Return the style-computed rect of the bar groove (excludes text area)."""
        opt = QStyleOptionProgressBar()
        self.initStyleOption(opt)
        return self.style().subElementRect(QStyle.SE_ProgressBarGroove, opt, self)

    def _value_at(self, x):
        """Map an x pixel position (clamped to groove) to a progress value."""
        groove = self._groove_rect()
        gx = max(groove.left(), min(groove.right(), x))
        ratio = (gx - groove.left()) / max(1, groove.width())
        span = self.maximum() - self.minimum()
        return self.minimum() + int(round(ratio * span))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._groove_rect().contains(event.pos()):
            self._seeking = True
            self.step_requested.emit(self._value_at(event.x()))
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton) and self._seeking:
            self.step_requested.emit(self._value_at(event.x()))
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._seeking = False
        super().mouseReleaseEvent(event)


class AnimationCanvas(QWidget):
    """Read-only canvas that renders the stitch pattern up to *visible_count* points."""

    MARGIN = 16
    LABEL_HEIGHT = 20   # pixels reserved above the border for the size label
    UNITS_PER_MM = 6    # coordinate units per millimetre

    COLOR_BORDER = QColor(0, 80, 200)
    COLOR_LABEL = QColor(80, 80, 80)
    COLOR_FIRST = QColor(0, 200, 0)    # green  — first stitch
    COLOR_HEAD  = QColor(255, 140, 0)  # orange — current head during animation
    COLOR_LAST  = QColor(200, 0, 0)    # red    — final stitch (when complete)

    _MARKER_STITCH_TYPES = {"9mm", "MAXI"}

    def __init__(self, pattern, background_color, line_color, point_color,
                 parent=None, show_stitches=False):
        """Create the preview canvas.

        Args:
            pattern: The pattern to animate.
            background_color: QColor filling the whole widget (Display settings).
            line_color: QColor for stitch lines of designs without a palette
                (9 mm / MAXI). Embroidery lines follow the thread palette.
            point_color: QColor for interior stitch points (9 mm / MAXI only).
            show_stitches: Whether interior stitch points are drawn.
        """
        super().__init__(parent)
        self._pattern = pattern
        self._visible_count = 0
        self._show_stitches = show_stitches

        # Colours come from the Display preferences so the animation matches the
        # canvas. Embroidery lines are drawn per palette entry, so line_color and
        # point_color only show up for 9 mm / MAXI designs.
        self._color_bg = background_color
        self._color_line = line_color
        self._color_point = point_color

        # Cached geometry — invalidated on resize
        self._screen_pts = []         # [(sx, sy), ...] for every coord element
        self._point_colors = []       # parallel list: active palette index at each screen point
        self._trim_after = set()      # coord-point indices after which the thread is cut
        self._bbox_vals = None         # (bx0, by0, bx1, by1)
        self._scale = 1.0
        self._off_x = 0.0
        self._off_y = 0.0

        # Incremental line backing pixmap
        self._lines_pixmap = None   # QPixmap with lines drawn up to _lines_count points
        self._lines_count = 0

        # Per-palette-colour pen cache  {color_index: QPen}
        self._color_pens = {}

        # Pre-built pens / brushes (avoid allocation inside paintEvent)
        self._pen_line   = QPen(self._color_line,   2)
        self._pen_border = QPen(self.COLOR_BORDER, 1)
        self._pen_label  = QPen(self.COLOR_LABEL,  1)
        self._brush_point = QBrush(self._color_point)
        self._brush_first = QBrush(self.COLOR_FIRST)
        self._brush_head  = QBrush(self.COLOR_HEAD)
        self._brush_last  = QBrush(self.COLOR_LAST)

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
        self._point_colors = []
        self._trim_after = set()
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
        has_palette = self._pattern.has_palette
        last_color_idx = len(self._pattern.colors) - 1
        cur_color = 0
        self._point_colors = []
        new_screen_pts = []
        for e in self._pattern.elements:
            if e[0] == ELEM_COLOR:
                if has_palette:
                    cur_color = min(e[1], last_color_idx)
            elif elem_has_coords(e):
                cx, cy = e[1], e[2]
                new_screen_pts.append(
                    (int(off_x + (cx - bx0) * scale), int(off_y + (by1 - cy) * scale))
                )
                self._point_colors.append(cur_color)
        self._screen_pts = new_screen_pts

        # A colour change between two points means the thread is cut there, so
        # no line may connect them.
        if has_palette:
            self._trim_after = {
                i for i in range(len(self._point_colors) - 1)
                if self._point_colors[i] != self._point_colors[i + 1]
            }
        else:
            self._trim_after = set()

    def _pen_for_segment(self, seg_idx):
        """Return the QPen for segment seg_idx (the line from coord point seg_idx to seg_idx+1)."""
        if not self._pattern.has_palette or seg_idx >= len(self._point_colors):
            return self._pen_line
        color_idx = self._point_colors[seg_idx]
        if color_idx not in self._color_pens:
            r, g, b = self._pattern.colors[color_idx]
            self._color_pens[color_idx] = QPen(QColor(r, g, b), 2)
        return self._color_pens[color_idx]

    def _draw_segments(self, painter, from_seg, to_seg):
        """Draw line segments [from_seg, to_seg) onto painter.

        Segments are grouped into polylines by colour and split at colour changes.
        Segment i connects screen points i and i+1.
        """
        pts = self._screen_pts
        trim = self._trim_after
        run = []
        run_pen = None
        for i in range(from_seg, to_seg):
            # A trim after point i means no line from i to i+1
            if i in trim:
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
        painter.fillRect(self.rect(), self._color_bg)

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

        n = min(self._visible_count, len(self._screen_pts))
        if n == 0 or not self._screen_pts:
            painter.end()
            return

        # Lines — blit incremental backing pixmap (O(1) per new frame)
        if n >= 2:
            self._ensure_lines_pixmap(n)
            painter.drawPixmap(0, 0, self._lines_pixmap)

        # Stitch markers — only for 9mm / MAXI (not embroidery hoops)
        if self._pattern.stitch_type in self._MARKER_STITCH_TYPES:
            r = max(2, int(3 * min(scale / 4.0, 1.5)))
            total = len(self._screen_pts)
            painter.setPen(Qt.NoPen)

            if self._show_stitches:
                # Interior points (all same colour — single brush set)
                if n > 2:
                    painter.setBrush(self._brush_point)
                    for sx, sy in self._screen_pts[1:n - 1]:
                        painter.drawEllipse(sx - r, sy - r, 2 * r, 2 * r)

            # First stitch — green (always visible once n > 0)
            painter.setBrush(self._brush_first)
            sx, sy = self._screen_pts[0]
            painter.drawEllipse(sx - r, sy - r, 2 * r, 2 * r)

            # Current head — orange (replaces red when animation is not yet done)
            if n < total:
                painter.setBrush(self._brush_head)
                sx, sy = self._screen_pts[n - 1]
                painter.drawEllipse(sx - r, sy - r, 2 * r, 2 * r)

            # Last stitch — red (only when animation is complete)
            if n == total:
                painter.setBrush(self._brush_last)
                sx, sy = self._screen_pts[total - 1]
                painter.drawEllipse(sx - r, sy - r, 2 * r, 2 * r)

        painter.end()


class AnimationWindow(QDialog):
    """Stitch animation preview window."""

    def __init__(self, pattern, parent=None, config=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Animate Stitching"))
        self.resize(860, 580)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowMinimizeButtonHint
        )

        self._pattern = pattern
        self._config = config if config is not None else Config()
        self._current_step = 0
        self._playing = False
        self._speed = 30  # stitches per second
        self._max_speed = (
            SPEED_MAX_EMBROIDERY if is_embroidery_type(pattern.stitch_type)
            else SPEED_MAX_STANDARD
        )
        self._play_start_time = None   # wall-clock time when play began
        self._play_start_step = 0      # step count when play began

        # Thread-change stops (embroidery only). The pattern cannot change while
        # this modal dialog is open, so the stops are resolved up front.
        self._color_stop_steps, self._color_stop_index = self._build_color_stops()

        # Timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        # ── Widgets ──

        embroidery = is_embroidery_type(pattern.stitch_type)
        display = self._config.get_display_preferences()
        bg_key = "embroidery_background_color" if embroidery else "background_color"
        points_key = "embroidery_show_stitch_points" if embroidery else "show_stitch_points"
        # Embroidery stitches are drawn in thread colours, so the line/point
        # colours from the Display preferences only apply to 9 mm / MAXI.
        self._canvas = AnimationCanvas(
            pattern,
            background_color=QColor(display[bg_key]),
            line_color=QColor(display["line_color"]),
            point_color=QColor(display["point_color"]),
            show_stitches=bool(display[points_key]),
        )

        self._progress = ClickableProgressBar()
        self._progress.setMinimum(0)
        self._progress.setMaximum(max(1, sum(1 for e in pattern.elements if elem_has_coords(e))))
        self._progress.setValue(0)
        self._progress.setFormat(self.tr("Stitch %v / %m"))
        self._progress.setMinimumWidth(200)
        self._progress.setCursor(Qt.PointingHandCursor)
        self._progress.step_requested.connect(self._on_progress_seek)

        self._speed_label = QLabel(self.tr("Speed: {0} st/s").format(self._speed))
        self._speed_slider = QSlider(Qt.Horizontal)
        self._speed_slider.setMinimum(0)
        self._speed_slider.setMaximum(SPEED_SLIDER_STEPS)
        self._speed_slider.setMinimumWidth(120)
        self._speed_slider.setToolTip(
            self.tr("Playback speed ({0}–{1} stitches/s, logarithmic)").format(
                SPEED_MIN, self._max_speed))
        self._speed_slider.valueChanged.connect(self._on_speed_changed)
        # Set after connecting so _speed and the label follow the slider position.
        self._speed_slider.setValue(self._position_for_speed(self._speed))

        self._stop_on_color_change = QCheckBox(self.tr("Stop on color change"))
        self._stop_on_color_change.setToolTip(
            self.tr("Pause the animation at every thread colour change"))
        self._stop_on_color_change.setVisible(embroidery)
        self._stop_on_color_change.toggled.connect(self._on_stop_on_color_changed)

        self._btn_to_start = QPushButton(QIcon(os.path.join(_ICONS, "player_tostart.svg")), "")
        self._btn_to_start.setToolTip(self.tr("To Start"))
        self._btn_to_start.clicked.connect(self._go_to_start)

        self._btn_play = QPushButton(QIcon(os.path.join(_ICONS, "player_play.svg")), "")
        self._btn_play.setToolTip(self.tr("Play"))
        self._btn_play.clicked.connect(self._toggle_play)

        self._btn_to_end = QPushButton(QIcon(os.path.join(_ICONS, "player_toend.svg")), "")
        self._btn_to_end.setToolTip(self.tr("To End"))
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
        controls.addWidget(self._stop_on_color_change)
        controls.addSpacing(12)
        controls.addWidget(self._btn_to_start)
        controls.addWidget(self._btn_play)
        controls.addWidget(self._btn_to_end)

        self._color_change_label = QLabel("")
        self._color_change_label.setTextFormat(Qt.RichText)
        self._color_change_label.setAlignment(Qt.AlignCenter)
        self._color_change_label.setVisible(False)

        self._debug_label = QLabel("")
        self._debug_label.setAlignment(Qt.AlignCenter)
        self._debug_label.setVisible(False)

        root = QVBoxLayout(self)
        root.addWidget(self._canvas, stretch=1)
        root.addWidget(self._color_change_label)
        root.addWidget(self._debug_label)
        root.addLayout(controls)

        self._refresh()

    # ── Internal helpers ──

    def _total(self):
        return sum(1 for e in self._pattern.elements if elem_has_coords(e))

    def _build_color_stops(self):
        """Map every thread change to the stitch count at which it takes effect.

        The animation counts coordinate points, so an ELEM_COLOR record found
        after n stitches is a stop at n — all n stitches still use the previous
        thread. Records before the first stitch are setup rather than a change
        (a stop at 0 is dropped), and consecutive records share one step where
        the last one wins, exactly how the canvas resolves the active colour.

        Returns:
            (sorted step list, {step: palette index}) for the thread changes.
        """
        stops = {}
        painted = 0
        for e in self._pattern.elements:
            if e[0] == ELEM_COLOR:
                if painted > 0:
                    stops[painted] = e[1]
            elif elem_has_coords(e):
                painted += 1
        return sorted(stops), stops

    def _next_color_stop(self, after, upto):
        """Return the first thread change in (after, upto], or None."""
        i = bisect.bisect_right(self._color_stop_steps, after)
        if i == len(self._color_stop_steps):
            return None
        step = self._color_stop_steps[i]
        return step if step <= upto else None

    def _show_color_change(self, color_index):
        """Show the thread change the animation stopped at, with a colour swatch."""
        if not self._pattern.colors:
            raise ValueError("Cannot show a thread change without a palette")
        color_index = min(color_index, len(self._pattern.colors) - 1)
        r, g, b = self._pattern.colors[color_index]
        self._color_change_label.setText(
            self.tr('<span style="color: rgb({0},{1},{2}); font-size: 18pt;">'
                    "\u25a0</span>&nbsp;Color change to thread index #{3}")
            .format(r, g, b, color_index + 1))
        self._color_change_label.setVisible(True)

    def _hide_color_change(self):
        self._color_change_label.setVisible(False)

    def _speed_for_position(self, position):
        """Map a log-scaled slider position to stitches per second."""
        ratio = self._max_speed / SPEED_MIN
        return SPEED_MIN * ratio ** (position / SPEED_SLIDER_STEPS)

    def _position_for_speed(self, speed):
        """Inverse of :meth:`_speed_for_position` (used to place the slider handle)."""
        ratio = self._max_speed / SPEED_MIN
        return int(round(SPEED_SLIDER_STEPS * math.log(speed / SPEED_MIN) / math.log(ratio)))

    def _timer_interval(self):
        """Timer period in ms for the current speed.

        Below STITCHES_PER_TICK_THRESHOLD the timer fires once per stitch, which
        looks smooth. Faster playback uses a fixed TICK_MS period and paints all
        stitches that became due since the last tick in one go (see _on_tick), so
        the canvas is repainted at most ten times per second.
        """
        if self._speed <= STITCHES_PER_TICK_THRESHOLD:
            return max(1, int(round(1000 / self._speed)))
        return TICK_MS

    def _restart_play_clock(self):
        """Anchor the stitch timeline at the current step and wall-clock time."""
        self._play_start_time = time.monotonic()
        self._play_start_step = self._current_step

    def _refresh(self):
        self._canvas.set_visible_count(self._current_step)
        self._progress.setMaximum(max(1, self._total()))
        self._progress.setValue(self._current_step)

    def _set_playing(self, playing):
        self._playing = playing
        if playing:
            icon_name = "player_pause.svg"
            tip = self.tr("Pause")
            self._restart_play_clock()
            self._hide_color_change()
            self._debug_label.setVisible(False)
            self._timer.start(self._timer_interval())
        else:
            icon_name = "player_play.svg"
            tip = self.tr("Play")
            self._timer.stop()
        self._btn_play.setIcon(QIcon(os.path.join(_ICONS, icon_name)))
        self._btn_play.setToolTip(tip)

    # ── Slots ──

    def _on_speed_changed(self, position):
        self._speed = max(SPEED_MIN, int(round(self._speed_for_position(position))))
        self._speed_label.setText(self.tr("Speed: {0} st/s").format(self._speed))
        if self._playing:
            # Re-anchor the clock so the new rate applies from the current step.
            self._restart_play_clock()
            self._timer.setInterval(self._timer_interval())

    def _on_stop_on_color_changed(self, checked):
        """Drop a stale thread-change notice when the option is switched off."""
        if not checked:
            self._hide_color_change()

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
        self._hide_color_change()
        self._current_step = 0
        self._refresh()

    def _go_to_end(self):
        self._set_playing(False)
        self._hide_color_change()
        self._current_step = self._total()
        self._refresh()

    def _on_progress_seek(self, step):
        self._set_playing(False)
        self._hide_color_change()
        self._current_step = max(0, min(step, self._total()))
        self._refresh()

    def _on_tick(self):
        total = self._total()
        if self._play_start_time is None:
            return
        # Every stitch owed since the clock was anchored is painted in this one
        # repaint: one stitch per tick at low speed, a batch per tick above
        # STITCHES_PER_TICK_THRESHOLD (50 st/s -> 5 stitches / 100 ms).
        due = self._play_start_step + int(
            round((time.monotonic() - self._play_start_time) * self._speed))
        # A batch can cover several thread changes; stop at the first one so the
        # user sees each colour before the next one is stitched over it.
        stop = None
        if self._stop_on_color_change.isChecked():
            stop = self._next_color_stop(self._current_step, min(due, total))
        if stop is not None and stop < total:
            self._current_step = stop
            self._refresh()
            self._set_playing(False)
            self._show_color_change(self._color_stop_index[stop])
            return
        if due > self._current_step:
            self._current_step = min(total, due)
            self._refresh()
        if self._current_step >= total:
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
