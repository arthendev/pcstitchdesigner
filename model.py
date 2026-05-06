"""Stitch pattern data model with undo/redo support."""

# ---------------------------------------------------------------------------
# Element type constants (values match file-format control bytes)
# ---------------------------------------------------------------------------
ELEM_STITCH = 0   # (ELEM_STITCH, x, y)  — normal stitch point
ELEM_AUTO   = 2   # (ELEM_AUTO, x, y)    — automatic stitch (hollow circle)
ELEM_COLOR  = 3   # (ELEM_COLOR, color_index) — color change
ELEM_TRIM   = 4   # (ELEM_TRIM, x, y)   — trim (line drawn to it, line broken after)


def elem_has_coords(e):
    """Return True when element *e* carries x,y coordinates."""
    return e[0] in (ELEM_STITCH, ELEM_AUTO)


class Command:
    """Base class for undoable commands."""
    def redo(self, pattern):
        raise NotImplementedError

    def undo(self, pattern):
        raise NotImplementedError


class AddPointCommand(Command):
    def __init__(self, index, x, y):
        self.index = index
        self.x = x
        self.y = y
        self.actual_index = None  # Track where element was actually inserted

    def redo(self, pattern):
        self.actual_index = max(0, min(self.index, len(pattern.elements)))
        pattern.elements.insert(self.actual_index, (ELEM_STITCH, self.x, self.y))

    def undo(self, pattern):
        idx = self.actual_index if self.actual_index is not None else self.index
        if 0 <= idx < len(pattern.elements):
            pattern.elements.pop(idx)


class MovePointCommand(Command):
    def __init__(self, index, old_x, old_y, new_x, new_y):
        self.index = index
        self.old_x = old_x
        self.old_y = old_y
        self.new_x = new_x
        self.new_y = new_y

    def redo(self, pattern):
        if 0 <= self.index < len(pattern.elements):
            e = pattern.elements[self.index]
            if elem_has_coords(e):
                pattern.elements[self.index] = (e[0], self.new_x, self.new_y)

    def undo(self, pattern):
        if 0 <= self.index < len(pattern.elements):
            e = pattern.elements[self.index]
            if elem_has_coords(e):
                pattern.elements[self.index] = (e[0], self.old_x, self.old_y)


class MoveManyPointsCommand(Command):
    """Moves multiple elements in one undoable step.

    Args:
        moves: list of (index, old_x, old_y, new_x, new_y)
    """
    def __init__(self, moves):
        self.moves = moves  # [(index, old_x, old_y, new_x, new_y), ...]

    def redo(self, pattern):
        for index, old_x, old_y, new_x, new_y in self.moves:
            if 0 <= index < len(pattern.elements):
                e = pattern.elements[index]
                if elem_has_coords(e):
                    pattern.elements[index] = (e[0], new_x, new_y)

    def undo(self, pattern):
        for index, old_x, old_y, new_x, new_y in self.moves:
            if 0 <= index < len(pattern.elements):
                e = pattern.elements[index]
                if elem_has_coords(e):
                    pattern.elements[index] = (e[0], old_x, old_y)


class DeletePointCommand(Command):
    def __init__(self, index, element):
        self.index = index
        self.element = element
        self.actual_index = None  # Track where element was actually deleted from

    def redo(self, pattern):
        if 0 <= self.index < len(pattern.elements):
            self.actual_index = self.index
            pattern.elements.pop(self.index)

    def undo(self, pattern):
        idx = self.actual_index if self.actual_index is not None else self.index
        idx = max(0, min(idx, len(pattern.elements)))
        pattern.elements.insert(idx, self.element)


class DeleteRangeCommand(Command):
    """Deletes elements at indices [start, end] (inclusive) as a single undo step."""

    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.saved_points = None  # kept as attribute name for compatibility

    def redo(self, pattern):
        self.saved_points = list(pattern.elements[self.start:self.end + 1])
        del pattern.elements[self.start:self.end + 1]

    def undo(self, pattern):
        if self.saved_points is not None:
            pattern.elements[self.start:self.start] = self.saved_points


class ReplaceRangeCommand(Command):
    """Replaces elements at indices [start, end] (inclusive) with new_elements."""

    def __init__(self, start, end, new_points):
        self.start = start
        self.end = end
        self.new_points = list(new_points)
        self.old_points = None

    def redo(self, pattern):
        self.old_points = pattern.elements[self.start:self.end + 1]
        pattern.elements[self.start:self.end + 1] = self.new_points

    def undo(self, pattern):
        if self.old_points is not None:
            inserted_end = self.start + len(self.new_points)
            pattern.elements[self.start:inserted_end] = self.old_points


class InvertRangeCommand(Command):
    """Inverts the order of elements within a [start, end] range."""
    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.original_points = None

    def redo(self, pattern):
        if self.original_points is None:
            self.original_points = [pattern.elements[i] for i in range(self.start, self.end + 1)]
        reversed_elems = list(reversed(self.original_points))
        for i, elem in enumerate(reversed_elems):
            pattern.elements[self.start + i] = elem

    def undo(self, pattern):
        if self.original_points:
            for i, elem in enumerate(self.original_points):
                pattern.elements[self.start + i] = elem


class MirrorVerticalCommand(Command):
    """Mirrors coord elements vertically around the center of a [start, end] range."""
    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.original_points = None

    def redo(self, pattern):
        if self.original_points is None:
            self.original_points = [pattern.elements[i] for i in range(self.start, self.end + 1)]

        coord_ys = [e[2] for e in self.original_points if elem_has_coords(e)]
        if not coord_ys:
            return
        center_y = (min(coord_ys) + max(coord_ys)) / 2

        for i, e in enumerate(self.original_points):
            if elem_has_coords(e):
                mirrored_y = int(round(2 * center_y - e[2]))
                pattern.elements[self.start + i] = (e[0], e[1], mirrored_y)
            else:
                pattern.elements[self.start + i] = e

    def undo(self, pattern):
        if self.original_points:
            for i, e in enumerate(self.original_points):
                pattern.elements[self.start + i] = e


class MirrorHorizontalCommand(Command):
    """Mirrors coord elements horizontally around the center of a [start, end] range."""
    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.original_points = None

    def redo(self, pattern):
        if self.original_points is None:
            self.original_points = [pattern.elements[i] for i in range(self.start, self.end + 1)]

        coord_xs = [e[1] for e in self.original_points if elem_has_coords(e)]
        if not coord_xs:
            return
        center_x = (min(coord_xs) + max(coord_xs)) / 2

        for i, e in enumerate(self.original_points):
            if elem_has_coords(e):
                mirrored_x = int(round(2 * center_x - e[1]))
                pattern.elements[self.start + i] = (e[0], mirrored_x, e[2])
            else:
                pattern.elements[self.start + i] = e

    def undo(self, pattern):
        if self.original_points:
            for i, e in enumerate(self.original_points):
                pattern.elements[self.start + i] = e


class StitchPattern:
    """Ordered list of pattern elements with undo/redo.

    Each element in ``elements`` is a tuple whose first value is an element
    type constant (ELEM_STITCH, ELEM_AUTO, ELEM_COLOR, ELEM_TRIM).
    """

    # Canvas size definitions: {name: (width, height)}
    CANVAS_SIZES = {
        "9mm": (198, 54),
        "MAXI": (998, 359),
        "small hoop": (480, 480),
        "large hoop": (720, 689),
    }

    STITCH_RES_MM = 1/6  # 1 stitch = 0.166... mm

    def __init__(self):
        self.elements = []  # list of element tuples
        self.colors = []    # list of (r, g, b) tuples representing thread colors
        self.modified = False
        self._undo_stack = []
        self._redo_stack = []
        self.stitch_type = "9mm"

    @property
    def has_palette(self):
        """True when the pattern has a defined color palette."""
        return len(self.colors) > 0

    def get_color_at(self, elem_idx):
        """Return the active palette color index at elements[elem_idx].

        Scans backwards through elements[0:elem_idx] counting ELEM_COLOR
        records.  Returns the last color index seen, defaulting to 0.
        Returns None when no palette is defined.
        """
        if not self.colors:
            return None
        color_idx = 0
        for i in range(min(elem_idx, len(self.elements))):
            e = self.elements[i]
            if e[0] == ELEM_COLOR:
                color_idx = e[1]
        return min(color_idx, len(self.colors) - 1)

    def get_point_color_index(self, i):
        """Backward-compatible alias for get_color_at."""
        return self.get_color_at(i)

    def get_coords(self, idx):
        """Return (x, y) for elements[idx], or None if the element has no coords."""
        if 0 <= idx < len(self.elements):
            e = self.elements[idx]
            if elem_has_coords(e):
                return (e[1], e[2])
        return None

    @property
    def CANVAS_WIDTH(self):
        """Get canvas width based on current canvas size."""
        return self.CANVAS_SIZES[self.stitch_type][0]

    @property
    def CANVAS_HEIGHT(self):
        """Get canvas height based on current canvas size."""
        return self.CANVAS_SIZES[self.stitch_type][1]

    @property
    def CANVAS_WIDTH_MM(self):
        """Get canvas width based on current canvas size."""
        return self.CANVAS_SIZES[self.stitch_type][0] * self.STITCH_RES_MM

    @property
    def CANVAS_HEIGHT_MM(self):
        """Get canvas height based on current canvas size."""
        return self.CANVAS_SIZES[self.stitch_type][1] * self.STITCH_RES_MM

    def _exec(self, cmd):
        cmd.redo(self)
        self._undo_stack.append(cmd)
        self._redo_stack.clear()
        self.modified = True

    # ── Element addition ──

    def add_point(self, x, y, index=None):
        """Add a normal stitch element. Alias for add_stitch."""
        return self.add_stitch(x, y, index=index)

    def add_stitch(self, x, y, index=None):
        """Append or insert a normal stitch element (ELEM_STITCH)."""
        if index is None:
            index = len(self.elements)
        x = max(0, min(self.CANVAS_WIDTH, int(round(x))))
        y = max(0, min(self.CANVAS_HEIGHT, int(round(y))))
        self._exec(AddPointCommand(index, x, y))

    def add_auto_stitch(self, x, y, index=None):
        """Append or insert an automatic stitch element (ELEM_AUTO)."""
        if index is None:
            index = len(self.elements)
        x = max(0, min(self.CANVAS_WIDTH, int(round(x))))
        y = max(0, min(self.CANVAS_HEIGHT, int(round(y))))
        # Reuse AddPointCommand but override element type after exec
        cmd = AddPointCommand(index, x, y)
        cmd.redo(self)
        # Fix the inserted element type to ELEM_AUTO
        self.elements[cmd.actual_index] = (ELEM_AUTO, x, y)
        self._undo_stack.append(cmd)
        self._redo_stack.clear()
        self.modified = True

    def add_color_change(self, color_index, index=None):
        """Insert a color-change element (ELEM_COLOR) at *index*."""
        if index is None:
            index = len(self.elements)
        index = max(0, min(index, len(self.elements)))
        self.elements.insert(index, (ELEM_COLOR, color_index))
        self._undo_stack.clear()   # simple insert — no undo support yet
        self._redo_stack.clear()
        self.modified = True

    def add_trim(self, index=None):
        """Insert a trim element (ELEM_TRIM) at *index*."""
        if index is None:
            index = len(self.elements)
        index = max(0, min(index, len(self.elements)))
        self.elements.insert(index, (ELEM_TRIM,))
        self._undo_stack.clear()   # simple insert — no undo support yet
        self._redo_stack.clear()
        self.modified = True

    # ── Element movement ──

    def move_point(self, index, new_x, new_y):
        e = self.elements[index]
        if not elem_has_coords(e):
            return
        old_x, old_y = e[1], e[2]
        new_x = max(0, min(self.CANVAS_WIDTH, int(round(new_x))))
        new_y = max(0, min(self.CANVAS_HEIGHT, int(round(new_y))))
        if (old_x, old_y) == (new_x, new_y):
            return
        self._exec(MovePointCommand(index, old_x, old_y, new_x, new_y))

    def move_points(self, indices, new_positions):
        """Move multiple coord elements as a single undoable step."""
        moves = []
        for idx, (new_x, new_y) in zip(indices, new_positions):
            e = self.elements[idx]
            if not elem_has_coords(e):
                continue
            old_x, old_y = e[1], e[2]
            new_x = max(0, min(self.CANVAS_WIDTH, int(round(new_x))))
            new_y = max(0, min(self.CANVAS_HEIGHT, int(round(new_y))))
            if (old_x, old_y) != (new_x, new_y):
                moves.append((idx, old_x, old_y, new_x, new_y))
        if moves:
            self._exec(MoveManyPointsCommand(moves))

    # ── Element deletion ──

    def delete_point(self, index):
        """Delete the element at *index*. Alias for delete_element."""
        return self.delete_element(index)

    def delete_element(self, index):
        """Delete the element at *index* (any type)."""
        elem = self.elements[index]
        self._exec(DeletePointCommand(index, elem))

    def delete_range(self, start, end):
        """Delete elements at indices [start, end] (inclusive) as a single undo step."""
        start = max(0, start)
        end = min(len(self.elements) - 1, end)
        if start > end:
            return
        self._exec(DeleteRangeCommand(start, end))

    def cut_range(self, start, end):
        """Delete elements at indices [start, end] (inclusive) and return them.

        Returns the list of removed elements, or [] when the range is invalid.
        """
        start = max(0, start)
        end = min(len(self.elements) - 1, end)
        if start > end:
            return []
        cmd = DeleteRangeCommand(start, end)
        self._exec(cmd)
        return list(cmd.saved_points)

    # ── Range operations ──

    def invert_selected(self, start, end):
        """Invert the order of elements within the selection."""
        if start is None or end is None or start > end:
            return
        self._exec(InvertRangeCommand(start, end))

    def mirror_vertical(self, start, end):
        """Mirror selected coord elements vertically around the center of selection."""
        if start is None or end is None or start > end:
            return
        self._exec(MirrorVerticalCommand(start, end))

    def mirror_horizontal(self, start, end):
        """Mirror selected coord elements horizontally around the center of selection."""
        if start is None or end is None or start > end:
            return
        self._exec(MirrorHorizontalCommand(start, end))

    def replace_range(self, start, end, new_elements):
        """Replace elements at indices [start, end] (inclusive) with new_elements."""
        start = max(0, start)
        end = min(len(self.elements) - 1, end)
        self._exec(ReplaceRangeCommand(start, end, new_elements))

    def clear(self):
        self.elements.clear()
        self.colors.clear()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.modified = False

    def can_undo(self):
        return len(self._undo_stack) > 0

    def can_redo(self):
        return len(self._redo_stack) > 0

    def undo(self):
        if not self._undo_stack:
            return
        cmd = self._undo_stack.pop()
        cmd.undo(self)
        self._redo_stack.append(cmd)
        self.modified = True

    def redo(self):
        if not self._redo_stack:
            return
        cmd = self._redo_stack.pop()
        cmd.redo(self)
        self._undo_stack.append(cmd)
        self.modified = True

    def set_machine_data(self, points, slot_type):
        """Load raw (x, y) pairs received from the machine.

        Converts them to ELEM_STITCH elements, adjusting MAXI Y coordinates
        to fit the canvas when needed.

        Args:
            points: list of (x, y) tuples from machine decode.
            slot_type: '9mm' or 'MAXI'.
        """
        self.stitch_type = slot_type
        if slot_type == "MAXI" and points:
            canvas_h = self.CANVAS_SIZES["MAXI"][1]
            y_min = min(y for _, y in points)
            y_max = max(y for _, y in points)
            offset = 0
            if y_min < 0:
                offset = -y_min
            elif y_max > canvas_h:
                offset = canvas_h - y_max
            if offset:
                points = [(x, y + offset) for x, y in points]
        self.elements.clear()
        for x, y in points:
            self.elements.append((ELEM_STITCH, x, y))
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.modified = True

    def get_stitch_bounds(self):
        """Return stitch bounds as (min_x, min_y, max_x, max_y), or None if empty."""
        coords = [(e[1], e[2]) for e in self.elements if elem_has_coords(e)]
        if not coords:
            return None
        xs = [x for x, _ in coords]
        ys = [y for _, y in coords]
        return min(xs), min(ys), max(xs), max(ys)

    def get_stitch_size_mm(self):
        """Return stitch width/height in mm using preview stitch resolution."""
        bounds = self.get_stitch_bounds()
        if bounds is None:
            return 0.0, 0.0
        min_x, min_y, max_x, max_y = bounds
        return (
            (max_x - min_x) * self.STITCH_RES_MM,
            (max_y - min_y) * self.STITCH_RES_MM,
        )
