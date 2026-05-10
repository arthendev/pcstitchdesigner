"""Custom canvas widget for drawing stitch patterns."""

import os

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

    MARGIN = 20  # pixel margin around the drawing area

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
        self._tpl_nx = 0.0   # (screen_left - MARGIN) / _scale  (zoom-invariant)
        self._tpl_ny = 0.0   # (screen_top  - MARGIN) / _scale
        self._tpl_nw = 0.0   # screen_width  / _scale
        self._tpl_nh = 0.0   # screen_height / _scale
        self._tpl_drag_handle = None      # active handle name or None
        self._tpl_drag_start_screen = None  # (sx, sy) at drag start
        self._tpl_drag_start_rect = None    # (nx, ny, nw, nh) at drag start
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
            self._tpl_nx = 0.0
            self._tpl_ny = 0.0
            self._tpl_nw = img_w * fit_px / self._scale
            self._tpl_nh = img_h * fit_px / self._scale
        self.update()

    def set_template_resize_mode(self, active):
        """Activate or deactivate the template resize/move overlay."""
        self._template_resize_mode = active
        self._tpl_drag_handle = None
        self._tpl_drag_start_screen = None
        self._tpl_drag_start_rect = None
        if not active:
            self.setCursor(self._tool.cursor if self._tool else Qt.ArrowCursor)
        self.update()

    def _tpl_screen_rect(self):
        """Return (sx, sy, sw, sh) of the template in screen pixels."""
        sx = int(self.MARGIN + self._tpl_nx * self._scale)
        sy = int(self.MARGIN + self._tpl_ny * self._scale)
        sw = max(1, int(self._tpl_nw * self._scale))
        sh = max(1, int(self._tpl_nh * self._scale))
        return sx, sy, sw, sh

    def _tpl_handle_positions(self, sx, sy, sw, sh):
        """Return dict of handle name → (hx, hy) in screen pixels."""
        return {
            'TL': (sx,           sy),
            'TC': (sx + sw / 2,  sy),
            'TR': (sx + sw,      sy),
            'ML': (sx,           sy + sh / 2),
            'MR': (sx + sw,      sy + sh / 2),
            'BL': (sx,           sy + sh),
            'BC': (sx + sw / 2,  sy + sh),
            'BR': (sx + sw,      sy + sh),
        }

    def _tpl_hit_handle(self, ex, ey):
        """Return handle name if near a handle, 'MOVE' if inside rect, else None."""
        if not self._template_resize_mode or self._template_image is None:
            return None
        sx, sy, sw, sh = self._tpl_screen_rect()
        HIT = 6
        for name, (hx, hy) in self._tpl_handle_positions(sx, sy, sw, sh).items():
            if abs(ex - hx) <= HIT and abs(ey - hy) <= HIT:
                return name
        if sx <= ex <= sx + sw and sy <= ey <= sy + sh:
            return 'MOVE'
        return None

    def _tpl_cursor_for_handle(self, handle):
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

    def _tpl_mouse_press(self, event):
        handle = self._tpl_hit_handle(event.x(), event.y())
        if handle:
            self._tpl_drag_handle = handle
            self._tpl_drag_start_screen = (event.x(), event.y())
            self._tpl_drag_start_rect = (self._tpl_nx, self._tpl_ny,
                                         self._tpl_nw, self._tpl_nh)
            self.setCursor(self._tpl_cursor_for_handle(handle))

    def _tpl_mouse_move(self, event):
        if self._tpl_drag_handle is None:
            handle = self._tpl_hit_handle(event.x(), event.y())
            self.setCursor(self._tpl_cursor_for_handle(handle)
                           if handle else Qt.ArrowCursor)
            return
        sx0, sy0 = self._tpl_drag_start_screen
        dnx = (event.x() - sx0) / self._scale
        dny = (event.y() - sy0) / self._scale
        nx, ny, nw, nh = self._tpl_drag_start_rect
        MIN = 1.0
        ratio = nw / nh if nh != 0 else 1.0  # original aspect ratio
        h = self._tpl_drag_handle
        if h == 'TL':
            # Use the larger absolute delta to drive both dimensions proportionally
            if abs(dnx) >= abs(dny) * ratio:
                dx_c = min(dnx, nw - MIN)
                dy_c = dx_c / ratio
            else:
                dy_c = min(dny, nh - MIN)
                dx_c = dy_c * ratio
            self._tpl_nx = nx + dx_c
            self._tpl_ny = ny + dy_c
            self._tpl_nw = max(nw - dx_c, MIN)
            self._tpl_nh = max(nh - dy_c, MIN)
        elif h == 'TC':
            dy_c = min(dny, nh - MIN)
            self._tpl_nx, self._tpl_nw = nx, nw
            self._tpl_ny = ny + dy_c
            self._tpl_nh = nh - dy_c
        elif h == 'TR':
            # Anchor: left & top edges fixed; width grows right, height shrinks from top
            new_w = max(nw + dnx, MIN)
            new_h = new_w / ratio
            dy_c = nh - new_h
            self._tpl_nx = nx
            self._tpl_ny = ny + dy_c
            self._tpl_nw = new_w
            self._tpl_nh = max(new_h, MIN)
        elif h == 'ML':
            dx_c = min(dnx, nw - MIN)
            self._tpl_nx = nx + dx_c
            self._tpl_ny, self._tpl_nh = ny, nh
            self._tpl_nw = nw - dx_c
        elif h == 'MR':
            self._tpl_nx, self._tpl_ny, self._tpl_nh = nx, ny, nh
            self._tpl_nw = max(nw + dnx, MIN)
        elif h == 'BL':
            # Anchor: right & top edges fixed; height grows down, width shrinks from left
            new_h = max(nh + dny, MIN)
            new_w = new_h * ratio
            dx_c = nw - new_w
            self._tpl_nx = nx + dx_c
            self._tpl_ny = ny
            self._tpl_nw = max(new_w, MIN)
            self._tpl_nh = new_h
        elif h == 'BC':
            self._tpl_nx, self._tpl_ny, self._tpl_nw = nx, ny, nw
            self._tpl_nh = max(nh + dny, MIN)
        elif h == 'BR':
            # Anchor: left & top fixed; pick dominant delta
            if abs(dnx) >= abs(dny) * ratio:
                new_w = max(nw + dnx, MIN)
                new_h = new_w / ratio
            else:
                new_h = max(nh + dny, MIN)
                new_w = new_h * ratio
            self._tpl_nx, self._tpl_ny = nx, ny
            self._tpl_nw = new_w
            self._tpl_nh = new_h
        elif h == 'MOVE':
            self._tpl_nx = nx + dnx
            self._tpl_ny = ny + dny
        self.update()

    def _tpl_mouse_release(self, event):
        self._tpl_drag_handle = None
        self._tpl_drag_start_screen = None
        self._tpl_drag_start_rect = None
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
            sx, sy, sw, sh = self._tpl_screen_rect()
            target = QRect(sx, sy, sw, sh)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.setOpacity(0.5)
            painter.drawPixmap(target, self._template_image)
            painter.setOpacity(1.0)

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

        # Template resize frame and handles (drawn on top of stitches, below tool overlay)
        if (self._template_resize_mode
                and self._template_image is not None
                and not self._template_image.isNull()):
            sx, sy, sw, sh = self._tpl_screen_rect()
            HANDLE = 8
            painter.setPen(QPen(QColor(255, 140, 0), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(sx, sy, sw, sh)
            painter.setPen(QPen(QColor(200, 100, 0), 1))
            painter.setBrush(QBrush(QColor(255, 255, 255)))
            for hx, hy in self._tpl_handle_positions(sx, sy, sw, sh).values():
                painter.drawRect(int(hx) - HANDLE // 2, int(hy) - HANDLE // 2, HANDLE, HANDLE)

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
