"""Tool classes for the stitch canvas."""

import os
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPen, QColor, QCursor, QPixmap


class BaseTool:
    """Abstract base for canvas tools."""

    name = ""
    cursor = Qt.ArrowCursor

    def mouse_press(self, canvas, event):
        pass

    def mouse_move(self, canvas, event):
        pass

    def mouse_release(self, canvas, event):
        pass

    def paint_overlay(self, canvas, painter):
        pass


class PanTool(BaseTool):
    """Pan/move the canvas view."""

    name = "Pan"
    cursor = Qt.OpenHandCursor

    def __init__(self):
        self._dragging = False
        self._last_global_pos = None
        self._scroll_area = None

    def _get_scroll_area(self, canvas):
        """Get and cache the scroll area reference."""
        if self._scroll_area is None:
            viewport = canvas.parent()
            if viewport:
                scroll_area = viewport.parent()
                if scroll_area and hasattr(scroll_area, 'horizontalScrollBar'):
                    self._scroll_area = scroll_area
        return self._scroll_area

    def mouse_press(self, canvas, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_global_pos = event.globalPos()
            canvas.setCursor(Qt.ClosedHandCursor)

    def mouse_move(self, canvas, event):
        if self._dragging and self._last_global_pos:
            current_global_pos = event.globalPos()
            delta = self._last_global_pos - current_global_pos
            
            scroll_area = self._get_scroll_area(canvas)
            if scroll_area:
                h_bar = scroll_area.horizontalScrollBar()
                v_bar = scroll_area.verticalScrollBar()
                h_bar.setValue(h_bar.value() + delta.x())
                v_bar.setValue(v_bar.value() + delta.y())
            
            self._last_global_pos = current_global_pos

    def mouse_release(self, canvas, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self._last_global_pos = None
            canvas.setCursor(self.cursor)


class AddPointTool(BaseTool):
    """Click to append a stitch point at the snapped position."""

    name = "Add Stitch Point"
    cursor = Qt.CrossCursor

    def __init__(self):
        self._cursor_x = None
        self._cursor_y = None

    def mouse_press(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        cx, cy = int(round(cx)), int(round(cy))
        if 0 <= cx <= canvas.pattern.CANVAS_WIDTH and 0 <= cy <= canvas.pattern.CANVAS_HEIGHT:
            canvas.pattern.add_point(cx, cy)
            canvas.update()
            canvas.notify_change()

    def mouse_move(self, canvas, event):
        self._cursor_x, self._cursor_y = canvas.screen_to_canvas(event.x(), event.y())
        canvas.update()

    def on_deselect(self, canvas):
        """Clear the preview when tool is deselected."""
        self._cursor_x = None
        self._cursor_y = None
        canvas.update()

    def paint_overlay(self, canvas, painter):
        if self._cursor_x is None or self._cursor_y is None:
            return
        
        # Clamp cursor position to canvas bounds
        cx = max(0, min(canvas.pattern.CANVAS_WIDTH, self._cursor_x))
        cy = max(0, min(canvas.pattern.CANVAS_HEIGHT, self._cursor_y))
        
        # Draw faint line to previous point
        if len(canvas.pattern.points) > 0:
            prev_x, prev_y = canvas.pattern.points[-1]
            sx1, sy1 = canvas.canvas_to_screen(prev_x, prev_y)
            sx2, sy2 = canvas.canvas_to_screen(cx, cy)
            
            line_pen = QPen(QColor(0, 80, 200, 100), 1)  # Faint blue line
            painter.setPen(line_pen)
            painter.drawLine(int(sx1), int(sy1), int(sx2), int(sy2))
        
        # Draw preview point
        sx, sy = canvas.canvas_to_screen(cx, cy)
        preview_pen = QPen(QColor(0, 80, 200, 150), 1)  # Faint point outline
        painter.setPen(preview_pen)
        painter.setBrush(QColor(0, 80, 200, 50))  # Very faint filled point
        r = canvas.POINT_RADIUS
        painter.drawEllipse(int(sx - r), int(sy - r), 2 * r, 2 * r)


class EditPointTool(BaseTool):
    """Click and drag to move an existing stitch point."""

    name = "Edit Stitch Point"
    cursor = Qt.CrossCursor
    HIT_RADIUS_CANVAS = 2  # in canvas units

    def __init__(self):
        self._dragging_index = None
        self._orig_pos = None

    def _find_nearest(self, canvas, cx, cy):
        """Return index of the nearest point within hit radius, or None."""
        best_idx = None
        best_dist_sq = float('inf')
        for i, (px, py) in enumerate(canvas.pattern.points):
            d = (px - cx) ** 2 + (py - cy) ** 2
            if d < best_dist_sq:
                best_dist_sq = d
                best_idx = i
        if best_idx is not None and best_dist_sq <= self.HIT_RADIUS_CANVAS ** 2:
            return best_idx
        return None

    def mouse_press(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        idx = self._find_nearest(canvas, cx, cy)
        if idx is not None:
            self._dragging_index = idx
            self._orig_pos = canvas.pattern.points[idx]
            canvas.setCursor(Qt.CrossCursor)

    def mouse_move(self, canvas, event):
        if self._dragging_index is not None:
            cx, cy = canvas.screen_to_canvas(event.x(), event.y())
            cx = max(0, min(canvas.pattern.CANVAS_WIDTH, int(round(cx))))
            cy = max(0, min(canvas.pattern.CANVAS_HEIGHT, int(round(cy))))
            # Live preview: temporarily set position (not via command)
            canvas.pattern.points[self._dragging_index] = (cx, cy)
            canvas.update()

    def mouse_release(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        if self._dragging_index is not None:
            idx = self._dragging_index
            final_pos = canvas.pattern.points[idx]
            # Restore original so move_point command captures the diff correctly
            canvas.pattern.points[idx] = self._orig_pos
            canvas.pattern.move_point(idx, final_pos[0], final_pos[1])
            self._dragging_index = None
            self._orig_pos = None
            canvas.setCursor(self.cursor)
            canvas.update()
            canvas.notify_change()


class DeletePointTool(BaseTool):
    """Click to delete a stitch point."""

    name = "Delete Stitch Point"
    cursor = Qt.CrossCursor
    HIT_RADIUS_CANVAS = 2  # in canvas units

    def __init__(self):
        """Initialize the tool with a custom cursor from the icon."""
        # Create custom cursor from the eraser icon
        icon_path = os.path.join(os.path.dirname(__file__), "icons", "delete_point.svg")
        pixmap = QPixmap(icon_path)
        if not pixmap.isNull():
            # Scale the pixmap to a reasonable cursor size (32x32)
            pixmap = pixmap.scaledToWidth(32, Qt.SmoothTransformation)
            self.cursor = QCursor(pixmap, hotX=11, hotY=27)
        else:
            # Fallback to CrossCursor if icon can't be loaded
            self.cursor = Qt.CrossCursor

    def _find_nearest(self, canvas, cx, cy):
        """Return index of the nearest point within hit radius, or None."""
        best_idx = None
        best_dist_sq = float('inf')
        for i, (px, py) in enumerate(canvas.pattern.points):
            d = (px - cx) ** 2 + (py - cy) ** 2
            if d < best_dist_sq:
                best_dist_sq = d
                best_idx = i
        if best_idx is not None and best_dist_sq <= self.HIT_RADIUS_CANVAS ** 2:
            return best_idx
        return None

    def mouse_press(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        idx = self._find_nearest(canvas, cx, cy)
        if idx is not None:
            canvas.pattern.delete_point(idx)
            canvas.update()
            canvas.notify_change()
