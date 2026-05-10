"""Custom canvas widget for drawing stitch patterns."""

import os
import math

from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QSize, QRect, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QBrush, QColor, QCursor, QPixmap

from model import StitchPattern, ELEM_STITCH, ELEM_AUTO, ELEM_COLOR, ELEM_TRIM, elem_has_coords


class StitchCanvas(QWidget):
    """Scrollable canvas for stitch point editing.

    Coordinate system:
      Canvas coords: origin at bottom-left, x right, y up.
      Screen coords: origin at top-left, x right, y down (Qt default).
    """

    changed = pyqtSignal()  # emitted when pattern data changes
    cursor_moved = pyqtSignal(float, float)  # canvas x, y under cursor
    selection_changed = pyqtSignal()  # emitted when selection changes
    drag_finished = pyqtSignal()  # emitted after a stitch drag is committed

    MARGIN = 25  # pixel margin around the drawing area

    # Line width mapping
    LINE_WIDTHS = {"fine": 1, "medium": 2, "thick": 3, 'very thick': 4}
    # Point radius mapping
    POINT_RADII = {"small": 2, "medium": 3, "large": 4}

    # Colors (defaults — overridden by apply_display_settings)
    COLOR_GRID = QColor(220, 220, 220)
    COLOR_BORDER = QColor(0, 80, 200)  # Blue frame
    COLOR_POINT = QColor(0, 0, 0)      # Black points
    COLOR_LINE = QColor(0, 0, 0)       # Black lines
    COLOR_BG = QColor(255, 255, 255)
    COLOR_FIRST_POINT = QColor(0, 200, 0)  # Green for first point
    COLOR_LAST_POINT = QColor(200, 0, 0)   # Red for last point

    def __init__(self, pattern, parent=None):
        super().__init__(parent)
        self.pattern = pattern
        self._scale = 10.0  # pixels per canvas unit
        self._tool = None
        self._selection_start = None  # Start index of selection range
        self._selection_end = None    # End index of selection range (inclusive)
        self._view_orientation = "default"  # "default" or "sewing_direction"
        # Display settings (instance-level, updated via apply_display_settings)
        self._color_grid = QColor(220, 220, 220)
        self._color_line = QColor(0, 0, 0)
        self._color_point = QColor(0, 0, 0)
        self._line_width = 2   # medium
        self._point_radius = 4  # medium
        self._show_grid = True
        self._show_stitch_points = True
        self._show_auto_stitch_points = True
        self.snap_normal_to_grid = True
        self._template_image = None  # QPixmap used as tracing underlay
        self._template_resize_mode = False
        self._tpl_cx = 0.0   # center x, scale-normalized: (screen_x - MARGIN) / scale
        self._tpl_cy = 0.0   # center y, scale-normalized
        self._tpl_nw = 0.0   # width,  scale-normalized
        self._tpl_nh = 0.0   # height, scale-normalized
        self._tpl_angle = 0.0  # rotation in degrees (CW, Qt convention)
        self._tpl_drag_handle = None
        self._tpl_drag_start_screen = None   # (sx, sy) at drag start
        self._tpl_drag_start_state = None    # (cx, cy, nw, nh, angle) at drag start
        self._tpl_drag_anchor_screen = None  # (ax, ay) fixed anchor screen pixels for resize
        self._tpl_drag_start_mouse_angle = None  # degrees, for rotation drag
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)  # Enable keyboard focus
        self._update_size()

        # Temporary right-button panning state
        self._temp_panning = False
        self._pan_last_global_pos = None
        self._pan_scroll_area = None
        # Load pan cursor from icon
        _pan_icon = os.path.join(os.path.dirname(__file__), "icons", "pan.svg")
        _pan_pixmap = QPixmap(_pan_icon)
        if not _pan_pixmap.isNull():
            self._pan_cursor = QCursor(_pan_pixmap)
        else:
            self._pan_cursor = QCursor(Qt.OpenHandCursor)

    def set_template_image(self, pixmap):
        """Set (or clear) the template underlay image. Pass None to remove."""
        self._template_image = pixmap
        if pixmap is not None and not pixmap.isNull():
            bx0, by0 = self.canvas_to_screen(0, 0)
            bx1, by1 = self.canvas_to_screen(self.pattern.CANVAS_WIDTH, self.pattern.CANVAS_HEIGHT)
            canvas_w_px = abs(bx1 - bx0)
            canvas_h_px = abs(by1 - by0)
            img_w = pixmap.width()
            img_h = pixmap.height()
            fit_px = min(canvas_w_px / img_w, canvas_h_px / img_h)
            nw = img_w * fit_px / self._scale
            nh = img_h * fit_px / self._scale
            self._tpl_cx = nw / 2.0
            self._tpl_cy = nh / 2.0
            self._tpl_nw = nw
            self._tpl_nh = nh
            self._tpl_angle = 0.0
        self.update()

    def set_template_resize_mode(self, active):
        """Activate or deactivate the template resize/move/rotate overlay."""
        self._template_resize_mode = active
        self._tpl_drag_handle = None
        self._tpl_drag_start_screen = None
        self._tpl_drag_start_state = None
        self._tpl_drag_anchor_screen = None
        self._tpl_drag_start_mouse_angle = None
        if not active:
            self.setCursor(self._tool.cursor if self._tool else Qt.ArrowCursor)
        self.update()

    def get_template_state(self):
        """Return current template transform as (cx, cy, nw, nh, angle), or None if no image."""
        if self._template_image is None:
            return None
        return (self._tpl_cx, self._tpl_cy, self._tpl_nw, self._tpl_nh, self._tpl_angle)

    def restore_template_state(self, state):
        """Restore a saved template transform state (cx, cy, nw, nh, angle)."""
        if state is not None:
            self._tpl_cx, self._tpl_cy, self._tpl_nw, self._tpl_nh, self._tpl_angle = state
        self.update()

    # ── Template geometry helpers ──

    _TPL_ROT_OFFSET = 20  # px from edge to rotation handle circle center

    def _tpl_center_screen(self):
        """Return template center (cx_px, cy_px) in screen pixels."""
        return (self.MARGIN + self._tpl_cx * self._scale,
                self.MARGIN + self._tpl_cy * self._scale)

    def _tpl_screen_size(self):
        """Return (sw, sh) of the template in screen pixels."""
        return (max(1, int(self._tpl_nw * self._scale)),
                max(1, int(self._tpl_nh * self._scale)))

    def _tpl_local_handles(self, half_w, half_h):
        """Handle positions in local frame (screen pixels, relative to center).

        Args:
            half_w, half_h: half the image size in screen pixels.
        """
        off = self._TPL_ROT_OFFSET
        return {
            'TL':    (-half_w,       -half_h),
            'TC':    (    0.0,       -half_h),
            'TR':    (+half_w,       -half_h),
            'ML':    (-half_w,           0.0),
            'MR':    (+half_w,           0.0),
            'BL':    (-half_w,       +half_h),
            'BC':    (    0.0,       +half_h),
            'BR':    (+half_w,       +half_h),
            'ROT_T': (    0.0, -half_h - off),
            'ROT_B': (    0.0, +half_h + off),
            'ROT_L': (-half_w - off,     0.0),
            'ROT_R': (+half_w + off,     0.0),
        }

    def _tpl_local_to_screen(self, lx, ly):
        """Transform a local-frame point (px, relative to center) to screen coords."""
        a = math.radians(self._tpl_angle)
        cx, cy = self._tpl_center_screen()
        return (cx + lx * math.cos(a) - ly * math.sin(a),
                cy + lx * math.sin(a) + ly * math.cos(a))

    def _tpl_screen_to_local(self, sx, sy):
        """Un-rotate a screen point into local frame (px, relative to center)."""
        a = math.radians(self._tpl_angle)
        cx, cy = self._tpl_center_screen()
        dx, dy = sx - cx, sy - cy
        return (dx * math.cos(a) + dy * math.sin(a),
                -dx * math.sin(a) + dy * math.cos(a))

    def _tpl_hit_handle(self, ex, ey):
        """Return handle name, 'MOVE' if inside rect body, or None."""
        if not self._template_resize_mode or self._template_image is None:
            return None
        sw, sh = self._tpl_screen_size()
        half_w, half_h = sw / 2.0, sh / 2.0
        lx, ly = self._tpl_screen_to_local(ex, ey)
        HIT = 7
        for name, (hlx, hly) in self._tpl_local_handles(half_w, half_h).items():
            if abs(lx - hlx) <= HIT and abs(ly - hly) <= HIT:
                return name
        if -half_w <= lx <= half_w and -half_h <= ly <= half_h:
            return 'MOVE'
        return None

    def _tpl_cursor_for_handle(self, handle):
        if handle is not None and handle.startswith('ROT'):
            return Qt.CrossCursor
        if handle in ('TL', 'BR'):
            return Qt.SizeFDiagCursor
        if handle in ('TR', 'BL'):
            return Qt.SizeBDiagCursor
        if handle in ('TC', 'BC'):
            return Qt.SizeVerCursor
        if handle in ('ML', 'MR'):
            return Qt.SizeHorCursor
        if handle == 'MOVE':
            return Qt.SizeAllCursor
        return Qt.ArrowCursor

    # Resize handle config:
    #   (delta_w_sign, delta_h_sign, constrain_ratio, anchor_local_x_mul, anchor_local_y_mul)
    # anchor_local_*_mul identifies the *opposite* corner/edge (stays fixed during drag).
    # center_new = anchor_screen - R * (anchor_mul_x * new_nw_px/2, anchor_mul_y * new_nh_px/2)
    _TPL_HANDLE_INFO = {
        'TL': (-1, -1, True,  +1, +1),
        'TC': ( 0, -1, False,  0, +1),
        'TR': (+1, -1, True,  -1, +1),
        'ML': (-1,  0, False, +1,  0),
        'MR': (+1,  0, False, -1,  0),
        'BL': (-1, +1, True,  +1, -1),
        'BC': ( 0, +1, False,  0, -1),
        'BR': (+1, +1, True,  -1, -1),
    }

    def _tpl_mouse_press(self, event):
        handle = self._tpl_hit_handle(event.x(), event.y())
        if not handle:
            return
        self._tpl_drag_handle = handle
        self._tpl_drag_start_screen = (event.x(), event.y())
        self._tpl_drag_start_state = (self._tpl_cx, self._tpl_cy,
                                      self._tpl_nw, self._tpl_nh, self._tpl_angle)
        if handle.startswith('ROT'):
            cx_px, cy_px = self._tpl_center_screen()
            self._tpl_drag_start_mouse_angle = math.degrees(
                math.atan2(event.y() - cy_px, event.x() - cx_px))
            self._tpl_drag_anchor_screen = None
        elif handle == 'MOVE':
            self._tpl_drag_anchor_screen = None
            self._tpl_drag_start_mouse_angle = None
        else:
            self._tpl_drag_start_mouse_angle = None
            sw, sh = self._tpl_screen_size()
            half_w, half_h = sw / 2.0, sh / 2.0
            mx, my = self._TPL_HANDLE_INFO[handle][3], self._TPL_HANDLE_INFO[handle][4]
            ax, ay = self._tpl_local_to_screen(mx * half_w, my * half_h)
            self._tpl_drag_anchor_screen = (ax, ay)
        self.setCursor(self._tpl_cursor_for_handle(handle))

    def _tpl_mouse_move(self, event):
        if self._tpl_drag_handle is None:
            handle = self._tpl_hit_handle(event.x(), event.y())
            self.setCursor(self._tpl_cursor_for_handle(handle)
                           if handle else Qt.ArrowCursor)
            return
        h = self._tpl_drag_handle
        cx0, cy0, nw0, nh0, a0 = self._tpl_drag_start_state

        if h.startswith('ROT'):
            cx_px, cy_px = self._tpl_center_screen()
            current_angle = math.degrees(
                math.atan2(event.y() - cy_px, event.x() - cx_px))
            self._tpl_angle = a0 + (current_angle - self._tpl_drag_start_mouse_angle)
            self.update()
            return

        if h == 'MOVE':
            sx0, sy0 = self._tpl_drag_start_screen
            self._tpl_cx = cx0 + (event.x() - sx0) / self._scale
            self._tpl_cy = cy0 + (event.y() - sy0) / self._scale
            self.update()
            return

        # ── Resize ──
        sx0, sy0 = self._tpl_drag_start_screen
        a_rad = math.radians(a0)
        ca, sa = math.cos(a_rad), math.sin(a_rad)
        dx_s = event.x() - sx0
        dy_s = event.y() - sy0
        # Un-rotate screen drag delta into local frame, convert to normalized units
        local_dx = (dx_s * ca + dy_s * sa) / self._scale
        local_dy = (-dx_s * sa + dy_s * ca) / self._scale

        dw_sign, dh_sign, constrain, mx, my = self._TPL_HANDLE_INFO[h]
        MIN = 0.5
        ratio = nw0 / nh0 if nh0 > 0 else 1.0
        delta_nw = dw_sign * local_dx
        delta_nh = dh_sign * local_dy

        if constrain:
            if abs(delta_nw) >= abs(delta_nh) * ratio:
                new_nw = max(nw0 + delta_nw, MIN)
                new_nh = new_nw / ratio
            else:
                new_nh = max(nh0 + delta_nh, MIN)
                new_nw = new_nh * ratio
        else:
            new_nw = max(nw0 + delta_nw, MIN) if dw_sign != 0 else nw0
            new_nh = max(nh0 + delta_nh, MIN) if dh_sign != 0 else nh0

        self._tpl_nw = new_nw
        self._tpl_nh = new_nh

        # Recompute center so the anchor point stays fixed in screen space.
        # center = anchor_screen - R * anchor_local_new
        alx_px = mx * new_nw * self._scale / 2.0
        aly_px = my * new_nh * self._scale / 2.0
        ax, ay = self._tpl_drag_anchor_screen
        self._tpl_cx = (ax - (alx_px * ca - aly_px * sa) - self.MARGIN) / self._scale
        self._tpl_cy = (ay - (alx_px * sa + aly_px * ca) - self.MARGIN) / self._scale
        self.update()

    def _tpl_mouse_release(self, event):
        self._tpl_drag_handle = None
        self._tpl_drag_start_screen = None
        self._tpl_drag_start_state = None
        self._tpl_drag_anchor_screen = None
        self._tpl_drag_start_mouse_angle = None
        handle = self._tpl_hit_handle(event.x(), event.y())
        self.setCursor(self._tpl_cursor_for_handle(handle)
                       if handle else Qt.ArrowCursor)

    def set_tool(self, tool):
        # Deselect the old tool if it has a deselect method
        if self._tool and hasattr(self._tool, 'on_deselect'):
            self._tool.on_deselect(self)
        self._tool = tool
        self.setCursor(tool.cursor if tool else Qt.ArrowCursor)

    def get_scale(self):
        return self._scale

    def set_scale(self, scale):
        self._scale = max(1.0, scale)
        self._update_size()
        self.update()

    def zoom_at(self, new_scale, widget_pos):
        """Zoom to new_scale keeping the canvas point under widget_pos stationary."""
        new_scale = max(1.0, new_scale)
        if new_scale == self._scale:
            return
        scroll_area = self._get_pan_scroll_area()
        if scroll_area is None:
            self.set_scale(new_scale)
            return
        sx, sy = widget_pos.x(), widget_pos.y()
        h = scroll_area.horizontalScrollBar().value()
        v = scroll_area.verticalScrollBar().value()
        # Cursor position in viewport coordinates
        vx = sx - h
        vy = sy - v
        # Canvas-space point under the cursor (before scale change)
        cx, cy = self.screen_to_canvas(sx, sy)
        # Apply new scale
        self._scale = new_scale
        self._update_size()
        self.update()
        # New pixel position of the same canvas point
        new_sx, new_sy = self.canvas_to_screen(cx, cy)
        # Adjust scroll bars so the canvas point stays under the cursor
        scroll_area.horizontalScrollBar().setValue(int(new_sx - vx))
        scroll_area.verticalScrollBar().setValue(int(new_sy - vy))

    def set_view_orientation(self, orientation):
        """Set view orientation: 'default' or 'sewing_direction' (90° CW)."""
        self._view_orientation = orientation
        self._update_size()
        self.update()

    def apply_display_settings(self, line_color, line_width, point_color,
                               point_size, grid_color, show_stitch_points,
                               show_grid):
        """Apply display preferences and redraw.

        Args:
            line_color: Hex string, e.g. '#000000'.
            line_width: 'fine', 'medium', 'thick', or 'very thick'.
            point_color: Hex string.
            point_size: 'small', 'medium', or 'large'.
            grid_color: Hex string.
            show_stitch_points: Whether stitch points are shown by default.
            show_grid: Whether grid is shown by default.
        """
        self._color_line = QColor(line_color)
        self._color_point = QColor(point_color)
        self._color_grid = QColor(grid_color)
        self._line_width = self.LINE_WIDTHS.get(line_width, 2)
        self._point_radius = self.POINT_RADII.get(point_size, 3)
        self._show_stitch_points = bool(show_stitch_points)
        self._show_grid = bool(show_grid)
        self.update()

    def get_selected_point(self):
        """Get the first selected point index, or None if no selection."""
        return self._selection_start

    def set_selected_point(self, index):
        """Set selection to a single point. Pass None to deselect."""
        if index is None:
            self._selection_start = None
            self._selection_end = None
        else:
            self._selection_start = index
            self._selection_end = index
        self.update()  # Redraw canvas to reflect selection change
        self.selection_changed.emit()

    def get_selection(self):
        """Get selection range as (start, end) or (None, None) if no selection."""
        return (self._selection_start, self._selection_end)

    def set_selection(self, start, end):
        """Set selection range. Swap if start > end. Pass (None, None) to deselect."""
        if start is None or end is None:
            self._selection_start = None
            self._selection_end = None
        else:
            # Ensure start <= end
            self._selection_start = min(start, end)
            self._selection_end = max(start, end)
        self.update()
        self.selection_changed.emit()

    def is_point_selected(self, idx):
        """Check if a point index is within the selection range."""
        if self._selection_start is None or self._selection_end is None:
            return False
        return self._selection_start <= idx <= self._selection_end

    def get_selected_indices(self):
        """Get list of all selected point indices."""
        if self._selection_start is None or self._selection_end is None:
            return []
        return list(range(self._selection_start, self._selection_end + 1))

    def _update_size(self):
        if self._view_orientation == "default":
            w = int(self.pattern.CANVAS_WIDTH * self._scale + 2 * self.MARGIN)
            h = int(self.pattern.CANVAS_HEIGHT * self._scale + 2 * self.MARGIN)
        else:  # sewing_direction - swap dimensions
            w = int(self.pattern.CANVAS_HEIGHT * self._scale + 2 * self.MARGIN)
            h = int(self.pattern.CANVAS_WIDTH * self._scale + 2 * self.MARGIN)
        self.setMinimumSize(QSize(w, h))
        self.setFixedSize(QSize(w, h))

    # ── Coordinate transforms ──

    def canvas_to_screen(self, cx, cy):
        """Transform canvas coordinates to screen coordinates."""
        if self._view_orientation == "default":
            sx = self.MARGIN + cx * self._scale
            sy = self.MARGIN + (self.pattern.CANVAS_HEIGHT - cy) * self._scale
        else:  # sewing_direction (90° CW rotation)
            # After 90° CW: (0,0) moves to top-left
            sx = self.MARGIN + cy * self._scale
            sy = self.MARGIN + cx * self._scale
        return sx, sy

    def screen_to_canvas(self, sx, sy):
        """Transform screen coordinates to canvas coordinates."""
        if self._view_orientation == "default":
            cx = (sx - self.MARGIN) / self._scale
            cy = self.pattern.CANVAS_HEIGHT - (sy - self.MARGIN) / self._scale
        else:  # sewing_direction (90° CW rotation)
            # Reverse 90° CW: cx = (sy - MARGIN) / scale, cy = (sx - MARGIN) / scale
            cx = (sy - self.MARGIN) / self._scale
            cy = (sx - self.MARGIN) / self._scale
        return cx, cy

    # ── Painting ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background
        painter.fillRect(self.rect(), self.COLOR_BG)

        # Template underlay image (drawn before grid so it appears beneath everything)
        if self._template_image is not None and not self._template_image.isNull():
            cx_px, cy_px = self._tpl_center_screen()
            sw, sh = self._tpl_screen_size()
            painter.save()
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.translate(cx_px, cy_px)
            painter.rotate(self._tpl_angle)
            painter.setOpacity(0.5)
            painter.drawPixmap(QRect(-sw // 2, -sh // 2, sw, sh), self._template_image)
            painter.setOpacity(1.0)
            painter.restore()

        # Grid lines (every integer unit)
        if self._show_grid:
            grid_pen = QPen(self._color_grid, 1)
            painter.setPen(grid_pen)
            for x in range(self.pattern.CANVAS_WIDTH + 1):
                sx1, sy1 = self.canvas_to_screen(x, self.pattern.CANVAS_HEIGHT)
                sx2, sy2 = self.canvas_to_screen(x, 0)
                painter.drawLine(int(sx1), int(sy1), int(sx2), int(sy2))
            for y in range(self.pattern.CANVAS_HEIGHT + 1):
                sx1, sy1 = self.canvas_to_screen(0, y)
                sx2, sy2 = self.canvas_to_screen(self.pattern.CANVAS_WIDTH, y)
                painter.drawLine(int(sx1), int(sy1), int(sx2), int(sy2))

        # Border rectangle
        border_pen = QPen(self.COLOR_BORDER, 2)
        painter.setPen(border_pen)
        bx0, by0 = self.canvas_to_screen(0, 0)
        bx1, by1 = self.canvas_to_screen(self.pattern.CANVAS_WIDTH, self.pattern.CANVAS_HEIGHT)
        painter.drawRect(int(bx1), int(by1),
                         int(bx0 - bx1), int(by0 - by1))

        # Connecting lines — iterate the display layer (includes ELEM_AUTO)
        display_elems = self.pattern.display_elements
        display_map = self.pattern.display_base_map
        if len(display_elems) >= 2:
            default_line_pen = QPen(self._color_line, self._line_width)
            use_palette = self.pattern.has_palette
            if not use_palette:
                painter.setPen(default_line_pen)

            current_color_idx = 0  # active palette index as we walk through elements
            last_coord = None      # (display_idx, sx, sy, kind) of the previous coord element
            last_eff_base_idx = None  # base index of the last non-auto coord element seen

            for elem_idx, elem in enumerate(display_elems):
                kind = elem[0]

                if kind == ELEM_COLOR:
                    current_color_idx = elem[1]
                    continue

                if kind not in (ELEM_STITCH, ELEM_AUTO, ELEM_TRIM):
                    continue

                x, y = elem[1], elem[2]
                sx, sy = self.canvas_to_screen(x, y)
                curr_base_idx = display_map[elem_idx]

                if last_coord is not None:
                    last_display_idx, last_sx, last_sy, last_kind = last_coord
                    # Suppress line only between two consecutive ELEM_TRIM elements
                    if not (kind == ELEM_TRIM and last_kind == ELEM_TRIM):
                        # Determine which base index to use for selection highlight.
                        # For ELEM_AUTO starting points, fall back to the last known base index.
                        start_base = display_map[last_display_idx]
                        if start_base is None:
                            start_base = last_eff_base_idx
                        is_in_selection = (
                            self._selection_start is not None
                            and self._selection_end is not None
                            and self._selection_end - self._selection_start >= 1
                            and start_base is not None
                            and start_base >= self._selection_start
                            and start_base < self._selection_end
                        )
                        if is_in_selection:
                            dashed_pen = QPen(QColor(0, 80, 200), 2)
                            dashed_pen.setDashPattern([4, 4])
                            painter.setPen(dashed_pen)
                        elif use_palette:
                            ci = min(current_color_idx, len(self.pattern.colors) - 1)
                            pr, pg, pb = self.pattern.colors[ci]
                            painter.setPen(QPen(QColor(pr, pg, pb), self._line_width))
                        else:
                            painter.setPen(default_line_pen)
                        painter.drawLine(int(last_sx), int(last_sy), int(sx), int(sy))

                if curr_base_idx is not None:
                    last_eff_base_idx = curr_base_idx
                last_coord = (elem_idx, sx, sy, kind)

        # Collect coord elements with running color for point rendering.
        # coord_elems: list of (base_idx_or_neg1, x, y, color_idx, kind)
        # base_idx is -1 for ELEM_AUTO (they cannot be selected).
        coord_elems = []
        if self._show_stitch_points or self._show_auto_stitch_points:
            cur_col = 0
            last_auto_sel_idx = -1  # effective base idx to use for auto-stitch selection colouring
            for disp_idx, elem in enumerate(self.pattern.display_elements):
                if elem[0] == ELEM_COLOR:
                    cur_col = elem[1]
                elif elem_has_coords(elem):
                    kind = elem[0]
                    if kind == ELEM_AUTO and not self._show_auto_stitch_points:
                        continue
                    if kind != ELEM_AUTO and not self._show_stitch_points:
                        continue
                    base_idx = display_map[disp_idx]
                    if base_idx is not None:
                        sel_idx = base_idx
                        last_auto_sel_idx = base_idx
                    else:
                        # Auto-stitch: inherit the preceding base index so that
                        # is_point_selected() returns True when the segment is selected.
                        # However, if the preceding base index is at or beyond selection_end,
                        # the auto-stitch lies after the last selected point — don't highlight it.
                        if (self._selection_end is not None
                                and last_auto_sel_idx >= self._selection_end):
                            sel_idx = -1
                        else:
                            sel_idx = last_auto_sel_idx
                    coord_elems.append((sel_idx, elem[1], elem[2], cur_col, kind))

        # Stitch points (draw in layers to ensure selected points are on top)
        if coord_elems:
            r = self._point_radius
            use_palette = self.pattern.has_palette
            num_coord = len(coord_elems)

            def _draw_point(sx, sy, color, kind, outline=False):
                """Draw a single stitch or auto-stitch marker."""
                if kind == ELEM_AUTO:
                    # Auto stitch: cross
                    cross_line = 2 if self._point_radius >= 3 else 1
                    cross_r = r-1
                    painter.setPen(QPen(color, cross_line))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawLine(int(sx - cross_r), int(sy - cross_r), int(sx + cross_r), int(sy + cross_r))
                    painter.drawLine(int(sx - cross_r), int(sy + cross_r), int(sx + cross_r), int(sy - cross_r))
                    painter.setPen(Qt.NoPen)

                    # # Auto stitch: hollow circle
                    # painter.setPen(QPen(color, 1))
                    # painter.setBrush(self.COLOR_BG)
                    # painter.drawEllipse(int(sx - r), int(sy - r), 2 * r, 2 * r)
                    # painter.setPen(Qt.NoPen)
                else:
                    # Normal stitch: filled circle
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QBrush(color))
                    painter.drawEllipse(int(sx - r), int(sy - r), 2 * r, 2 * r)

            if use_palette:
                # Palette mode: draw non-selected in palette color, selected in blue
                painter.setPen(Qt.NoPen)
                for cidx, x, y, color_idx, kind in coord_elems:
                    if self.is_point_selected(cidx):
                        continue
                    ci = min(color_idx, len(self.pattern.colors) - 1)
                    pr, pg, pb = self.pattern.colors[ci]
                    sx, sy = self.canvas_to_screen(x, y)
                    _draw_point(sx, sy, QColor(pr, pg, pb), kind)
                for cidx, x, y, color_idx, kind in coord_elems:
                    if self.is_point_selected(cidx):
                        sx, sy = self.canvas_to_screen(x, y)
                        _draw_point(sx, sy, QColor(0, 80, 200), kind)
            else:
                # Default mode: first=green, last=red, selected=blue, others=default
                painter.setPen(Qt.NoPen)
                # Layer 1: regular (not first, not last, not selected)
                for j, (cidx, x, y, color_idx, kind) in enumerate(coord_elems):
                    if num_coord > 1 and (j == 0 or j == num_coord - 1):
                        continue
                    if self.is_point_selected(cidx):
                        continue
                    sx, sy = self.canvas_to_screen(x, y)
                    _draw_point(sx, sy, self._color_point, kind)

                # Layer 2: first, last, selected
                if num_coord > 1:
                    cidx0, x0, y0, _, kind0 = coord_elems[0]
                    c0 = QColor(0, 80, 200) if self.is_point_selected(cidx0) else self.COLOR_FIRST_POINT
                    sx, sy = self.canvas_to_screen(x0, y0)
                    _draw_point(sx, sy, c0, kind0)

                    cidx_n, xn, yn, _, kindn = coord_elems[-1]
                    cn = QColor(0, 80, 200) if self.is_point_selected(cidx_n) else self.COLOR_LAST_POINT
                    sx, sy = self.canvas_to_screen(xn, yn)
                    _draw_point(sx, sy, cn, kindn)
                elif num_coord == 1:
                    cidx0, x0, y0, _, kind0 = coord_elems[0]
                    c0 = QColor(0, 80, 200) if self.is_point_selected(cidx0) else self.COLOR_FIRST_POINT
                    sx, sy = self.canvas_to_screen(x0, y0)
                    _draw_point(sx, sy, c0, kind0)

                # Selected mid-points in blue
                for j, (cidx, x, y, color_idx, kind) in enumerate(coord_elems):
                    if j == 0 or j == num_coord - 1:
                        continue
                    if self.is_point_selected(cidx):
                        sx, sy = self.canvas_to_screen(x, y)
                        _draw_point(sx, sy, QColor(0, 80, 200), kind)

        # Template resize/rotate frame and handles
        if (self._template_resize_mode
                and self._template_image is not None
                and not self._template_image.isNull()):
            cx_px, cy_px = self._tpl_center_screen()
            sw, sh = self._tpl_screen_size()
            half_w, half_h = sw / 2.0, sh / 2.0
            HANDLE_SZ = 8
            H2 = HANDLE_SZ // 2
            ROT_R = 5
            painter.save()
            painter.translate(cx_px, cy_px)
            painter.rotate(self._tpl_angle)
            # Dashed orange frame
            painter.setPen(QPen(QColor(255, 140, 0), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(int(-half_w), int(-half_h), sw, sh)
            # Resize handles (white squares at corners and edges)
            painter.setPen(QPen(QColor(200, 100, 0), 1))
            painter.setBrush(QBrush(QColor(255, 255, 255)))
            for name, (lx, ly) in self._tpl_local_handles(half_w, half_h).items():
                if name.startswith('ROT'):
                    continue
                painter.drawRect(int(lx) - H2, int(ly) - H2, HANDLE_SZ, HANDLE_SZ)
            # Rotation handles: circles on all 4 sides with short stems
            off = self._TPL_ROT_OFFSET
            rot_defs = [
                (0, int(-half_h),      0, int(-half_h - off)),   # top
                (0, int(+half_h),      0, int(+half_h + off)),   # bottom
                (int(-half_w), 0, int(-half_w - off), 0),        # left
                (int(+half_w), 0, int(+half_w + off), 0),        # right
            ]
            painter.setPen(QPen(QColor(0, 160, 255), 1))
            painter.setBrush(QBrush(QColor(255, 255, 255)))
            for x1, y1, cx2, cy2 in rot_defs:
                dx, dy = cx2 - x1, cy2 - y1
                length = math.sqrt(dx * dx + dy * dy)
                if length > 0:
                    ux, uy = dx / length, dy / length
                    painter.drawLine(x1, y1,
                                     int(cx2 - ux * ROT_R), int(cy2 - uy * ROT_R))
                painter.drawEllipse(cx2 - ROT_R, cy2 - ROT_R, ROT_R * 2, ROT_R * 2)
            painter.restore()

        # Tool overlay
        if self._tool:
            self._tool.paint_overlay(self, painter)

        painter.end()

    def _get_pan_scroll_area(self):
        """Return the scroll area containing this canvas, caching the result."""
        if self._pan_scroll_area is None:
            viewport = self.parent()
            if viewport:
                scroll_area = viewport.parent()
                if scroll_area and hasattr(scroll_area, 'horizontalScrollBar'):
                    self._pan_scroll_area = scroll_area
        return self._pan_scroll_area

    # ── Mouse events ──

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._temp_panning = True
            self._pan_last_global_pos = event.globalPos()
            self.setCursor(self._pan_cursor)
            event.accept()
            return
        if (self._template_resize_mode and self._template_image is not None
                and event.button() == Qt.LeftButton):
            self._tpl_mouse_press(event)
            return
        if self._tool:
            self._tool.mouse_press(self, event)

    def mouseMoveEvent(self, event):
        cx, cy = self.screen_to_canvas(event.x(), event.y())
        self.cursor_moved.emit(cx, cy)
        if self._temp_panning and self._pan_last_global_pos is not None:
            current_pos = event.globalPos()
            delta = self._pan_last_global_pos - current_pos
            scroll_area = self._get_pan_scroll_area()
            if scroll_area:
                scroll_area.horizontalScrollBar().setValue(
                    scroll_area.horizontalScrollBar().value() + delta.x())
                scroll_area.verticalScrollBar().setValue(
                    scroll_area.verticalScrollBar().value() + delta.y())
            self._pan_last_global_pos = current_pos
            return
        if self._template_resize_mode and self._template_image is not None:
            self._tpl_mouse_move(event)
            return
        if self._tool:
            self._tool.mouse_move(self, event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton and self._temp_panning:
            self._temp_panning = False
            self._pan_last_global_pos = None
            # Restore the cursor for the active tool
            if self._tool:
                self.setCursor(self._tool.cursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        if (self._template_resize_mode and self._template_image is not None
                and event.button() == Qt.LeftButton):
            self._tpl_mouse_release(event)
            return
        if self._tool:
            self._tool.mouse_release(self, event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_at(self._scale * 1.25, event.pos())
            elif delta < 0:
                self.zoom_at(self._scale / 1.25, event.pos())
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        """Handle keyboard events for moving selected points or tool-specific actions."""
        # First, check if the current tool handles this key
        if self._tool and hasattr(self._tool, 'key_press'):
            if self._tool.key_press(self, event):
                return
        
        # Then handle arrow keys for moving selected points
        if self._selection_start is not None and not event.isAutoRepeat():
            moved = False
            dx, dy = 0, 0
            
            if event.key() == Qt.Key_Up:
                dy = 1
                moved = True
            elif event.key() == Qt.Key_Down:
                dy = -1
                moved = True
            elif event.key() == Qt.Key_Left:
                dx = -1
                moved = True
            elif event.key() == Qt.Key_Right:
                dx = 1
                moved = True
            
            if moved:
                # Move all selected elements that have coordinates
                selected_indices = self.get_selected_indices()
                for idx in selected_indices:
                    coords = self.pattern.get_coords(idx)
                    if coords is None:
                        continue
                    x, y = coords
                    x += dx
                    y += dy
                    # Clamp to canvas bounds
                    x = max(0, min(self.pattern.CANVAS_WIDTH, x))
                    y = max(0, min(self.pattern.CANVAS_HEIGHT, y))
                    self.pattern.move_point(idx, x, y)
                self.update()
                self.notify_change()
                event.accept()
                return
        
        super().keyPressEvent(event)

    # ── Notifications ──

    def notify_change(self):
        """Called by tools after modifying the pattern."""
        self.changed.emit()
