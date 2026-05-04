"""Stitch pattern data model with undo/redo support."""

from copy import deepcopy


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
        self.actual_index = None  # Track where point was actually inserted

    def redo(self, pattern):
        # Clamp index to valid range [0, len(points)] and track actual index
        self.actual_index = max(0, min(self.index, len(pattern.points)))
        pattern.points.insert(self.actual_index, (self.x, self.y))

    def undo(self, pattern):
        # Use actual_index if available, otherwise fall back to self.index
        idx = self.actual_index if self.actual_index is not None else self.index
        if 0 <= idx < len(pattern.points):
            pattern.points.pop(idx)


class MovePointCommand(Command):
    def __init__(self, index, old_x, old_y, new_x, new_y):
        self.index = index
        self.old_x = old_x
        self.old_y = old_y
        self.new_x = new_x
        self.new_y = new_y

    def redo(self, pattern):
        # Only modify if index is within valid range
        if 0 <= self.index < len(pattern.points):
            pattern.points[self.index] = (self.new_x, self.new_y)

    def undo(self, pattern):
        # Only modify if index is within valid range
        if 0 <= self.index < len(pattern.points):
            pattern.points[self.index] = (self.old_x, self.old_y)


class DeletePointCommand(Command):
    def __init__(self, index, x, y):
        self.index = index
        self.x = x
        self.y = y
        self.actual_index = None  # Track where point was actually deleted from

    def redo(self, pattern):
        # Only redo if index is within valid range, track actual deletion
        if 0 <= self.index < len(pattern.points):
            self.actual_index = self.index
            pattern.points.pop(self.index)

    def undo(self, pattern):
        # Use actual_index if available, otherwise fall back to self.index
        idx = self.actual_index if self.actual_index is not None else self.index
        idx = max(0, min(idx, len(pattern.points)))
        pattern.points.insert(idx, (self.x, self.y))


class DeleteRangeCommand(Command):
    """Deletes points at indices [start, end] (inclusive) as a single undo step."""

    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.saved_points = None

    def redo(self, pattern):
        self.saved_points = list(pattern.points[self.start:self.end + 1])
        del pattern.points[self.start:self.end + 1]

    def undo(self, pattern):
        if self.saved_points is not None:
            pattern.points[self.start:self.start] = self.saved_points


class ReplaceRangeCommand(Command):
    """Replaces points at indices [start, end] (inclusive) with new_points."""

    def __init__(self, start, end, new_points):
        self.start = start
        self.end = end
        self.new_points = list(new_points)
        self.old_points = None

    def redo(self, pattern):
        self.old_points = pattern.points[self.start:self.end + 1]
        pattern.points[self.start:self.end + 1] = self.new_points

    def undo(self, pattern):
        if self.old_points is not None:
            inserted_end = self.start + len(self.new_points)
            pattern.points[self.start:inserted_end] = self.old_points


class InvertRangeCommand(Command):
    """Inverts the order of points within a [start, end] range."""
    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.original_points = None

    def redo(self, pattern):
        # Store original points if not already stored
        if self.original_points is None:
            self.original_points = [pattern.points[i] for i in range(self.start, self.end + 1)]
        # Reverse the order
        reversed_points = list(reversed(self.original_points))
        for i, point in enumerate(reversed_points):
            pattern.points[self.start + i] = point

    def undo(self, pattern):
        # Restore original order
        if self.original_points:
            for i, point in enumerate(self.original_points):
                pattern.points[self.start + i] = point


class MirrorVerticalCommand(Command):
    """Mirrors points vertically around the center of a [start, end] range."""
    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.original_points = None

    def redo(self, pattern):
        # Store original points if not already stored
        if self.original_points is None:
            self.original_points = [pattern.points[i] for i in range(self.start, self.end + 1)]
        
        # Calculate center Y using min and max
        y_coords = [y for x, y in self.original_points]
        min_y = min(y_coords)
        max_y = max(y_coords)
        center_y = (min_y + max_y) / 2
        
        # Mirror vertically (flip Y coordinates around center)
        for i, (x, y) in enumerate(self.original_points):
            mirrored_y = int(round(2 * center_y - y))
            pattern.points[self.start + i] = (x, mirrored_y)

    def undo(self, pattern):
        # Restore original positions
        if self.original_points:
            for i, point in enumerate(self.original_points):
                pattern.points[self.start + i] = point


class MirrorHorizontalCommand(Command):
    """Mirrors points horizontally around the center of a [start, end] range."""
    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.original_points = None

    def redo(self, pattern):
        # Store original points if not already stored
        if self.original_points is None:
            self.original_points = [pattern.points[i] for i in range(self.start, self.end + 1)]
        
        # Calculate center X using min and max
        x_coords = [x for x, y in self.original_points]
        min_x = min(x_coords)
        max_x = max(x_coords)
        center_x = (min_x + max_x) / 2
        
        # Mirror horizontally (flip X coordinates around center)
        for i, (x, y) in enumerate(self.original_points):
            mirrored_x = int(round(2 * center_x - x))
            pattern.points[self.start + i] = (mirrored_x, y)

    def undo(self, pattern):
        # Restore original positions
        if self.original_points:
            for i, point in enumerate(self.original_points):
                pattern.points[self.start + i] = point


class StitchPattern:
    """Ordered list of stitch points with undo/redo."""

    # Canvas size definitions: {name: (width, height)}
    CANVAS_SIZES = {
        "9mm": (198, 54),
        "MAXI": (998, 359),
        "small hoop": (480, 480),
        "large hoop": (689, 720),
    }

    STITCH_RES_MM = 1/6  # 1 stitch = 0.166... mm

    def __init__(self):
        self.points = []  # list of (int, int)
        self.colors = []  # list of (r, g, b) tuples representing thread colors
        self.modified = False
        self._undo_stack = []
        self._redo_stack = []
        self.stitch_type = "9mm"  # "9mm" or "MAXI"

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

    def add_point(self, x, y, index=None):
        if index is None:
            index = len(self.points)
        x = max(0, min(self.CANVAS_WIDTH, int(round(x))))
        y = max(0, min(self.CANVAS_HEIGHT, int(round(y))))
        self._exec(AddPointCommand(index, x, y))

    def move_point(self, index, new_x, new_y):
        old_x, old_y = self.points[index]
        new_x = max(0, min(self.CANVAS_WIDTH, int(round(new_x))))
        new_y = max(0, min(self.CANVAS_HEIGHT, int(round(new_y))))
        if (old_x, old_y) == (new_x, new_y):
            return
        self._exec(MovePointCommand(index, old_x, old_y, new_x, new_y))

    def delete_point(self, index):
        x, y = self.points[index]
        self._exec(DeletePointCommand(index, x, y))

    def delete_range(self, start, end):
        """Delete points at indices [start, end] (inclusive) as a single undo step."""
        start = max(0, start)
        end = min(len(self.points) - 1, end)
        if start > end:
            return
        self._exec(DeleteRangeCommand(start, end))

    def invert_selected(self, start, end):
        """Invert the order of points within the selection."""
        if start is None or end is None or start > end:
            return
        self._exec(InvertRangeCommand(start, end))

    def mirror_vertical(self, start, end):
        """Mirror selected points vertically around the center of selection."""
        if start is None or end is None or start > end:
            return
        self._exec(MirrorVerticalCommand(start, end))

    def mirror_horizontal(self, start, end):
        """Mirror selected points horizontally around the center of selection."""
        if start is None or end is None or start > end:
            return
        self._exec(MirrorHorizontalCommand(start, end))

    def replace_range(self, start, end, new_points):
        """Replace points at indices [start, end] (inclusive) with new_points."""
        start = max(0, start)
        end = min(len(self.points) - 1, end)
        self._exec(ReplaceRangeCommand(start, end, new_points))

    def clear(self):
        self.points.clear()
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
        """Load points received from the machine, adjusting coordinates to fit the canvas.

        For MAXI patterns, shifts all Y values by a uniform offset so that
        y_min >= 0 and y_max <= CANVAS_HEIGHT.  The shift is the minimum
        required: if y_min < 0 the pattern is moved up; if y_max exceeds the
        canvas height it is moved down.

        Args:
            points (list[tuple[int, int]]): Raw (x, y) pairs decoded from the
                machine response.
            slot_type (str): '9mm' or 'MAXI'.
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
        self.points.clear()
        self.points.extend(points)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.modified = True

    def get_stitch_bounds(self):
        """Return stitch bounds as (min_x, min_y, max_x, max_y), or None if empty."""
        if not self.points:
            return None
        xs = [x for x, _ in self.points]
        ys = [y for _, y in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def get_stitch_size_mm(self):
        """Return stitch width/height in mm using preview stitch resolution."""
        bounds = self.get_stitch_bounds()
        if bounds is None:
            return 0.0, 0.0
        min_x, min_y, max_x, max_y = bounds
        width_units = max_x - min_x
        height_units = max_y - min_y
        return (
            width_units * self.STITCH_RES_MM,
            height_units * self.STITCH_RES_MM,
        )
