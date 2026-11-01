"""Read-only dialog summarising the design currently open in the window.

Design → Design Info answers "what is in this pattern?" in one place, so the
figures the status bar spreads over several labels — size, stitches, colors —
can be read together with the parts the status bar does not show at all:
the file, the working area, colour changes, section markers and the palette.
"""

from PyQt5.QtCore import QLocale, Qt
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QGridLayout, QLabel, QVBoxLayout,
    QWidget,
)

from model import ELEM_COLOR, ELEM_SECTION
from preferences_dialog import palette_strip

# Shown where a figure has no meaning for the current design (no palette on a
# 9 mm / MAXI pattern, an empty pattern, a file that was never saved).
_EMPTY = "\u2014"


def _number(value):
    """Format *value* with the system locale's thousands separators."""
    return QLocale().toString(value)


class DesignInfoDialog(QDialog):
    """Show a read-only summary of *pattern*.

    Args:
        pattern: The ``StitchPattern`` to describe.
        design_type: User-facing design type as named by the Design menu
            ("9mm", "MAXI", "Embroidery (Hoop 80×80)", …). Passed in so the
            window's naming of design types stays the single source.
        file_path: File the pattern was loaded from or saved to, if any.
        name: Pattern name shown in the window title.
        parent: Parent widget.
    """

    def __init__(self, pattern, design_type, file_path=None, name="",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Design Info"))
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)

        # Colour changes and section markers only exist as records in the
        # element list, so they are counted here rather than asked of the model.
        color_changes = sum(1 for e in pattern.elements if e[0] == ELEM_COLOR)
        section_markers = sum(1 for e in pattern.elements if e[0] == ELEM_SECTION)

        rows = (
            (self.tr("Name:"), name or self.tr("Untitled")),
            (self.tr("File path:"), file_path or self.tr("Not saved yet")),
            (self.tr("Design type:"), design_type),
            (self.tr("Working area:"), self._size_text(
                pattern.CANVAS_WIDTH_MM, pattern.CANVAS_HEIGHT_MM)),
            (self.tr("Design size:"), self._design_size_text(pattern)),
            (self.tr("Stitches:"), self._stitch_text(pattern)),
            # 9 mm / MAXI patterns are stitched in the machine's own thread,
            # so they have neither a palette nor a used-color count.
            (self.tr("Colors used:"),
             self._count_text(pattern.used_color_count()
                              if pattern.has_palette else None)),
            (self.tr("Color changes:"), self._count_text(color_changes)),
            (self.tr("Section markers:"), self._count_text(section_markers)),
        )

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        for row, (title, value) in enumerate(rows):
            grid.addWidget(self._title_label(title), row, 0, Qt.AlignTop)
            grid.addWidget(self._value_label(value), row, 1)
        palette_row = len(rows)
        grid.addWidget(self._title_label(self.tr("Color palette:")),
                       palette_row, 0, Qt.AlignTop)
        grid.addWidget(self._palette_widget(pattern), palette_row, 1)
        layout.addLayout(grid)

        layout.addWidget(self._separator())

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # ── Value formatting ──

    def _size_text(self, width_mm, height_mm):
        """Describe a size as "W × H mm"."""
        return self.tr("{0} × {1} mm").format(
            f"{width_mm:.1f}", f"{height_mm:.1f}")

    def _design_size_text(self, pattern):
        """Return the span of the stitches in mm, or an empty marker."""
        width_mm, height_mm = pattern.get_stitch_size_mm()
        if not width_mm and not height_mm:
            return _EMPTY
        return self._size_text(width_mm, height_mm)

    def _stitch_text(self, pattern):
        """Split the stitch number into normal and automatic stitches.

        The total is the figure the status bar reports: every needle position
        plus the automatic stitches generated between them.
        """
        total = pattern.stitch_count()
        auto = pattern.auto_stitch_count()
        return self.tr("{0} ({1} normal, {2} automatic)").format(
            _number(total), _number(total - auto), _number(auto))

    def _count_text(self, count):
        """Return *count* formatted, or the empty marker when not applicable."""
        return _EMPTY if count is None else _number(count)

    # ── Widgets ──

    def _title_label(self, text):
        """Build the bold label of a grid row."""
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    def _value_label(self, text):
        """Build a selectable value label (so a path can be copied)."""
        label = QLabel(text)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setWordWrap(True)
        return label

    def _palette_widget(self, pattern):
        """Build the palette row: the color count above the swatch strip."""
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        if not pattern.has_palette:
            column.addWidget(self._value_label(_EMPTY))
            return box

        column.addWidget(self._value_label(
            self.tr("{0} colors").format(_number(len(pattern.colors)))))
        # Same strip the preferences use for the default palette: one row of
        # narrow swatches with a single outline around the whole palette.
        swatches = QLabel()
        swatches.setPixmap(palette_strip(pattern.colors))
        swatches.setToolTip(
            self.tr("Thread colors the design can use"))
        column.addWidget(swatches)
        column.addStretch(1)
        return box

    def _separator(self):
        """Build the horizontal rule above the buttons."""
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        return line
