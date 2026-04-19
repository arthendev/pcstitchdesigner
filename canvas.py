"""Custom canvas widget for drawing stitch patterns."""

from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QSize, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QBrush, QColor

from model import StitchPattern


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
    POINT_RADIUS = 4  # pixels

    # Colors
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
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)  # Enable keyboard focus
        self._update_size()

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
        w = int(self.pattern.CANVAS_WIDTH * self._scale + 2 * self.MARGIN)
        h = int(self.pattern.CANVAS_HEIGHT * self._scale + 2 * self.MARGIN)
        self.setMinimumSize(QSize(w, h))
        self.setFixedSize(QSize(w, h))

    # ── Coordinate transforms ──

    def canvas_to_screen(self, cx, cy):
        sx = self.MARGIN + cx * self._scale
        sy = self.MARGIN + (self.pattern.CANVAS_HEIGHT - cy) * self._scale
        return sx, sy

    def screen_to_canvas(self, sx, sy):
        cx = (sx - self.MARGIN) / self._scale
        cy = self.pattern.CANVAS_HEIGHT - (sy - self.MARGIN) / self._scale
        return cx, cy

    # ── Painting ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background
        painter.fillRect(self.rect(), self.COLOR_BG)

        # Grid lines (every integer unit)
        grid_pen = QPen(self.COLOR_GRID, 1)
        painter.setPen(grid_pen)
        for x in range(self.pattern.CANVAS_WIDTH + 1):
            sx, sy_top = self.canvas_to_screen(x, self.pattern.CANVAS_HEIGHT)
            _, sy_bot = self.canvas_to_screen(x, 0)
            painter.drawLine(int(sx), int(sy_top), int(sx), int(sy_bot))
        for y in range(self.pattern.CANVAS_HEIGHT + 1):
            sx_left, sy = self.canvas_to_screen(0, y)
            sx_right, _ = self.canvas_to_screen(self.pattern.CANVAS_WIDTH, y)
            painter.drawLine(int(sx_left), int(sy), int(sx_right), int(sy))

        # Border rectangle
        border_pen = QPen(self.COLOR_BORDER, 2)
        painter.setPen(border_pen)
        bx0, by0 = self.canvas_to_screen(0, 0)
        bx1, by1 = self.canvas_to_screen(self.pattern.CANVAS_WIDTH, self.pattern.CANVAS_HEIGHT)
        painter.drawRect(int(bx1), int(by1),
                         int(bx0 - bx1), int(by0 - by1))

        # Connecting lines
        if len(self.pattern.points) >= 2:
            line_pen = QPen(self.COLOR_LINE, 2)
            painter.setPen(line_pen)
            for i in range(len(self.pattern.points) - 1):
                x1, y1 = self.pattern.points[i]
                x2, y2 = self.pattern.points[i + 1]
                sx1, sy1 = self.canvas_to_screen(x1, y1)
                sx2, sy2 = self.canvas_to_screen(x2, y2)
                
                # Draw blue dashed line if both points in selection and multiple selected
                if (self._selection_start is not None and self._selection_end is not None and
                    self._selection_end - self._selection_start >= 1 and
                    i >= self._selection_start and i < self._selection_end):
                    dashed_pen = QPen(QColor(0, 80, 200), 2)
                    dashed_pen.setDashPattern([4, 4])
                    painter.setPen(dashed_pen)
                else:
                    painter.setPen(line_pen)
                painter.drawLine(int(sx1), int(sy1), int(sx2), int(sy2))

        # Stitch points (draw in layers to ensure first, last, and selected are on top)
        painter.setPen(Qt.NoPen)
        r = self.POINT_RADIUS
        num_points = len(self.pattern.points)
        
        # First layer: draw all regular points (not first, not last, not selected)
        for i, (x, y) in enumerate(self.pattern.points):
            # Skip first, last, and selected points (draw them in the top layer)
            if num_points > 1 and (i == 0 or i == num_points - 1):
                continue
            if self.is_point_selected(i):
                continue
            
            # Draw regular point
            painter.setBrush(QBrush(self.COLOR_POINT))
            sx, sy = self.canvas_to_screen(x, y)
            painter.drawEllipse(int(sx - r), int(sy - r), 2 * r, 2 * r)
        
        # Second layer: draw first, last, and selected points on top
        if num_points > 1:
            # Draw first point (green unless selected)
            x, y = self.pattern.points[0]
            if self.is_point_selected(0):
                painter.setBrush(QBrush(QColor(0, 80, 200)))  # Blue if selected
            else:
                painter.setBrush(QBrush(self.COLOR_FIRST_POINT))
            sx, sy = self.canvas_to_screen(x, y)
            painter.drawEllipse(int(sx - r), int(sy - r), 2 * r, 2 * r)
            
            # Draw last point (red unless selected)
            x, y = self.pattern.points[num_points - 1]
            if self.is_point_selected(num_points - 1):
                painter.setBrush(QBrush(QColor(0, 80, 200)))  # Blue if selected
            else:
                painter.setBrush(QBrush(self.COLOR_LAST_POINT))
            sx, sy = self.canvas_to_screen(x, y)
            painter.drawEllipse(int(sx - r), int(sy - r), 2 * r, 2 * r)
        elif num_points == 1:
            # Single point: draw in green unless selected
            x, y = self.pattern.points[0]
            if self.is_point_selected(0):
                painter.setBrush(QBrush(QColor(0, 80, 200)))  # Blue if selected
            else:
                painter.setBrush(QBrush(self.COLOR_FIRST_POINT))
            sx, sy = self.canvas_to_screen(x, y)
            painter.drawEllipse(int(sx - r), int(sy - r), 2 * r, 2 * r)
        
        # Draw all other selected points (not first, not last) on top in blue
        for i in range(1, num_points - 1):
            if self.is_point_selected(i):
                x, y = self.pattern.points[i]
                painter.setBrush(QBrush(QColor(0, 80, 200)))
                sx, sy = self.canvas_to_screen(x, y)
                painter.drawEllipse(int(sx - r), int(sy - r), 2 * r, 2 * r)

        # Tool overlay
        if self._tool:
            self._tool.paint_overlay(self, painter)

        painter.end()

    # ── Mouse events ──

    def mousePressEvent(self, event):
        if self._tool:
            self._tool.mouse_press(self, event)

    def mouseMoveEvent(self, event):
        cx, cy = self.screen_to_canvas(event.x(), event.y())
        self.cursor_moved.emit(cx, cy)
        if self._tool:
            self._tool.mouse_move(self, event)

    def mouseReleaseEvent(self, event):
        if self._tool:
            self._tool.mouse_release(self, event)

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
                # Move all selected points
                selected_indices = self.get_selected_indices()
                for idx in selected_indices:
                    x, y = self.pattern.points[idx]
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
