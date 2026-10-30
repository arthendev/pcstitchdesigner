"""Stitch pattern data model with undo/redo support."""

import math

# ---------------------------------------------------------------------------
# Element type constants (values match file-format control bytes)
# ---------------------------------------------------------------------------
ELEM_STITCH = 0   # (ELEM_STITCH, x, y)  — normal stitch point
ELEM_AUTO   = 2   # (ELEM_AUTO, x, y)    — automatic stitch (hollow circle)
ELEM_COLOR  = 3   # (ELEM_COLOR, color_index) — color change
ELEM_SECTION = 4  # (ELEM_SECTION, x, y) — section marker (start/end)

# Default 16 thread colours, offered in the preferences as the VGA palette".
# The card and some embroidery formats name threads by number rather than
# colour, so a design that carries no colour table of its own falls back to
# default tables.
VGA_PALETTE = (
    (0, 0, 0),          # 1  black
    (220, 0, 0),        # 2  red
    (0, 150, 0),        # 3  green
    (0, 0, 180),        # 4  blue
    (230, 140, 0),      # 5  orange
    (150, 0, 180),      # 6  purple
    (0, 160, 160),      # 7  teal
    (210, 0, 140),      # 8  magenta
    (130, 90, 0),       # 9  brown
    (100, 100, 100),    # 10 grey
    (0, 120, 210),      # 11 azure
    (120, 200, 0),      # 12 lime
    (255, 150, 190),    # 13 pink
    (0, 200, 140),      # 14 mint
    (160, 120, 255),    # 15 violet
    (0, 70, 130),       # 16 navy
)

# A softer 16-thread table offered as "Pastel colors"
PASTEL_PALETTE = (
    (128, 128, 128),    # 1  grey
    (0, 128, 255),      # 2  azure
    (255, 85, 0),       # 3  orange
    (0, 240, 112),      # 4  green
    (255, 128, 0),      # 5  light orange
    (255, 0, 255),      # 6  magenta
    (0, 255, 255),      # 7  cyan
    (249, 219, 86),     # 8  soft yellow
    (149, 132, 237),    # 9  violet
    (192, 192, 192),    # 10 silver
    (205, 127, 50),     # 11 copper
    (255, 102, 153),    # 12 pink
    (192, 255, 192),    # 13 pale mint
    (255, 255, 153),    # 14 pale yellow
    (223, 222, 220),    # 15 warm white
    (204, 204, 255),    # 16 lavender
)

# Default palettes the user chooses between in the preferences. The key is what
# the config file stores, so renaming one breaks existing settings.
PALETTES = {
    "vga": VGA_PALETTE,
    "pastel": PASTEL_PALETTE,
}

# Palette used when the stored preference is missing or names no known palette.
DEFAULT_PALETTE_NAME = "vga"

# The machine's own table, under the name the rest of the code knows it by.
# Card decoding and encoding mean these 16 threads, not whichever palette the
# user picked as their working default.
DEFAULT_PALETTE = VGA_PALETTE


def elem_has_coords(e):
    """Return True when element *e* carries x,y coordinates."""
    return e[0] in (ELEM_STITCH, ELEM_AUTO, ELEM_SECTION)


def is_embroidery_type(stitch_type):
    """Return True when *stitch_type* is an embroidery hoop preset."""
    return stitch_type in ("small hoop", "large hoop")


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
        self._original_element = None

    def redo(self, pattern):
        if 0 <= self.index < len(pattern.elements):
            if self._original_element is None:
                self._original_element = pattern.elements[self.index]
            e = pattern.elements[self.index]
            if elem_has_coords(e):
                pattern.elements[self.index] = (e[0], self.new_x, self.new_y)

    def undo(self, pattern):
        if self._original_element is not None and 0 <= self.index < len(pattern.elements):
            pattern.elements[self.index] = self._original_element


class MoveManyPointsCommand(Command):
    """Moves multiple elements in one undoable step.

    Args:
        moves: list of (index, old_x, old_y, new_x, new_y)
    """
    def __init__(self, moves):
        self.moves = moves  # [(index, old_x, old_y, new_x, new_y), ...]
        self._affected_elements = None

    def redo(self, pattern):
        if self._affected_elements is None:
            self._affected_elements = {
                idx: pattern.elements[idx]
                for idx, old_x, old_y, new_x, new_y in self.moves
                if 0 <= idx < len(pattern.elements)
            }
        for index, old_x, old_y, new_x, new_y in self.moves:
            if 0 <= index < len(pattern.elements):
                e = pattern.elements[index]
                if elem_has_coords(e):
                    pattern.elements[index] = (e[0], new_x, new_y)

    def undo(self, pattern):
        if self._affected_elements is not None:
            for idx, element in self._affected_elements.items():
                if 0 <= idx < len(pattern.elements):
                    pattern.elements[idx] = element


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


def deletion_targets(elements, indices):
    """Indices a deletion of *indices* really removes.

    Colour changes are anchors, not stitches: an ``ELEM_COLOR`` record is kept
    even when it lies inside the selection, because it carries the thread colour
    of everything that follows it. When the surviving records end up in a row,
    all but the last one of that run are dropped — same rule as
    :meth:`StitchPattern.get_color_at`, which lets the last colour change win.

    A deletion that leaves no stitch behind empties the pattern, so in that case
    the colour records go as well — an anchor has nothing left to colour.

    Args:
        elements: the pattern's base element list.
        indices: element indices the caller wants to delete.

    Returns:
        Sorted list of the indices that are actually removed.
    """
    doomed = {i for i in indices
              if 0 <= i < len(elements) and elements[i][0] != ELEM_COLOR}
    previous_color = None  # colour record kept so far in the current run
    for i, element in enumerate(elements):
        if element[0] == ELEM_COLOR:
            if previous_color is not None:
                doomed.add(previous_color)
            previous_color = i
        elif i not in doomed:
            # A surviving stitch ends the run of colour records.
            previous_color = None
    if not doomed:
        return []
    if not any(elem_has_coords(element) and i not in doomed
               for i, element in enumerate(elements)):
        # Nothing would be left to stitch: drop the pattern entirely.
        return list(range(len(elements)))
    return sorted(doomed)


class DeleteIndicesCommand(Command):
    """Deletes the elements at the given indices as a single undo step.

    Used for selections that are not a contiguous range (area selection).
    Colour changes survive the deletion; see :func:`deletion_targets`.
    """

    def __init__(self, indices):
        self.indices = sorted({int(i) for i in indices})
        self.removed = None  # [(index, element), ...] in ascending index order

    def redo(self, pattern):
        targets = deletion_targets(pattern.elements, self.indices)
        self.removed = [(i, pattern.elements[i]) for i in targets]
        # Delete from the end so the earlier indices stay valid.
        for index, _element in reversed(self.removed):
            del pattern.elements[index]

    def undo(self, pattern):
        if self.removed is None:
            return
        for index, element in self.removed:
            pattern.elements.insert(index, element)


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
    """Mirrors the coord elements at the given indices around their vertical centre."""
    def __init__(self, indices, snap=True):
        self.indices = list(indices)
        self.snap = snap
        self.original_points = None  # [(index, element), ...]

    def redo(self, pattern):
        if self.original_points is None:
            self.original_points = [(i, pattern.elements[i]) for i in self.indices
                                    if 0 <= i < len(pattern.elements)]

        coord_ys = [e[2] for _i, e in self.original_points if elem_has_coords(e)]
        if not coord_ys:
            return
        center_y = (min(coord_ys) + max(coord_ys)) / 2

        for index, e in self.original_points:
            if elem_has_coords(e):
                mirrored_y = 2 * center_y - e[2]
                if self.snap:
                    mirrored_y = int(round(mirrored_y))
                pattern.elements[index] = (e[0], e[1], mirrored_y)

    def undo(self, pattern):
        if self.original_points:
            for index, e in self.original_points:
                pattern.elements[index] = e


class MirrorHorizontalCommand(Command):
    """Mirrors the coord elements at the given indices around their horizontal centre."""
    def __init__(self, indices, snap=True):
        self.indices = list(indices)
        self.snap = snap
        self.original_points = None  # [(index, element), ...]

    def redo(self, pattern):
        if self.original_points is None:
            self.original_points = [(i, pattern.elements[i]) for i in self.indices
                                    if 0 <= i < len(pattern.elements)]

        coord_xs = [e[1] for _i, e in self.original_points if elem_has_coords(e)]
        if not coord_xs:
            return
        center_x = (min(coord_xs) + max(coord_xs)) / 2

        for index, e in self.original_points:
            if elem_has_coords(e):
                mirrored_x = 2 * center_x - e[1]
                if self.snap:
                    mirrored_x = int(round(mirrored_x))
                pattern.elements[index] = (e[0], mirrored_x, e[2])

    def undo(self, pattern):
        if self.original_points:
            for index, e in self.original_points:
                pattern.elements[index] = e


class ConvertAutoStitchesCommand(Command):
    """Promotes all ELEM_AUTO elements from the display layer into base elements."""

    def __init__(self):
        self._old_elements = None
        self._old_display = None
        self._old_map = None

    def redo(self, pattern):
        self._old_elements = list(pattern.elements)
        self._old_display = list(pattern._display_elements)
        self._old_map = list(pattern._display_base_map)
        # Flatten display into new base elements, rounding auto-stitch coords to grid
        new_elems = []
        for e in pattern._display_elements:
            if e[0] == ELEM_AUTO:
                new_elems.append((ELEM_STITCH, round(e[1]), round(e[2])))
            else:
                new_elems.append(e)
        pattern.elements = new_elems
        pattern._rebuild_display_no_auto()

    def undo(self, pattern):
        if self._old_elements is not None:
            pattern.elements = self._old_elements
            pattern._display_elements = self._old_display
            pattern._display_base_map = self._old_map


class RemoveSectionMarkersCommand(Command):
    """Turns every ELEM_SECTION marker into a normal stitch.

    Keeps the old records so undo can put the markers back where they were; the
    element count and all indices stay unchanged.
    """

    def __init__(self):
        self.changed = None  # [(index, (ELEM_SECTION, x, y)), ...]

    def redo(self, pattern):
        self.changed = [(i, e) for i, e in enumerate(pattern.elements)
                        if e[0] == ELEM_SECTION]
        for index, element in self.changed:
            pattern.elements[index] = (ELEM_STITCH, element[1], element[2])

    def undo(self, pattern):
        if self.changed is None:
            return
        for index, element in self.changed:
            pattern.elements[index] = element


class SetSectionMarkersCommand(Command):
    """Changes coordinate elements between stitch (ELEM_STITCH) and section marker.

    Because only the element kind changes, the element count and every index
    stay the same; the old records are kept so undo can restore them.

    Args:
        edits: ``[(index, element), ...]`` with the new element tuples, built by
            the caller from the existing coordinates.
    """

    def __init__(self, edits):
        self.edits = list(edits)
        self._old_elements = None

    def redo(self, pattern):
        if self._old_elements is None:
            self._old_elements = {
                index: pattern.elements[index]
                for index, _element in self.edits
                if 0 <= index < len(pattern.elements)
            }
        for index, element in self.edits:
            if 0 <= index < len(pattern.elements):
                pattern.elements[index] = element

    def undo(self, pattern):
        if self._old_elements is None:
            return
        for index, element in self._old_elements.items():
            if 0 <= index < len(pattern.elements):
                pattern.elements[index] = element


class ReplaceElementsCommand(Command):
    """Replaces the whole base element list in one atomic step.

    An editor that inserts or removes records shifts every following index, so
    a chain of index-based commands would not undo as one coherent action.
    Keeping both lists lets such an edit be a single undo entry.

    Args:
        new_elements: The complete base element list the pattern should hold.
    """

    def __init__(self, new_elements):
        self.new_elements = list(new_elements)
        self._old_elements = None

    def redo(self, pattern):
        if self._old_elements is None:
            self._old_elements = list(pattern.elements)
        pattern.elements = list(self.new_elements)

    def undo(self, pattern):
        if self._old_elements is not None:
            pattern.elements = list(self._old_elements)


class SetPaletteColorCommand(Command):
    """Replaces one entry of the thread palette, keeping the old RGB for undo.

    Only the palette entry changes — every ELEM_COLOR that pointed at *index*
    keeps pointing at it, so all stitches of that color change together.
    """

    def __init__(self, index, old_rgb, new_rgb):
        self.index = index
        self.old_rgb = tuple(old_rgb)
        self.new_rgb = tuple(new_rgb)

    def redo(self, pattern):
        if 0 <= self.index < len(pattern.colors):
            pattern.colors[self.index] = self.new_rgb

    def undo(self, pattern):
        if 0 <= self.index < len(pattern.colors):
            pattern.colors[self.index] = self.old_rgb


class SetPaletteCommand(Command):
    """Replaces the whole thread palette, keeping the old colors for undo.

    Used when a file format requires a fixed palette size, so missing entries
    have to be added or surplus ones dropped before saving.  ELEM_COLOR records
    are not touched: every index they use stays valid in the new palette.
    """

    def __init__(self, new_colors):
        self.new_colors = [tuple(c) for c in new_colors]
        self.old_colors = None

    def redo(self, pattern):
        self.old_colors = list(pattern.colors)
        pattern.colors = list(self.new_colors)

    def undo(self, pattern):
        if self.old_colors is not None:
            pattern.colors = self.old_colors


class SetPaletteAndElementsCommand(Command):
    """Replaces palette and element list as one undo step.

    Removing a palette entry renumbers the ELEM_COLOR records after it, so the
    palette and the records that reference it must be restored together.

    Args:
        new_colors: complete palette as ``[(r, g, b), ...]``.
        new_elements: complete base element list.
    """

    def __init__(self, new_colors, new_elements):
        self.new_colors = [tuple(c) for c in new_colors]
        self.new_elements = list(new_elements)
        self.old_colors = None
        self.old_elements = None

    def redo(self, pattern):
        if self.old_colors is None:
            self.old_colors = list(pattern.colors)
            self.old_elements = list(pattern.elements)
        pattern.colors = list(self.new_colors)
        pattern.elements = list(self.new_elements)

    def undo(self, pattern):
        if self.old_colors is not None:
            pattern.colors = list(self.old_colors)
            pattern.elements = list(self.old_elements)


class StitchPattern:
    """Ordered list of pattern elements with undo/redo.

    Each element in ``elements`` is a tuple whose first value is an element
    type constant (ELEM_STITCH, ELEM_AUTO, ELEM_COLOR, ELEM_SECTION).
    """

    # Canvas size definitions: {name: (width, height)}
    CANVAS_SIZES = {
        "9mm": (198, 54),
        "MAXI": (999, 360),
        "small hoop": (480, 480),
        "large hoop": (720, 690),
    }

    STITCH_RES_MM = 1/6  # 1 stitch = 0.166... mm
    EMBROIDERY_RES_MM = 51/512  # 1 stitch = 0.099609375 mm (approx. 1/10 mm)

    def __init__(self):
        self.elements = []  # list of base element tuples (never contains ELEM_AUTO)
        self._display_elements = []  # derived display list (includes ELEM_AUTO)
        self._display_base_map = []  # parallel to _display_elements: base index or None for ELEM_AUTO
        self.colors = []    # list of (r, g, b) tuples representing thread colors
        self.modified = False
        self._undo_stack = []
        self._redo_stack = []
        self.stitch_type = "9mm"

    @property
    def has_palette(self):
        """True when the pattern has a defined color palette."""
        return len(self.colors) > 0

    @property
    def display_elements(self):
        """Display element list including ELEM_AUTO stitches."""
        return self._display_elements

    @property
    def display_base_map(self):
        """Parallel list to display_elements: base index (int) or None for ELEM_AUTO."""
        return self._display_base_map

    @property
    def has_auto_stitches(self):
        """True when the display layer currently contains auto-stitches."""
        return any(e[0] == ELEM_AUTO for e in self._display_elements)

    def _rebuild_display_no_auto(self):
        """Reset display to a copy of base elements with an identity index map."""
        self._display_elements = list(self.elements)
        self._display_base_map = list(range(len(self.elements)))

    def _load_elements(self, all_elems):
        """Called after loading a file. Splits ELEM_AUTO out of the flat list
        into the display layer; non-auto elements become the base."""
        self.elements = [e for e in all_elems if e[0] != ELEM_AUTO]
        self._display_elements = list(all_elems)
        base_idx = 0
        self._display_base_map = []
        for e in all_elems:
            if e[0] != ELEM_AUTO:
                self._display_base_map.append(base_idx)
                base_idx += 1
            else:
                self._display_base_map.append(None)
        self.modified = False

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

    def set_palette_color(self, index, rgb):
        """Change one thread-color palette entry, undoably.

        All stitches of that color keep referencing the same palette index, so
        they all change together.

        Args:
            index: zero-based palette position.
            rgb: new ``(r, g, b)`` tuple with values 0–255.

        Raises:
            IndexError: when *index* is outside the palette.
        """
        if not 0 <= index < len(self.colors):
            raise IndexError(
                f"Palette index {index} out of range "
                f"(0..{len(self.colors) - 1})"
            )
        self._exec(SetPaletteColorCommand(index, self.colors[index], rgb))

    def set_palette(self, colors):
        """Replace the whole thread palette, undoably.

        Args:
            colors: complete palette as ``[(r, g, b), ...]``.
        """
        self._exec(SetPaletteCommand(colors))

    def set_palette_and_elements(self, colors, elements):
        """Replace palette and element list as one undo step.

        Used by the palette editor: removing an entry renumbers the ELEM_COLOR
        records after it, so both lists have to change together.

        Args:
            colors: complete palette as ``[(r, g, b), ...]``.
            elements: complete base element list (no ELEM_AUTO).

        Returns:
            bool: True when the pattern changed; False for an identical state.
        """
        new_colors = [tuple(c) for c in colors]
        new_elements = list(elements)
        if new_colors == self.colors and new_elements == self.elements:
            return False
        self._exec(SetPaletteAndElementsCommand(new_colors, new_elements))
        return True

    def ensure_palette(self, colors=None):
        """Give the pattern a thread palette when it has none yet.

        Embroidery designs are always shown in thread colors, so a design from
        an empty document — or from a file or machine that carried no color
        table — still needs something to display. Not marked as a change: like
        switching the stitch type, this only supplies a default for a value the
        pattern did not have.

        Args:
            colors: fallback palette as ``[(r, g, b), ...]``; defaults to
                :data:`DEFAULT_PALETTE`. The application passes the palette
                chosen in the preferences instead, so the default only applies
                to callers that have no settings access.

        Returns:
            bool: True when the fallback palette was applied.
        """
        if self.colors:
            return False
        source = DEFAULT_PALETTE if colors is None else colors
        self.colors = [tuple(c) for c in source]
        return True

    def clear_palette(self):
        """Drop the thread palette; 9 mm and MAXI patterns carry none.

        Returns:
            bool: True when a palette was removed.
        """
        if not self.colors:
            return False
        self.colors = []
        return True

    def clear_color_changes(self):
        """Remove every ELEM_COLOR record; 9 mm and MAXI patterns carry none.

        A colour change only names a slot in the thread palette, so it becomes
        meaningless as soon as a design stops being embroidery. The sewing
        formats have no palette and their writers skip colour records, so the
        records are dropped from the model instead of lingering there. This
        shifts element indices, hence the display layer is rebuilt.

        Returns:
            int: How many colour-change records were removed.
        """
        kept = [e for e in self.elements if e[0] != ELEM_COLOR]
        removed = len(self.elements) - len(kept)
        if not removed:
            return 0
        self.elements = kept
        self._rebuild_display_no_auto()
        return removed

    def rounded_elements(self):
        """Yield elements with x/y coordinates rounded and converted to int on demand."""
        for e in self.elements:
            if elem_has_coords(e):
                yield (e[0], int(round(e[1])), int(round(e[2])))
            else:
                yield e

    def rounded_display_elements(self):
        """Like rounded_elements but over the display layer (includes ELEM_AUTO)."""
        for e in self._display_elements:
            if elem_has_coords(e):
                yield (e[0], int(round(e[1])), int(round(e[2])))
            else:
                yield e

    def get_coords(self, idx):
        """Return (x, y) for elements[idx], or None if the element has no coords."""
        if 0 <= idx < len(self.elements):
            e = self.elements[idx]
            if elem_has_coords(e):
                return (e[1], e[2])
        return None

    def clear_auto_stitches(self):
        """Remove all ELEM_AUTO from the display layer (does not modify base elements)."""
        self._rebuild_display_no_auto()

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
        self._rebuild_display_no_auto()

    # ── Element addition ──

    def add_point(self, x, y, index=None, snap=True):
        """Add a normal stitch element. Alias for add_stitch."""
        return self.add_stitch(x, y, index=index, snap=snap)

    def add_stitch(self, x, y, index=None, snap=True):
        """Append or insert a normal stitch element (ELEM_STITCH)."""
        if index is None:
            index = len(self.elements)
        if snap:
            x = max(0, min(self.CANVAS_WIDTH, int(round(x))))
            y = max(0, min(self.CANVAS_HEIGHT, int(round(y))))
        else:
            x = max(0.0, min(float(self.CANVAS_WIDTH), float(x)))
            y = max(0.0, min(float(self.CANVAS_HEIGHT), float(y)))
        self._exec(AddPointCommand(index, x, y))

    def add_auto_stitch(self, x, y, index=None):
        """No-op: auto-stitches are managed exclusively via recalculate_auto_stitches."""
        pass  # kept for API compatibility

    def add_color_change(self, color_index, index=None):
        """Insert a color-change element (ELEM_COLOR) at *index*."""
        if index is None:
            index = len(self.elements)
        index = max(0, min(index, len(self.elements)))
        self.elements.insert(index, (ELEM_COLOR, color_index))
        self._undo_stack.clear()   # simple insert — no undo support yet
        self._redo_stack.clear()
        self.modified = True
        self._rebuild_display_no_auto()

    def add_section_marker(self, index=None):
        """Insert a section marker (ELEM_SECTION) at *index*."""
        if index is None:
            index = len(self.elements)
        index = max(0, min(index, len(self.elements)))
        self.elements.insert(index, (ELEM_SECTION,))
        self._undo_stack.clear()   # simple insert — no undo support yet
        self._redo_stack.clear()
        self.modified = True
        self._rebuild_display_no_auto()

    def remove_section_markers(self):
        """Turn every section marker into a normal stitch, undoably.

        Used by Edit → Sections → Remove all section markers, e.g. to let
        Select Section fall back to the colour changes of a design whose section
        markers are no longer wanted.

        Returns:
            int: number of markers converted; 0 (and no undo step) when the
            pattern carries none.
        """
        count = sum(1 for e in self.elements if e[0] == ELEM_SECTION)
        if count == 0:
            return 0
        self._exec(RemoveSectionMarkersCommand())
        return count

    def set_section_markers(self, edits):
        """Change individual stitch points into section markers or back, undoably.

        Used by Edit → Sections → Edit section markers, where the user edits a
        working copy and applies the result in one step.

        Args:
            edits: ``[(index, element), ...]`` where *element* is the new
                ``(ELEM_SECTION, x, y)`` or ``(ELEM_STITCH, x, y)`` record for
                the element at *index*.  Coordinates are the caller's
                responsibility, so fractional hoop positions survive.

        Returns:
            int: number of elements changed; 0 (and no undo step) for an empty
            edit list.
        """
        if not edits:
            return 0
        self._exec(SetSectionMarkersCommand(edits))
        return len(edits)

    def replace_elements(self, elements):
        """Replace the whole base element list as one undo step.

        Used by record editors that insert or remove elements (colour-change
        markers), where per-index commands would shift and stop matching.

        Args:
            elements: Complete list of base elements; ``ELEM_AUTO`` must not be
                included, because the display layer is derived from it.

        Returns:
            int: number of elements added (positive) or removed (negative);
            0 (and no undo step) when the list is unchanged.
        """
        new_elements = list(elements)
        if new_elements == self.elements:
            return 0
        delta = len(new_elements) - len(self.elements)
        self._exec(ReplaceElementsCommand(new_elements))
        return delta

    # ── Element movement ──

    def move_point(self, index, new_x, new_y, snap=True):
        e = self.elements[index]
        if not elem_has_coords(e):
            return
        old_x, old_y = e[1], e[2]
        if snap:
            new_x = max(0, min(self.CANVAS_WIDTH, int(round(new_x))))
            new_y = max(0, min(self.CANVAS_HEIGHT, int(round(new_y))))
        else:
            new_x = max(0.0, min(float(self.CANVAS_WIDTH), float(new_x)))
            new_y = max(0.0, min(float(self.CANVAS_HEIGHT), float(new_y)))
        if (old_x, old_y) == (new_x, new_y):
            return
        self._exec(MovePointCommand(index, old_x, old_y, new_x, new_y))

    def move_points(self, indices, new_positions, snap=True):
        """Move multiple coord elements as a single undoable step."""
        moves = []
        for idx, (new_x, new_y) in zip(indices, new_positions):
            e = self.elements[idx]
            if not elem_has_coords(e):
                continue
            old_x, old_y = e[1], e[2]
            if snap:
                new_x = max(0, min(self.CANVAS_WIDTH, int(round(new_x))))
                new_y = max(0, min(self.CANVAS_HEIGHT, int(round(new_y))))
            else:
                new_x = max(0.0, min(float(self.CANVAS_WIDTH), float(new_x)))
                new_y = max(0.0, min(float(self.CANVAS_HEIGHT), float(new_y)))
            if (old_x, old_y) != (new_x, new_y):
                moves.append((idx, old_x, old_y, new_x, new_y))
        if moves:
            self._exec(MoveManyPointsCommand(moves))

    # ── Centring ──

    def center_offsets(self, stitch_type=None):
        """Return the shift that centres the stitches in a canvas.

        Args:
            stitch_type: canvas preset to centre in; ``None`` uses the
                pattern's own type. The hoop switch passes the hoop it is about
                to switch to, so the design is centred there before the type
                changes.

        Returns:
            tuple[int, int] or None: the ``(dx, dy)`` to apply, or None when
            the pattern has no coordinate-bearing element.
        """
        bounds = self.get_stitch_bounds()
        if bounds is None:
            return None
        min_x, min_y, max_x, max_y = bounds
        canvas_w, canvas_h = self.CANVAS_SIZES[
            self.stitch_type if stitch_type is None else stitch_type]
        return (round((canvas_w - (max_x - min_x)) / 2 - min_x),
                round((canvas_h - (max_y - min_y)) / 2 - min_y))

    def center_design(self, stitch_type=None, undoable=True):
        """Move the whole design to the centre of a canvas.

        Args:
            stitch_type: canvas preset to centre in; see
                :meth:`center_offsets`.
            undoable: Push the shift as one undo step. The hoop switch passes
                False, because undoing the shift alone would put the stitches
                back outside the hoop it has already switched to.

        Returns:
            tuple[int, int] or None: the applied ``(dx, dy)``, or None when the
            pattern has no coordinate-bearing element.
        """
        offsets = self.center_offsets(stitch_type)
        if offsets is None:
            return None
        dx, dy = offsets
        if dx == 0 and dy == 0:
            return offsets
        moves = [(i, e[1], e[2], e[1] + dx, e[2] + dy)
                 for i, e in enumerate(self.elements) if elem_has_coords(e)]
        command = MoveManyPointsCommand(moves)
        if undoable:
            self._exec(command)
        else:
            command.redo(self)
            self._rebuild_display_no_auto()
        return offsets

    # ── Element deletion ──

    def delete_point(self, index):
        """Delete the element at *index*. Alias for delete_element."""
        return self.delete_element(index)

    def delete_element(self, index):
        """Delete the element at *index* (any type)."""
        elem = self.elements[index]
        self._exec(DeletePointCommand(index, elem))

    def delete_range(self, start, end):
        """Delete elements at indices [start, end] (inclusive) as a single undo step.

        Colour changes inside the range are kept; see :meth:`delete_indices`.
        """
        start = max(0, start)
        end = min(len(self.elements) - 1, end)
        if start > end:
            return
        self.delete_indices(range(start, end + 1))

    def cut_range(self, start, end):
        """Delete elements at indices [start, end] (inclusive) and return them.

        Returns the list of removed elements, or [] when the range is invalid.
        Colour changes inside the range are kept, except for redundant records of
        a colour run; see :meth:`cut_indices`.
        """
        start = max(0, start)
        end = min(len(self.elements) - 1, end)
        if start > end:
            return []
        return self.cut_indices(range(start, end + 1))

    def delete_indices(self, indices):
        """Delete the elements at *indices* (any order) as a single undo step.

        Colour changes inside *indices* are kept, a run of colour records left
        behind by the deletion is collapsed to its last record, and a deletion
        that would empty the pattern removes the colour records too.
        """
        indices = [i for i in indices if 0 <= i < len(self.elements)]
        if not indices or not deletion_targets(self.elements, indices):
            return
        self._exec(DeleteIndicesCommand(indices))

    def cut_indices(self, indices):
        """Delete the elements at *indices* and return them.

        Returns the elements that were really removed in index order, or [] when
        nothing matched. Colour changes stay in the pattern, so they are only
        returned when they were dropped as a redundant record of a colour run.
        """
        indices = [i for i in indices if 0 <= i < len(self.elements)]
        if not indices or not deletion_targets(self.elements, indices):
            return []
        cmd = DeleteIndicesCommand(indices)
        self._exec(cmd)
        return [element for _index, element in cmd.removed]

    # ── Range operations ──

    def invert_selected(self, start, end):
        """Invert the order of elements within the selection."""
        if start is None or end is None or start > end:
            return
        self._exec(InvertRangeCommand(start, end))

    def mirror_vertical(self, start, end, snap=True):
        """Mirror selected coord elements vertically around the center of selection."""
        if start is None or end is None or start > end:
            return
        self.mirror_vertical_indices(range(start, end + 1), snap=snap)

    def mirror_vertical_indices(self, indices, snap=True):
        """Mirror the coord elements at *indices* vertically around their centre."""
        indices = [i for i in indices if 0 <= i < len(self.elements)]
        if indices:
            self._exec(MirrorVerticalCommand(indices, snap=snap))

    def mirror_horizontal(self, start, end, snap=True):
        """Mirror selected coord elements horizontally around the center of selection."""
        if start is None or end is None or start > end:
            return
        self.mirror_horizontal_indices(range(start, end + 1), snap=snap)

    def mirror_horizontal_indices(self, indices, snap=True):
        """Mirror the coord elements at *indices* horizontally around their centre."""
        indices = [i for i in indices if 0 <= i < len(self.elements)]
        if indices:
            self._exec(MirrorHorizontalCommand(indices, snap=snap))

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
        self._rebuild_display_no_auto()

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
        self._rebuild_display_no_auto()

    def redo(self):
        if not self._redo_stack:
            return
        cmd = self._redo_stack.pop()
        cmd.redo(self)
        self._undo_stack.append(cmd)
        self.modified = True
        self._rebuild_display_no_auto()

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
        self._rebuild_display_no_auto()

    def set_machine_elements(self, elements, colors, slot_type):
        """Replace the pattern with pre-built elements received from a machine.

        Embroidery card reads carry thread colours and colour-change positions,
        which a plain point list would lose; this keeps them.

        Args:
            elements: list of ELEM_STITCH / ELEM_COLOR tuples.
            colors: palette as [(r, g, b), ...]; may be empty.
            slot_type: 'small hoop' or 'large hoop'.
        """
        self.stitch_type = slot_type
        self.colors = list(colors)
        self._load_elements(elements)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.modified = True

    def convert_auto_to_normal(self):
        """Promote all ELEM_AUTO elements from the display layer to base elements (undoable)."""
        if not self.has_auto_stitches:
            return
        self._exec(ConvertAutoStitchesCommand())

    def recalculate_auto_stitches(self, max_length_mm, align_to_grid=False, max_dx_mm=None):
        """Rebuild the display layer by inserting ELEM_AUTO elements wherever the gap
        between consecutive base coord elements exceeds *max_length_mm* or, when
        *max_dx_mm* is given, wherever the horizontal (x) distance exceeds *max_dx_mm*.
        Base elements (``self.elements``) and the undo stack are never modified.
        """
        new_display = []
        new_map = []
        prev_coord = None  # (x, y) of previous coord-bearing element
        for base_idx, elem in enumerate(self.elements):
            if elem_has_coords(elem):
                if prev_coord is not None:
                    x1, y1 = prev_coord
                    x2, y2 = elem[1], elem[2]
                    dist_mm = math.hypot(x2 - x1, y2 - y1) * self.STITCH_RES_MM
                    n = math.ceil(dist_mm / max_length_mm) if dist_mm > max_length_mm else 1
                    if max_dx_mm is not None:
                        dx_mm = abs(x2 - x1) * self.STITCH_RES_MM
                        if dx_mm > max_dx_mm:
                            n = max(n, math.ceil(dx_mm / max_dx_mm))
                    if n >= 2:
                        for i in range(1, n):
                            t = i / n
                            ax = x1 + t * (x2 - x1)
                            ay = y1 + t * (y2 - y1)
                            if align_to_grid:
                                ax = round(ax)
                                ay = round(ay)
                            new_display.append((ELEM_AUTO, ax, ay))
                            new_map.append(None)
                prev_coord = (elem[1], elem[2])
            new_display.append(elem)
            new_map.append(base_idx)
        self._display_elements = new_display
        self._display_base_map = new_map

    def get_stitch_bounds(self):
        """Return stitch bounds as (min_x, min_y, max_x, max_y), or None if empty."""
        coords = [(e[1], e[2]) for e in self.elements if elem_has_coords(e)]
        if not coords:
            return None
        xs = [x for x, _ in coords]
        ys = [y for _, y in coords]
        return min(xs), min(ys), max(xs), max(ys)

    def get_max_stitch_gap_mm(self):
        """Return the maximum distance in mm between consecutive coord-bearing elements
        in the display layer (includes auto-stitches when active).

        Returns 0.0 when the display has fewer than two coord-bearing elements.
        """
        coords = [(e[1], e[2]) for e in self._display_elements if elem_has_coords(e)]
        if len(coords) < 2:
            return 0.0
        return max(
            math.hypot(coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1])
            * self.STITCH_RES_MM
            for i in range(len(coords) - 1)
        )

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
