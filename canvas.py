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

    MARGIN = 20  # pixel margin around the drawing area
    POINT_RADIUS = 4  # pixels

    # Colors
    COLOR_GRID = QColor(220, 220, 220)
    COLOR_BORDER = QColor(0, 0, 0)
    COLOR_POINT = QColor(0, 80, 200)
    COLOR_LINE = QColor(0, 80, 200)
    COLOR_BG = QColor(255, 255, 255)
    COLOR_FIRST_POINT = QColor(0, 200, 0)  # Green for first point
    COLOR_LAST_POINT = QColor(200, 0, 0)   # Red for last point

    def __init__(self, pattern, parent=None):
        super().__init__(parent)
        self.pattern = pattern
        self._scale = 10.0  # pixels per canvas unit
        self._tool = None
        self.setMouseTracking(True)
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
                painter.drawLine(int(sx1), int(sy1), int(sx2), int(sy2))

        # Stitch points
        painter.setPen(Qt.NoPen)
        r = self.POINT_RADIUS
        num_points = len(self.pattern.points)
        for i, (x, y) in enumerate(self.pattern.points):
            # Choose color based on position
            if num_points > 1 and i == 0:
                # First point is green
                painter.setBrush(QBrush(self.COLOR_FIRST_POINT))
            elif num_points > 1 and i == num_points - 1:
                # Last point is red
                painter.setBrush(QBrush(self.COLOR_LAST_POINT))
            else:
                # Other points are blue
                painter.setBrush(QBrush(self.COLOR_POINT))
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

    # ── Notifications ──

    def notify_change(self):
        """Called by tools after modifying the pattern."""
        self.changed.emit()
