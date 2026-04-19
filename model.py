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


class InvertSelectionCommand(Command):
    """Inverts the order of points within a selection."""
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
    """Mirrors selected points vertically around the center of selection."""
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
    """Mirrors selected points horizontally around the center of selection."""
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
        "big hoop": (689, 720),
    }

    STITCH_RES_MM = 1/3  # 1 stitch = 0.333... mm

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

    def invert_selection(self, start, end):
        """Invert the order of points within the selection."""
        if start is None or end is None or start > end:
            return
        self._exec(InvertSelectionCommand(start, end))

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
