"""Tool classes for the stitch canvas."""

import math
import os
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import (QPen, QColor, QCursor, QPixmap, QBrush, QPainter,
                         QPolygon)

from model import (AddPointCommand, DeletePointCommand, MovePointCommand,
                   MoveManyPointsCommand, elem_has_coords, is_embroidery_type,
                   ELEM_COLOR, ELEM_SECTION)

# Hit radius, in screen pixels, for grabbing stitch points to move. Screen based
# so the grab area feels the same at every zoom level.
MOVE_SNAP_RADIUS_PX = 16

# How far the cursor has to travel before a press counts as a drag. A click that
# stays inside this radius keeps the tool's own click behaviour.
DRAG_START_PX = 4


def find_nearest_coord_index(pattern, cx, cy, radius_canvas):
    """Return the index of the coordinate element nearest to (*cx*, *cy*).

    Args:
        pattern: The StitchPattern to search.
        cx, cy: Click position in canvas units.
        radius_canvas: Maximum distance in canvas units; farther points do not
            count as a hit.

    Returns:
        The element index, or None when nothing is within the radius.
    """
    best_idx = None
    best_dist_sq = float('inf')
    for i, elem in enumerate(pattern.elements):
        if not elem_has_coords(elem):
            continue
        px, py = elem[1], elem[2]
        d = (px - cx) ** 2 + (py - cy) ** 2
        if d < best_dist_sq:
            best_dist_sq = d
            best_idx = i
    if best_idx is not None and best_dist_sq <= radius_canvas ** 2:
        return best_idx
    return None


def point_in_polygon(px, py, polygon):
    """Return True when the point (*px*, *py*) lies inside the closed *polygon*.

    Standard ray casting: count the polygon edges crossed by the horizontal ray
    going right from the point; an odd count means inside. The polygon is closed
    implicitly (last vertex joins the first).

    Args:
        px, py: Point to test.
        polygon: Sequence of ``(x, y)`` vertices.
    """
    if len(polygon) < 3:
        return False
    inside = False
    xj, yj = polygon[-1]
    for xi, yi in polygon:
        if (yi > py) != (yj > py):
            x_cross = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < x_cross:
                inside = not inside
        xj, yj = xi, yi
    return inside


def distance_to_segment(px, py, ax, ay, bx, by):
    """Return the distance from (*px*, *py*) to the segment (ax, ay)-(bx, by)."""
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    # Projection of the point onto the line, clamped to the segment.
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def find_nearest_section_hit(pattern, cx, cy, radius_canvas):
    """Return the element index whose section is under (*cx*, *cy*).

    Stitch points win over the lines joining them. A line hit reports its start
    point, because that is the section the line is drawn in — the marker a line
    ends on opens the *next* section. Lines are not drawn across a colour change
    (the thread is cut there), so such a gap is not a hit either.

    Args:
        pattern: The StitchPattern to search.
        cx, cy: Cursor position in canvas units.
        radius_canvas: Hit radius in canvas units.

    Returns:
        The element index, or None when neither a point nor a line is close.
    """
    index = find_nearest_coord_index(pattern, cx, cy, radius_canvas)
    if index is not None:
        return index

    best_idx = None
    best_dist = radius_canvas
    previous = None  # (index, x, y) of the previous coordinate element
    for i, elem in enumerate(pattern.elements):
        if elem[0] == ELEM_COLOR:
            previous = None
            continue
        if not elem_has_coords(elem):
            continue
        if previous is not None:
            dist = distance_to_segment(cx, cy, previous[1], previous[2],
                                       elem[1], elem[2])
            if dist <= best_dist:
                best_dist = dist
                best_idx = previous[0]
        previous = (i, elem[1], elem[2])
    return best_idx


def find_nearest_selected_hit(canvas, cx, cy, radius_canvas):
    """Return the selected element that is nearest to (*cx*, *cy*), or None.

    This is the grab test for moving a selection: a press inside the radius
    picks the selection up. Selected points win over the lines joining them, and
    only lines between two *consecutive* selected points count, so a gap the
    selection does not span is not a hit.

    Args:
        canvas: The StitchCanvas holding the selection.
        cx, cy: Cursor position in canvas units.
        radius_canvas: Hit radius in canvas units.

    Returns:
        The element index, or None when nothing selected is that close.
    """
    selected = canvas.get_selected_indices()
    if not selected:
        return None

    elements = canvas.pattern.elements
    best_idx = None
    best_dist = radius_canvas
    for idx in selected:
        elem = elements[idx]
        if not elem_has_coords(elem):
            continue
        dist = math.hypot(elem[1] - cx, elem[2] - cy)
        if dist <= best_dist:
            best_dist = dist
            best_idx = idx
    if best_idx is not None:
        return best_idx

    for previous, current in zip(selected, selected[1:]):
        if current != previous + 1:
            continue  # the selection skips an element, so no line is drawn
        start, end = elements[previous], elements[current]
        if not (elem_has_coords(start) and elem_has_coords(end)):
            continue
        dist = distance_to_segment(cx, cy, start[1], start[2], end[1], end[2])
        if dist <= best_dist:
            best_dist = dist
            best_idx = previous
    return best_idx


def highlight_color(color):
    """Return a gently shifted version of *color* for hover previews.

    The shift has to be noticeable without drowning out the design: dark colours
    are lightened a little, bright colours darkened a little, and the saturation
    is raised slightly so the tint stays recognisable. Colours without a hue
    (black, white, grey) are tinted blue, because for them brightness alone
    barely shows.

    Args:
        color: QColor whose RGB values are used; alpha is ignored.

    Returns:
        QColor suitable for the preview pen and brush.
    """
    hue, sat, value, _alpha = color.getHsv()
    if value < 110:
        # Dark thread: lighten it so the preview is visible on dark stitches.
        value = min(255, value + 70)
        sat = min(255, sat + 15)
    else:
        # Bright thread: darken it a little instead of glowing into the canvas.
        value = max(0, value - 50)
        sat = min(255, sat + 10)
    if hue < 0:
        # Achromatic colours have no hue to keep — give them one.
        hue, sat = 210, max(sat, 140)
    return QColor.fromHsv(hue, sat, value)


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


class PointDragMixin:
    """Shared "drag points to move them" mechanics.

    Holds the dragged indices and their original elements, previews the moved
    positions on the canvas, and commits the whole move as one undoable command
    when the button is released. The Move Stitch Point tool and the selection
    tools share it, so dragging behaves the same way everywhere.

    Only coordinate elements are moved; colour changes, and section markers
    without coordinates, stay where they are.
    """

    def _init_point_drag(self):
        self._drag_indices = []        # element indices being dragged
        self._drag_originals = []      # their elements before the drag
        self._drag_anchor = None       # element index the cursor grabbed
        self._drag_start_canvas = None  # press point for cursor-relative drags
        self._drag_offset = (0.0, 0.0)  # canvas-space move applied so far

    @property
    def _is_dragging(self):
        """True while stitch points are being dragged."""
        return bool(self._drag_indices)

    def _begin_point_drag(self, canvas, anchor_index):
        """Start dragging the selection, anchored at *anchor_index*.

        When *anchor_index* is not part of the selection, only that point is
        dragged — the Move Stitch Point tool relies on this after it selects a
        point the user grabbed outside the selection.
        """
        indices = canvas.get_selected_indices()
        if anchor_index not in indices:
            indices = [anchor_index]
        self._drag_indices = list(indices)
        self._drag_originals = [canvas.pattern.elements[i] for i in self._drag_indices]
        self._drag_anchor = anchor_index
        self._drag_start_canvas = None
        self._drag_offset = (0.0, 0.0)

    def _begin_cursor_point_drag(self, canvas, press_canvas):
        """Move the selection by the cursor's travel from *press_canvas*.

        Used when the drag starts on empty canvas: there is no grabbed point, so
        the whole selection follows the cursor delta instead of jumping to it.

        Returns:
            bool: True when there was a selection to move.
        """
        indices = canvas.get_selected_indices()
        if not indices:
            return False
        self._drag_indices = list(indices)
        self._drag_originals = [canvas.pattern.elements[i] for i in self._drag_indices]
        self._drag_anchor = None
        self._drag_start_canvas = press_canvas
        self._drag_offset = (0.0, 0.0)
        return True

    def _cursor_canvas_pos(self, canvas, event):
        """Clipped, optionally grid-snapped canvas position of the cursor."""
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        width, height = canvas.pattern.CANVAS_WIDTH, canvas.pattern.CANVAS_HEIGHT
        if canvas.snap_normal_to_grid:
            return (max(0, min(width, int(round(cx)))),
                    max(0, min(height, int(round(cy)))))
        return (max(0.0, min(float(width), cx)),
                max(0.0, min(float(height), cy)))

    def _update_point_drag(self, canvas, event):
        """Move the dragged points so the grabbed point follows the cursor."""
        cx, cy = self._cursor_canvas_pos(canvas, event)
        if self._drag_anchor is None:
            # Started on empty canvas: move by the cursor delta.
            sx, sy = self._drag_start_canvas
            self._drag_offset = (cx - sx, cy - sy)
        else:
            anchor = self._drag_originals[self._drag_indices.index(self._drag_anchor)]
            self._drag_offset = (cx - anchor[1], cy - anchor[2])
        width, height = canvas.pattern.CANVAS_WIDTH, canvas.pattern.CANVAS_HEIGHT
        for index, original in zip(self._drag_indices, self._drag_originals):
            if not elem_has_coords(original):
                continue
            canvas.pattern.elements[index] = (
                original[0],
                max(0, min(width, original[1] + self._drag_offset[0])),
                max(0, min(height, original[2] + self._drag_offset[1])),
            )
        canvas.pattern._rebuild_display_no_auto()
        canvas.update()

    def _restore_dragged_points(self, canvas):
        """Put the original elements back, undoing the live preview."""
        for index, original in zip(self._drag_indices, self._drag_originals):
            canvas.pattern.elements[index] = original
        self._init_point_drag()

    def _finish_point_drag(self, canvas):
        """Commit the dragged positions as one undoable step."""
        moves = [(index, (original[1] + self._drag_offset[0],
                          original[2] + self._drag_offset[1]))
                 for index, original in zip(self._drag_indices, self._drag_originals)
                 if elem_has_coords(original)]
        # The preview edited `elements` directly, so restore the originals first:
        # the model then records the move and undo has a clean "before" state.
        self._restore_dragged_points(canvas)
        if len(moves) == 1:
            index, (x, y) = moves[0]
            canvas.pattern.move_point(index, x, y, snap=canvas.snap_normal_to_grid)
        elif moves:
            canvas.pattern.move_points([index for index, _ in moves],
                                       [position for _, position in moves],
                                       snap=canvas.snap_normal_to_grid)
        canvas.pattern._rebuild_display_no_auto()
        canvas.update()
        canvas.notify_change()
        canvas.drag_finished.emit()

    def _cancel_point_drag(self, canvas):
        """Drop a drag without committing it."""
        if not self._is_dragging:
            return
        self._restore_dragged_points(canvas)
        canvas.pattern._rebuild_display_no_auto()
        canvas.update()


class SelectionMoveMixin(PointDragMixin):
    """Move the current selection by dragging near it, from a selection tool.

    The press has to prove itself a drag before it becomes a move, because a
    plain click still means "select" for every selection tool. So the press only
    remembers a candidate anchor and the move starts once the cursor has
    travelled DRAG_START_PX. The active tool and its cursor are left alone: the
    point is to move the selection without switching to the Move Stitch Point
    tool.
    """

    def _init_selection_move(self):
        self._init_point_drag()
        self._pending_anchor = None       # candidate element index, or None
        self._pending_click = None        # canvas position of the pending press
        self._pending_press_screen = None  # screen position of the pending press

    def _selection_move_press(self, canvas, event, cx, cy):
        """Remember a press that may become a selection move. True when it did."""
        self._clear_selection_move()
        if not canvas.get_selected_indices():
            return False
        anchor = find_nearest_selected_hit(
            canvas, cx, cy, MOVE_SNAP_RADIUS_PX / canvas.get_scale())
        if anchor is None:
            return False
        self._pending_anchor = anchor
        self._pending_click = (cx, cy)
        self._pending_press_screen = (event.x(), event.y())
        return True

    def _selection_move_drag(self, canvas, event):
        """Advance a pending or running move. True when the move owns the event."""
        if not self._is_dragging:
            if self._pending_anchor is None:
                return False
            dx = event.x() - self._pending_press_screen[0]
            dy = event.y() - self._pending_press_screen[1]
            if dx * dx + dy * dy <= DRAG_START_PX ** 2:
                # Still a click: keep the tool's own mouse-move handling out.
                return True
            self._begin_point_drag(canvas, self._pending_anchor)
        self._update_point_drag(canvas, event)
        return True

    def _selection_move_release(self, canvas):
        """Commit a running move, or replay the click it turned out to be."""
        if self._is_dragging:
            self._finish_point_drag(canvas)
            # The pending press has to go too: leaving it armed would let the
            # next cursor move (button already released) start a new drag.
            self._clear_selection_move()
            return True
        if self._pending_anchor is None:
            return False
        cx, cy = self._pending_click
        self._clear_selection_move()
        self._on_selection_click(canvas, cx, cy)
        return True

    def _selection_move_cancel(self, canvas):
        """Abandon a pending move or an uncommitted drag."""
        self._cancel_point_drag(canvas)
        self._clear_selection_move()

    def _clear_selection_move(self):
        """Forget the pending press."""
        self._pending_anchor = None
        self._pending_click = None
        self._pending_press_screen = None

    def _on_selection_click(self, canvas, cx, cy):
        """Handle a press that never became a drag — the tool's own click."""
        raise NotImplementedError


class SelectPointTool(SelectionMoveMixin, BaseTool):
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
        self._init_selection_move()

    def _find_nearest(self, canvas, cx, cy):
        """Return element index of the nearest coord element within hit radius, or None."""
        return find_nearest_coord_index(canvas.pattern, cx, cy, self.HIT_RADIUS_CANVAS)

    def _select_point_at(self, canvas, cx, cy):
        """Select the nearest point, or deselect. Returns the index, or None."""
        idx = self._find_nearest(canvas, cx, cy)
        canvas.set_selected_point(idx)
        canvas.update()
        return idx

    def _on_selection_click(self, canvas, cx, cy):
        """The press near the selection never dragged, so it was just a click."""
        self._select_point_at(canvas, cx, cy)

    def mouse_press(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        if self._selection_move_press(canvas, event, cx, cy):
            return
        idx = self._select_point_at(canvas, cx, cy)
        if idx is not None:
            # Start drag from this point
            self._dragging = True
            self._drag_start_idx = idx

    def mouse_move(self, canvas, event):
        if self._selection_move_drag(canvas, event):
            return
        if not self._dragging or self._drag_start_idx is None:
            return

        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        current_idx = self._find_nearest(canvas, cx, cy)

        if current_idx is not None:
            start = min(self._drag_start_idx, current_idx)
            end = max(self._drag_start_idx, current_idx)
            canvas.set_selection(start, end)
        else:
            canvas.set_selected_point(self._drag_start_idx)
        canvas.update()

    def mouse_release(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        self._dragging = False
        self._drag_start_idx = None
        self._selection_move_release(canvas)

    def on_deselect(self, canvas):
        """Drop a pending or running selection move when another tool takes over."""
        self._dragging = False
        self._drag_start_idx = None
        self._selection_move_cancel(canvas)


class SelectSectionTool(SelectionMoveMixin, BaseTool):
    """Select a whole section. Click near a stitch point to select its section.

    A section runs between two ELEM_SECTION markers. Designs without section
    markers use their colour changes as boundaries instead, and the start and
    the end of the pattern always count as boundaries.
    """

    name = "Select Section"
    cursor = Qt.CrossCursor
    HIT_RADIUS_CANVAS = SelectPointTool.HIT_RADIUS_CANVAS  # same proximity as point selection
    # The preview only tints the stitches instead of covering them. It is drawn
    # opaque onto a small layer and composited once at this opacity, so
    # overlapping strokes cannot build up into a solid area.
    PREVIEW_ALPHA = 100
    # 9 mm / MAXI designs carry no thread palette, so there is no colour identity
    # to preserve: they get a bold, high-contrast cue instead of a tint.
    STRONG_PREVIEW_ALPHA = 235
    STRONG_PREVIEW_RGB = (255, 120, 0)   # vivid orange, unmistakable on the canvas

    def __init__(self):
        """Initialize the tool with a custom cursor from the icon."""
        icon_path = os.path.join(os.path.dirname(__file__), "icons", "select_section.svg")
        pixmap = QPixmap(icon_path)
        if not pixmap.isNull():
            self.cursor = QCursor(pixmap, hotX=6, hotY=3)
        else:
            self.cursor = Qt.CrossCursor
        self._preview = None  # (first, last) of the section under the cursor, or None
        self._init_selection_move()

    def _section_range(self, canvas, index):
        """Return the ``(first, last)`` element indices of the section holding *index*.

        Boundaries are ELEM_SECTION markers. A design without them falls back to
        its colour changes, which stay outside the selection because a colour
        change belongs to the section after it. Clicking a section marker takes
        the section that marker opens — or, for the closing marker of the last
        section, the one it closes.
        """
        elements = canvas.pattern.elements
        count = len(elements)
        if not 0 <= index < count:
            return None

        sectioned = any(e[0] == ELEM_SECTION for e in elements)
        boundary = ELEM_SECTION if sectioned else ELEM_COLOR
        markers = [i for i, e in enumerate(elements) if e[0] == boundary]
        before = [i for i in markers if i <= index]
        after = [i for i in markers if i > index]

        if sectioned:
            if before and before[-1] == index and not after:
                before = before[:-1]
            first = before[-1] if before else 0
            last = after[0] if after else count - 1
        else:
            first = before[-1] + 1 if before else 0
            last = after[0] - 1 if after else count - 1

        # A section can be a single stitch; never return an empty range.
        return max(0, min(first, index)), min(count - 1, max(last, index))

    def mouse_move(self, canvas, event):
        """Track the section under the cursor so it can be previewed."""
        if self._selection_move_drag(canvas, event):
            return
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        index = find_nearest_section_hit(canvas.pattern, cx, cy, self.HIT_RADIUS_CANVAS)
        bounds = self._section_range(canvas, index) if index is not None else None
        if bounds is not None and bounds == canvas.get_selection():
            # Already selected; its own highlight is enough.
            bounds = None
        if bounds == self._preview:
            return
        self._preview = bounds
        canvas.update()

    def on_deselect(self, canvas):
        """Drop the preview when another tool takes over."""
        self._selection_move_cancel(canvas)
        self._preview = None
        canvas.update()

    def on_leave(self, canvas):
        """Drop the preview when the cursor leaves the canvas."""
        if self._preview is None:
            return
        self._preview = None
        canvas.update()

    def paint_overlay(self, canvas, painter):
        """Draw the section that a click would select, as a soft tint."""
        if self._preview is None:
            return
        pattern = canvas.pattern
        elements = pattern.elements
        first, last = self._preview
        if not (0 <= first <= last < len(elements)):
            return

        def thread_color(index):
            """QColor of palette entry *index*, or the canvas line colour."""
            if index is None or not pattern.has_palette:
                return canvas._color_line
            return QColor(*pattern.colors[min(max(index, 0), len(pattern.colors) - 1)])

        # Collect the strokes: (screen point, tint, previous screen point).
        strong = not is_embroidery_type(pattern.stitch_type)
        if strong:
            alpha = self.STRONG_PREVIEW_ALPHA
            line_width = canvas._line_width + 2
            radius = canvas._point_radius + 1
        else:
            alpha = self.PREVIEW_ALPHA
            line_width = canvas._line_width
            radius = canvas._point_radius

        def tint_for(index):
            """Preview colour for the thread active at that point."""
            if strong:
                return QColor(*self.STRONG_PREVIEW_RGB)
            return highlight_color(thread_color(index))

        strokes = []
        color_index = pattern.get_color_at(first) if pattern.has_palette else None
        previous = None
        for i in range(first, last + 1):
            elem = elements[i]
            if elem[0] == ELEM_COLOR:
                color_index = elem[1]
                previous = None  # a colour change cuts the thread
                continue
            if not elem_has_coords(elem):
                continue
            point = canvas.canvas_to_screen(elem[1], elem[2])
            strokes.append((point, tint_for(color_index), previous))
            previous = point
        if not strokes:
            return

        margin = radius + line_width + 2
        xs = [point[0] for point, _tint, _prev in strokes]
        ys = [point[1] for point, _tint, _prev in strokes]
        left, top = int(min(xs) - margin), int(min(ys) - margin)
        width = int(max(xs) - min(xs) + 2 * margin) + 1
        height = int(max(ys) - min(ys) + 2 * margin) + 1

        layer = QPixmap(width, height)
        layer.fill(Qt.transparent)
        layer_painter = QPainter(layer)
        layer_painter.setRenderHint(QPainter.Antialiasing)
        layer_painter.translate(-left, -top)
        for point, tint, prev in strokes:
            if prev is not None:
                layer_painter.setPen(QPen(tint, line_width))
                layer_painter.drawLine(int(prev[0]), int(prev[1]),
                                       int(point[0]), int(point[1]))
            layer_painter.setPen(Qt.NoPen)
            layer_painter.setBrush(QBrush(tint))
            layer_painter.drawEllipse(int(point[0] - radius), int(point[1] - radius),
                                     radius * 2, radius * 2)
        layer_painter.end()

        painter.save()
        painter.setOpacity(alpha / 255)
        painter.drawPixmap(left, top, layer)
        painter.restore()

    def mouse_press(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        if self._selection_move_press(canvas, event, cx, cy):
            return
        self._select_section_at(canvas, cx, cy)

    def mouse_release(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        self._selection_move_release(canvas)

    def _select_section_at(self, canvas, cx, cy):
        """Select the section under (*cx*, *cy*), or deselect when there is none."""
        index = find_nearest_section_hit(canvas.pattern, cx, cy, self.HIT_RADIUS_CANVAS)
        bounds = self._section_range(canvas, index) if index is not None else None
        self._preview = None
        if bounds is None:
            canvas.set_selected_point(None)
        else:
            canvas.set_selection(*bounds)
        canvas.update()

    def _on_selection_click(self, canvas, cx, cy):
        """The press near the selection never dragged, so it was just a click."""
        self._select_section_at(canvas, cx, cy)


class SelectAreaTool(SelectionMoveMixin, BaseTool):
    """Select the stitch points inside a freehand drawn area.

    Press the left button and draw an outline; when it is released every stitch
    point inside the outline is selected. The result is an explicit index set, so
    it can pick points that do not form a continuous chain — for example both the
    outbound and the return pass where a shape is filled back and forth.
    """

    name = "Select Area"
    cursor = Qt.CrossCursor
    MIN_STEP_CANVAS = 0.5   # ignore outline samples closer than this (canvas units)
    MIN_OUTLINE_POINTS = 3  # fewer samples than this do not enclose an area

    def __init__(self):
        """Initialize the tool with a custom cursor from the icon."""
        icon_path = os.path.join(os.path.dirname(__file__), "icons", "select_area.svg")
        pixmap = QPixmap(icon_path)
        if not pixmap.isNull():
            # Hot spot is the arrow tip in the icon (12,9 in its 24x24 viewBox).
            self.cursor = QCursor(pixmap, hotX=12, hotY=9)
        else:
            self.cursor = Qt.CrossCursor
        self._outline = []  # canvas-space points of the outline being drawn
        self._init_selection_move()

    def mouse_press(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        if self._selection_move_press(canvas, event, cx, cy):
            return
        self._outline = [(cx, cy)]
        canvas.update()

    def mouse_move(self, canvas, event):
        if self._selection_move_drag(canvas, event):
            return
        if not self._outline:
            return
        point = canvas.screen_to_canvas(event.x(), event.y())
        last_x, last_y = self._outline[-1]
        if math.hypot(point[0] - last_x, point[1] - last_y) < self.MIN_STEP_CANVAS:
            return
        self._outline.append(point)
        canvas.update()

    def mouse_release(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        if self._selection_move_release(canvas):
            return
        self._finish_outline(canvas)

    def _finish_outline(self, canvas):
        """Select the stitch points inside the outline that was drawn."""
        outline, self._outline = self._outline, []
        canvas.set_selection_indices(self.indices_in_area(canvas.pattern, outline))
        canvas.update()

    def _on_selection_click(self, canvas, cx, cy):
        """The press near the selection never dragged, so it was just a click."""
        self._outline = [(cx, cy)]
        self._finish_outline(canvas)

    def on_deselect(self, canvas):
        """Abandon a half-drawn outline when another tool takes over."""
        self._selection_move_cancel(canvas)
        if not self._outline:
            return
        self._outline = []
        canvas.update()

    def indices_in_area(self, pattern, outline):
        """Return the indices of the stitch points inside *outline*."""
        if len(outline) < self.MIN_OUTLINE_POINTS:
            return []
        return [i for i, elem in enumerate(pattern.elements)
                if elem_has_coords(elem) and point_in_polygon(elem[1], elem[2], outline)]

    def paint_overlay(self, canvas, painter):
        """Draw the outline while the user is dragging."""
        if len(self._outline) < 2:
            return
        pen = QPen(QColor(0, 80, 200), 1)
        pen.setDashPattern([4, 4])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        polygon = QPolygon([
            QPoint(int(sx), int(sy))
            for sx, sy in (canvas.canvas_to_screen(x, y) for x, y in self._outline)
        ])
        painter.drawPolygon(polygon)


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
                canvas.drag_finished.emit()
                event.accept()
                return True
        return False

    def mouse_press(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        if canvas.snap_normal_to_grid:
            cx, cy = int(round(cx)), int(round(cy))
        if 0 <= cx <= canvas.pattern.CANVAS_WIDTH and 0 <= cy <= canvas.pattern.CANVAS_HEIGHT:
            # If points are selected, insert after the end of selection; otherwise append
            start, end = canvas.get_selection()
            if start is not None and end is not None:
                # Use end index for range selection
                insert_idx = end + 1
                canvas.pattern.add_point(cx, cy, index=insert_idx, snap=canvas.snap_normal_to_grid)
                canvas.set_selected_point(insert_idx)  # Select the newly added point
            else:
                # Fallback: use start of selection if only single point selected
                selected_idx = canvas.get_selected_point()
                if selected_idx is not None:
                    insert_idx = selected_idx + 1
                    canvas.pattern.add_point(cx, cy, index=insert_idx, snap=canvas.snap_normal_to_grid)
                    canvas.set_selected_point(insert_idx)  # Select the newly added point
                else:
                    # No selection: append to end
                    insert_idx = len(canvas.pattern.elements)
                    canvas.pattern.add_point(cx, cy, snap=canvas.snap_normal_to_grid)
                    canvas.set_selected_point(insert_idx)  # Select the newly added point
            canvas.update()
            canvas.notify_change()
            canvas.drag_finished.emit()

    def mouse_move(self, canvas, event):
        self._cursor_x, self._cursor_y = canvas.screen_to_canvas(event.x(), event.y())
        if canvas.snap_normal_to_grid:
            self._cursor_x = round(self._cursor_x)
            self._cursor_y = round(self._cursor_y)
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
        
        ref_coords = canvas.pattern.get_coords(ref_idx) if ref_idx is not None else None
        if ref_coords is not None:
            sx1, sy1 = canvas.canvas_to_screen(ref_coords[0], ref_coords[1])
            painter.drawLine(int(sx1), int(sy1), int(cursor_sx), int(cursor_sy))

            # Draw line to the next coord element (if one exists after ref_idx)
            next_coords = None
            for ni in range(ref_idx + 1, len(canvas.pattern.elements)):
                nc = canvas.pattern.get_coords(ni)
                if nc is not None:
                    next_coords = nc
                    break
            if next_coords is not None:
                sx2, sy2 = canvas.canvas_to_screen(next_coords[0], next_coords[1])
                painter.drawLine(int(cursor_sx), int(cursor_sy), int(sx2), int(sy2))
        else:
            # Fallback: draw from last coord element to cursor
            last_coords = None
            for elem in reversed(canvas.pattern.elements):
                if elem_has_coords(elem):
                    last_coords = (elem[1], elem[2])
                    break
            if last_coords is not None:
                sx1, sy1 = canvas.canvas_to_screen(last_coords[0], last_coords[1])
                painter.drawLine(int(sx1), int(sy1), int(cursor_sx), int(cursor_sy))
        
        # Draw preview point
        sx, sy = canvas.canvas_to_screen(cx, cy)
        preview_pen = QPen(QColor(0, 80, 200, 150), 1)  # Faint point outline
        painter.setPen(preview_pen)
        painter.setBrush(QColor(0, 80, 200, 50))  # Very faint filled point
        r = canvas._point_radius
        painter.drawEllipse(int(sx - r), int(sy - r), 2 * r, 2 * r)


class MovePointTool(PointDragMixin, BaseTool):
    """Click and drag to move existing stitch point(s)."""

    name = "Move Stitch Point"
    cursor = Qt.CrossCursor
    SNAP_RADIUS_PX = MOVE_SNAP_RADIUS_PX

    def __init__(self):
        self._init_point_drag()
        self._pending_empty_drag = False  # empty-space press waiting for travel
        self._press_screen_pos = None     # screen pos of that press
        self._press_canvas_pos = None     # canvas pos of that press

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

        n = len(canvas.pattern.elements)
        if n == 0:
            return False

        key = event.key()

        if key == Qt.Key_Backspace and not event.isAutoRepeat():
            # Only undo if the last command in undo stack is a MovePointCommand
            if canvas.pattern._undo_stack and isinstance(canvas.pattern._undo_stack[-1], MovePointCommand):
                canvas.pattern.undo()
                canvas.update()
                canvas.notify_change()
                canvas.drag_finished.emit()
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
        """Return element index of the nearest coord element within SNAP_RADIUS_PX, or None."""
        return find_nearest_coord_index(
            canvas.pattern, cx, cy, self.SNAP_RADIUS_PX / canvas.get_scale())

    def _clear_pending_empty_drag(self):
        """Forget an empty-space press that has not turned into a drag yet."""
        self._pending_empty_drag = False
        self._press_screen_pos = None
        self._press_canvas_pos = None

    def mouse_press(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        idx = self._find_nearest(canvas, cx, cy)
        if idx is None:
            # Empty canvas never deselects. A drag from here moves the current
            # selection; a plain click leaves it untouched.
            self._clear_pending_empty_drag()
            if canvas.get_selected_indices():
                self._pending_empty_drag = True
                self._press_screen_pos = (event.x(), event.y())
                self._press_canvas_pos = (cx, cy)
            return
        self._clear_pending_empty_drag()
        if not canvas.is_point_selected(idx):
            # Grabbing a point outside the selection moves just that point.
            canvas.set_selected_point(idx)
        self._begin_point_drag(canvas, idx)
        canvas.setCursor(Qt.CrossCursor)

    def mouse_move(self, canvas, event):
        if self._pending_empty_drag and self._press_screen_pos is not None:
            dx = event.x() - self._press_screen_pos[0]
            dy = event.y() - self._press_screen_pos[1]
            if dx * dx + dy * dy <= DRAG_START_PX ** 2:
                return
            press_canvas = self._press_canvas_pos
            self._clear_pending_empty_drag()
            if self._begin_cursor_point_drag(canvas, press_canvas):
                self._update_point_drag(canvas, event)
            return
        if self._is_dragging:
            self._update_point_drag(canvas, event)

    def mouse_release(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        self._clear_pending_empty_drag()
        if self._is_dragging:
            self._finish_point_drag(canvas)
            canvas.setCursor(self.cursor)

    def on_deselect(self, canvas):
        """Drop an uncommitted drag when another tool takes over."""
        self._cancel_point_drag(canvas)
        self._clear_pending_empty_drag()


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
                canvas.drag_finished.emit()
                event.accept()
                return True
        return False

    def _find_nearest(self, canvas, cx, cy):
        """Return element index of the nearest coord element within hit radius, or None."""
        return find_nearest_coord_index(canvas.pattern, cx, cy, self.HIT_RADIUS_CANVAS)

    def mouse_press(self, canvas, event):
        if event.button() != Qt.LeftButton:
            return
        cx, cy = canvas.screen_to_canvas(event.x(), event.y())
        idx = self._find_nearest(canvas, cx, cy)
        if idx is not None:
            canvas.pattern.delete_point(idx)
            canvas.update()
            canvas.notify_change()
            canvas.drag_finished.emit()
