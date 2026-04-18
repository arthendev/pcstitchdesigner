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

    def redo(self, pattern):
        pattern.points.insert(self.index, (self.x, self.y))

    def undo(self, pattern):
        pattern.points.pop(self.index)


class MovePointCommand(Command):
    def __init__(self, index, old_x, old_y, new_x, new_y):
        self.index = index
        self.old_x = old_x
        self.old_y = old_y
        self.new_x = new_x
        self.new_y = new_y

    def redo(self, pattern):
        pattern.points[self.index] = (self.new_x, self.new_y)

    def undo(self, pattern):
        pattern.points[self.index] = (self.old_x, self.old_y)


class DeletePointCommand(Command):
    def __init__(self, index, x, y):
        self.index = index
        self.x = x
        self.y = y

    def redo(self, pattern):
        pattern.points.pop(self.index)

    def undo(self, pattern):
        pattern.points.insert(self.index, (self.x, self.y))


class StitchPattern:
    """Ordered list of stitch points with undo/redo."""

    # Canvas size definitions: {name: (width, height)}
    CANVAS_SIZES = {
        "9mm": (198, 54),
        "MAXI": (998, 359),
        "small hoop": (480, 480),
        "big hoop": (689, 720),
    }

    def __init__(self):
        self.points = []  # list of (int, int)
        self.modified = False
        self._undo_stack = []
        self._redo_stack = []
        self.stitch_type = "9mm"  # "9mm" or "MAXI"
        self.canvas_size = "9mm"  # "9mm", "MAXI", "small hoop", or "big hoop"

    @property
    def CANVAS_WIDTH(self):
        """Get canvas width based on current canvas size."""
        return self.CANVAS_SIZES[self.canvas_size][0]

    @property
    def CANVAS_HEIGHT(self):
        """Get canvas height based on current canvas size."""
        return self.CANVAS_SIZES[self.canvas_size][1]

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

    def clear(self):
        self.points.clear()
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
