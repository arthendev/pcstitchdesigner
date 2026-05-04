"""Tool classes for the stitch canvas."""

import os
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPen, QColor, QCursor, QPixmap

from model import AddPointCommand, DeletePointCommand, MovePointCommand, MoveManyPointsCommand


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

    def key_press(self, canvas, event):
        """Handle keyboard events. Return True if event is handled, False otherwise."""
        return False

    def paint_overlay(self, canvas, painter):
        pass


class SelectPointTool(BaseTool):
    """Select stitch points. Click to select single point. Click and drag to select range."""

    name = "Select Stitch Point"
    cursor = Qt.CrossCursor
    HIT_RADIUS_CANVAS = 2  # in canvas units

    def __init__(self):
        """Initialize the tool with a custom cursor from the icon."""
        # Create custom cursor from the select_point icon
        icon_path = os.path.join(os.path.dirname(__file__), "icons", "select_point.svg")
        pixmap = QPixmap(icon_path)
        if not pixmap.isNull():
            self.cursor = QCursor(pixmap, hotX=6, hotY=3)
        else:
            # Fallback to CrossCursor if icon can't be loaded
            self.cursor = Qt.CrossCursor
        
        self._dragging = False
        self._drag_start_idx = None  # Index of point where drag started

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
            # Start drag from this point
            self._dragging = True
            self._drag_start_idx = idx
            canvas.set_selected_point(idx)
        else:
            # Clicked on empty space, deselect
            canvas.set_selected_point(None)
        canvas.update()

    def mouse_move(self, canvas, event):
        if not self._dragging or self._drag_start_idx is None:
            return
        
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        # Find the nearest point to current cursor position
        current_idx = self._find_nearest(canvas, cx, cy)
        
        if current_idx is not None:
            # Extend selection from drag_start_idx to current_idx
            start = min(self._drag_start_idx, current_idx)
            end = max(self._drag_start_idx, current_idx)
            canvas.set_selection(start, end)
        else:
            # If no point is near cursor, keep the original single-point selection
            canvas.set_selected_point(self._drag_start_idx)
        canvas.update()

    def mouse_release(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        self._dragging = False
        self._drag_start_idx = None


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

    def key_press(self, canvas, event):
        """Handle keyboard events. Backspace triggers Undo only if last action was AddPointCommand."""
        if event.key() == Qt.Key_Backspace and not event.isAutoRepeat():
            # Only undo if the last command in undo stack is an AddPointCommand
            if canvas.pattern._undo_stack and isinstance(canvas.pattern._undo_stack[-1], AddPointCommand):
                canvas.pattern.undo()
                canvas.update()
                canvas.notify_change()
                event.accept()
                return True
        return False

    def mouse_press(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        cx, cy = int(round(cx)), int(round(cy))
        if 0 <= cx <= canvas.pattern.CANVAS_WIDTH and 0 <= cy <= canvas.pattern.CANVAS_HEIGHT:
            # If points are selected, insert after the end of selection; otherwise append
            start, end = canvas.get_selection()
            if start is not None and end is not None:
                # Use end index for range selection
                insert_idx = end + 1
                canvas.pattern.add_point(cx, cy, index=insert_idx)
                canvas.set_selected_point(insert_idx)  # Select the newly added point
            else:
                # Fallback: use start of selection if only single point selected
                selected_idx = canvas.get_selected_point()
                if selected_idx is not None:
                    insert_idx = selected_idx + 1
                    canvas.pattern.add_point(cx, cy, index=insert_idx)
                    canvas.set_selected_point(insert_idx)  # Select the newly added point
                else:
                    # No selection: append to end
                    insert_idx = len(canvas.pattern.points)
                    canvas.pattern.add_point(cx, cy)
                    canvas.set_selected_point(insert_idx)  # Select the newly added point
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
        
        # Draw faint lines connecting to cursor
        line_pen = QPen(QColor(0, 80, 200, 100), 1)  # Faint blue line
        painter.setPen(line_pen)
        
        cursor_sx, cursor_sy = canvas.canvas_to_screen(cx, cy)
        
        # Get reference point: use end of selection if range selected, otherwise start point
        start, end = canvas.get_selection()
        ref_idx = None
        if start is not None and end is not None:
            ref_idx = end
        else:
            ref_idx = canvas.get_selected_point()
        
        if ref_idx is not None and ref_idx < len(canvas.pattern.points):
            # Draw line from reference point to cursor
            prev_x, prev_y = canvas.pattern.points[ref_idx]
            sx1, sy1 = canvas.canvas_to_screen(prev_x, prev_y)
            painter.drawLine(int(sx1), int(sy1), int(cursor_sx), int(cursor_sy))
            
            # Draw line from cursor to the following point (if it exists)
            if ref_idx + 1 < len(canvas.pattern.points):
                next_x, next_y = canvas.pattern.points[ref_idx + 1]
                sx2, sy2 = canvas.canvas_to_screen(next_x, next_y)
                painter.drawLine(int(cursor_sx), int(cursor_sy), int(sx2), int(sy2))
        elif len(canvas.pattern.points) > 0:
            # Fallback: draw line from last point to cursor (when no point is selected)
            prev_x, prev_y = canvas.pattern.points[-1]
            sx1, sy1 = canvas.canvas_to_screen(prev_x, prev_y)
            painter.drawLine(int(sx1), int(sy1), int(cursor_sx), int(cursor_sy))
        
        # Draw preview point
        sx, sy = canvas.canvas_to_screen(cx, cy)
        preview_pen = QPen(QColor(0, 80, 200, 150), 1)  # Faint point outline
        painter.setPen(preview_pen)
        painter.setBrush(QColor(0, 80, 200, 50))  # Very faint filled point
        r = canvas._point_radius
        painter.drawEllipse(int(sx - r), int(sy - r), 2 * r, 2 * r)


class MovePointTool(BaseTool):
    """Click and drag to move existing stitch point(s)."""

    name = "Move Stitch Point"
    cursor = Qt.CrossCursor
    SNAP_RADIUS_PX = 16  # screen pixels for hit detection (zoom-independent)

    def __init__(self):
        self._dragging_indices = []  # List of indices being dragged
        self._orig_positions = []    # Original positions of dragged points
        self._clicked_idx = None     # Index of the point that was clicked
        self._offset_x = 0
        self._offset_y = 0
        self._empty_click = False    # True when press landed far from any point
        self._press_screen_pos = None  # Screen pos of last empty-space press

    def key_press(self, canvas, event):
        """Backspace triggers Undo but only if last action was MovePointCommand.
        Handle Ctrl+arrow shortcuts for selection navigation.

        Ctrl+Right: move selection 1 point towards end of pattern
        Ctrl+Left:  move selection 1 point towards beginning of pattern
        Ctrl+Up:    extend selection by 1 point towards end
        Ctrl+Down:  reduce selection by 1 point from the end side
        """
        if not (event.modifiers() & Qt.ControlModifier):
            return False

        start = canvas._selection_start
        end = canvas._selection_end

        if start is None or end is None:
            return False

        n = len(canvas.pattern.points)
        if n == 0:
            return False

        key = event.key()

        if key == Qt.Key_Backspace and not event.isAutoRepeat():
            # Only undo if the last command in undo stack is a MovePointCommand
            if canvas.pattern._undo_stack and isinstance(canvas.pattern._undo_stack[-1], MovePointCommand):
                canvas.pattern.undo()
                canvas.update()
                canvas.notify_change()
                event.accept()
                return True
        elif key == Qt.Key_Right:
            # Shift entire selection one step toward end
            if end < n - 1:
                canvas.set_selection(start + 1, end + 1)
            event.accept()
            return True
        elif key == Qt.Key_Left:
            # Shift entire selection one step toward beginning
            if start > 0:
                canvas.set_selection(start - 1, end - 1)
            event.accept()
            return True
        elif key == Qt.Key_Up:
            # Extend selection one more point toward end
            if end < n - 1:
                canvas.set_selection(start, end + 1)
            event.accept()
            return True
        elif key == Qt.Key_Down:
            # Shrink selection by removing the point closest to end
            if end > start:
                canvas.set_selection(start, end - 1)
            event.accept()
            return True

        return False

    def _find_nearest(self, canvas, cx, cy):
        """Return index of the nearest point within SNAP_RADIUS_PX screen pixels, or None."""
        radius_canvas = self.SNAP_RADIUS_PX / canvas.get_scale()
        best_idx = None
        best_dist_sq = float('inf')
        for i, (px, py) in enumerate(canvas.pattern.points):
            d = (px - cx) ** 2 + (py - cy) ** 2
            if d < best_dist_sq:
                best_dist_sq = d
                best_idx = i
        if best_idx is not None and best_dist_sq <= radius_canvas ** 2:
            return best_idx
        return None

    def mouse_press(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        idx = self._find_nearest(canvas, cx, cy)
        if idx is not None:
            self._empty_click = False
            # Store the clicked point index
            self._clicked_idx = idx
            # Check if clicked point is in current selection
            start, end = canvas.get_selection()
            if start is not None and start <= idx <= end:
                # Clicked point is in selection: drag all selected points
                self._dragging_indices = canvas.get_selected_indices()
            else:
                # Clicked point is not in selection: select only this point and drag it
                canvas.set_selected_point(idx)
                self._dragging_indices = [idx]
            
            # Store original positions
            self._orig_positions = [canvas.pattern.points[i] for i in self._dragging_indices]
            self._offset_x = 0
            self._offset_y = 0
            canvas.setCursor(Qt.CrossCursor)
        else:
            self._empty_click = True
            self._press_screen_pos = (event.x(), event.y())

    def mouse_move(self, canvas, event):
        if self._empty_click and self._press_screen_pos is not None:
            dx = event.x() - self._press_screen_pos[0]
            dy = event.y() - self._press_screen_pos[1]
            if dx * dx + dy * dy > 16:  # 4 px threshold
                self._empty_click = False
                self._press_screen_pos = None
        if self._dragging_indices:
            cx, cy = canvas.screen_to_canvas(event.x(), event.y())
            cx = max(0, min(canvas.pattern.CANVAS_WIDTH, int(round(cx))))
            cy = max(0, min(canvas.pattern.CANVAS_HEIGHT, int(round(cy))))
            
            # Calculate offset from the clicked point's original position
            if self._clicked_idx is not None:
                # Find the clicked point in the dragging indices to get its original position
                try:
                    clicked_pos_idx = self._dragging_indices.index(self._clicked_idx)
                    clicked_orig = self._orig_positions[clicked_pos_idx]
                except (ValueError, IndexError):
                    # Fallback to first point if clicked point not found
                    clicked_orig = self._orig_positions[0]
            else:
                clicked_orig = self._orig_positions[0]
            
            self._offset_x = cx - clicked_orig[0]
            self._offset_y = cy - clicked_orig[1]
            
            # Live preview: temporarily update positions
            for i, idx in enumerate(self._dragging_indices):
                orig_x, orig_y = self._orig_positions[i]
                new_x = max(0, min(canvas.pattern.CANVAS_WIDTH, orig_x + self._offset_x))
                new_y = max(0, min(canvas.pattern.CANVAS_HEIGHT, orig_y + self._offset_y))
                canvas.pattern.points[idx] = (new_x, new_y)
            canvas.update()

    def mouse_release(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        if self._empty_click:
            self._empty_click = False
            self._press_screen_pos = None
            canvas.set_selection(None, None)
            canvas.update()
            return
        if self._dragging_indices:
            # Restore live-preview positions to originals, then commit all moves
            # as a single undoable step.
            for i, idx in enumerate(self._dragging_indices):
                canvas.pattern.points[idx] = self._orig_positions[i]

            new_positions = []
            for i, idx in enumerate(self._dragging_indices):
                orig_x, orig_y = self._orig_positions[i]
                new_x = max(0, min(canvas.pattern.CANVAS_WIDTH, orig_x + self._offset_x))
                new_y = max(0, min(canvas.pattern.CANVAS_HEIGHT, orig_y + self._offset_y))
                new_positions.append((new_x, new_y))

            if len(self._dragging_indices) == 1:
                idx = self._dragging_indices[0]
                new_x, new_y = new_positions[0]
                canvas.pattern.move_point(idx, new_x, new_y)
            else:
                canvas.pattern.move_points(self._dragging_indices, new_positions)
            
            self._dragging_indices = []
            self._orig_positions = []
            self._clicked_idx = None
            self._offset_x = 0
            self._offset_y = 0
            canvas.setCursor(self.cursor)
            canvas.update()
            canvas.notify_change()


class DeletePointTool(BaseTool):
    """Click to delete a stitch point."""

    name = "Delete Stitch Point"
    # cursor = Qt.CrossCursor
    HIT_RADIUS_CANVAS = 2  # in canvas units

    def __init__(self):
        """Initialize the tool with a custom cursor from the icon."""
        # Create custom cursor from the eraser icon
        icon_path = os.path.join(os.path.dirname(__file__), "icons", "delete_point.svg")
        pixmap = QPixmap(icon_path)
        if not pixmap.isNull():
            self.cursor = QCursor(pixmap, hotX=8, hotY=21)
        else:
            # Fallback to CrossCursor if icon can't be loaded
            self.cursor = Qt.CrossCursor

    def key_press(self, canvas, event):
        """Handle keyboard events. Backspace triggers Undo only if last action was DeletePointCommand."""
        if event.key() == Qt.Key_Backspace and not event.isAutoRepeat():
            # Only undo if the last command in undo stack is a DeletePointCommand
            if canvas.pattern._undo_stack and isinstance(canvas.pattern._undo_stack[-1], DeletePointCommand):
                canvas.pattern.undo()
                canvas.update()
                canvas.notify_change()
                event.accept()
                return True
        return False

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
