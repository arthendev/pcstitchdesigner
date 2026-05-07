"""Custom canvas widget for drawing stitch patterns."""

import os

from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QSize, pyqtSignal
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

        # Connecting lines
        elements = self.pattern.elements
        if len(elements) >= 2:
            default_line_pen = QPen(self._color_line, self._line_width)
            use_palette = self.pattern.has_palette
            if not use_palette:
                painter.setPen(default_line_pen)

            current_color_idx = 0  # active palette index as we walk through elements
            last_coord = None      # (elem_idx, sx, sy, kind) of the previous coord element

            for elem_idx, elem in enumerate(elements):
                kind = elem[0]

                if kind == ELEM_COLOR:
                    current_color_idx = elem[1]
                    continue

                if kind not in (ELEM_STITCH, ELEM_AUTO, ELEM_TRIM):
                    continue

                x, y = elem[1], elem[2]
                sx, sy = self.canvas_to_screen(x, y)

                if last_coord is not None:
                    last_idx, last_sx, last_sy, last_kind = last_coord
                    # Suppress line only between two consecutive ELEM_TRIM elements
                    if not (kind == ELEM_TRIM and last_kind == ELEM_TRIM):
                        is_in_selection = (
                            self._selection_start is not None
                            and self._selection_end is not None
                            and self._selection_end - self._selection_start >= 1
                            and last_idx >= self._selection_start
                            and last_idx < self._selection_end
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

                last_coord = (elem_idx, sx, sy, kind)

        # Collect coord elements with running color for point rendering.
        # coord_elems: list of (elem_idx, x, y, color_idx, kind)
        coord_elems = []
        if self._show_stitch_points or self._show_auto_stitch_points:
            cur_col = 0
            for idx, elem in enumerate(self.pattern.elements):
                if elem[0] == ELEM_COLOR:
                    cur_col = elem[1]
                elif elem_has_coords(elem):
                    kind = elem[0]
                    if kind == ELEM_AUTO and not self._show_auto_stitch_points:
                        continue
                    if kind != ELEM_AUTO and not self._show_stitch_points:
                        continue
                    coord_elems.append((idx, elem[1], elem[2], cur_col, kind))

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
