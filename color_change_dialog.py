"""Dialog for editing the colour-change markers of an embroidery pattern."""

from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QDialog, QDialogButtonBox,
    QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
)

from model import (DEFAULT_PALETTE, ELEM_COLOR, ELEM_SECTION, ELEM_STITCH,
                   elem_has_coords, is_hoop_stitch_type)
from section_markers_dialog import StitchPreviewCanvas


class _PaletteSwatch(QPushButton):
    """Palette swatch that also reports double clicks.

    QPushButton has no double-click signal, and a double click has to stay
    distinct from the single click that only selects the swatch.
    """

    double_clicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class PaletteColorDialog(QDialog):
    """Pick one of the pattern's 16 thread colours.

    A plain swatch grid: the palette index is what the colour-change record
    stores, so free RGB values would not be usable. Double-clicking a swatch
    opens a colour picker for that palette entry; the changes are reported
    through :attr:`edited_colors` and only touch this dialog's copy.
    """

    COLUMNS = 8
    SWATCH_SIZE = 32

    def __init__(self, colors, current_index=0, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Select Color"))
        self._colors = [tuple(color) for color in colors]
        self._selected_index = max(0, min(int(current_index), len(self._colors) - 1))
        self._swatches = []   # one button per entry, so an edit can update it
        self._edited = {}     # palette index -> new (r, g, b)

        grid = QGridLayout()
        grid.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for index, (r, g, b) in enumerate(self._colors):
            button = _PaletteSwatch()
            button.setCheckable(True)
            button.setFixedSize(self.SWATCH_SIZE, self.SWATCH_SIZE)
            button.setToolTip(self._swatch_tooltip(index))
            button.setStyleSheet(self._swatch_style(r, g, b))
            button.setChecked(index == self._selected_index)
            button.clicked.connect(
                lambda _checked=False, i=index: self._select(i))
            button.double_clicked.connect(
                lambda i=index: self._edit_color(i))
            self._group.addButton(button, index)
            self._swatches.append(button)
            grid.addWidget(button, index // self.COLUMNS, index % self.COLUMNS)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        hint = QLabel(self.tr("Double-click a color to change it."))
        hint.setStyleSheet("color: gray;")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("Available thread colors")))
        layout.addLayout(grid)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    @staticmethod
    def _swatch_style(r, g, b):
        """Return the style sheet that draws one swatch in *r*, *g*, *b*."""
        return (
            f"QPushButton {{ background-color: rgb({r},{g},{b});"
            f" border: 1px solid #808080; }}"
            f"QPushButton:checked {{ border: 3px solid #1a73e8; }}")

    def _swatch_tooltip(self, index):
        """Return the tooltip naming colour *index* and its RGB values."""
        r, g, b = self._colors[index]
        return self.tr("Color {0}: RGB({1}, {2}, {3})").format(
            index + 1, r, g, b)

    def _select(self, index):
        """Remember the clicked swatch; OK confirms the choice."""
        self._selected_index = index

    def _edit_color(self, index):
        """Open a colour picker for palette entry *index* (double click)."""
        if not 0 <= index < len(self._colors):
            return
        chosen = QColorDialog.getColor(
            QColor(*self._colors[index]), self,
            self.tr("Change Color {0}").format(index + 1))
        if not chosen.isValid():
            return
        rgb = (chosen.red(), chosen.green(), chosen.blue())
        if rgb == self._colors[index]:
            return
        self._colors[index] = rgb
        self._edited[index] = rgb
        self._swatches[index].setStyleSheet(self._swatch_style(*rgb))
        self._swatches[index].setToolTip(self._swatch_tooltip(index))

    @property
    def selected_index(self):
        """Return the palette index the user chose."""
        return self._selected_index

    @property
    def edited_colors(self):
        """Return ``{index: (r, g, b)}`` for the entries changed here."""
        return dict(self._edited)


class ColorChangeMarkersDialog(QDialog):
    """Edit the colour-change markers of an embroidery pattern.

    Works like the section-marker editor: the dialog holds a copy of
    ``pattern.elements``, Add/Remove/Edit only touch that copy, Apply replaces
    the element list in one undo step and Cancel discards it.

    A marker has no coordinates, so all marker operations are anchored on the
    stitch the marker belongs to: Add inserts the record directly before the
    selected stitch, Remove deletes the record directly before it.
    """

    def __init__(self, pattern, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Edit Color Change Markers"))
        self.resize(980, 660)

        self._pattern = pattern
        self._elements = list(pattern.elements)
        # Embroidery designs always carry a palette; the default keeps the
        # swatches meaningful if one is missing (third-party files).
        self._colors = list(pattern.colors) or list(DEFAULT_PALETTE)
        self._current_index = None   # anchor stitch that Add/Remove act on
        self._marker_index = None    # selected ELEM_COLOR element
        self._palette_edits = {}     # palette index -> (r, g, b), applied with Apply
        self._did_fit = False

        # ── Left: colour changes with Add / Remove / Edit ──
        self._color_list = QListWidget()
        self._color_list.setSelectionMode(QListWidget.SingleSelection)
        self._color_list.setIconSize(QSize(14, 14))
        self._color_list.setMinimumWidth(170)
        self._color_list.itemSelectionChanged.connect(self._on_list_selection_changed)

        self._add_button = QPushButton(self.tr("Add"))
        self._add_button.setToolTip(
            self.tr("Insert a colour change before the selected stitch."))
        self._add_button.clicked.connect(self._add_marker)

        self._remove_button = QPushButton(self.tr("Remove"))
        self._remove_button.setToolTip(
            self.tr("Remove the colour change before the selected stitch."))
        self._remove_button.clicked.connect(self._remove_marker)

        self._edit_button = QPushButton(self.tr("Edit"))
        self._edit_button.setToolTip(
            self.tr("Choose another colour for the selected colour change."))
        self._edit_button.clicked.connect(self._edit_marker)

        marker_buttons = QHBoxLayout()
        marker_buttons.addWidget(self._add_button)
        marker_buttons.addWidget(self._remove_button)
        marker_buttons.addWidget(self._edit_button)

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel(self.tr("Color changes")))
        left_layout.addWidget(self._color_list, 1)
        left_layout.addLayout(marker_buttons)

        # ── Right: preview with navigation ──
        self._preview = StitchPreviewCanvas(
            self._elements, self._colors,
            pattern.CANVAS_WIDTH, pattern.CANVAS_HEIGHT)
        self._preview.point_clicked.connect(self._on_canvas_point_clicked)

        self._scroll = QScrollArea()
        self._scroll.setWidget(self._preview)
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignCenter)
        self._scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._scroll.setMinimumSize(560, 420)
        self._preview.set_scroll_area(self._scroll)

        # Embroidery designs read better as a thread view, so the points start
        # hidden; they can be switched on for precise picking.
        self._show_points_check = QCheckBox(self.tr("Show stitch points"))
        self._show_points_check.setChecked(not is_hoop_stitch_type(pattern.stitch_type))
        self._show_points_check.toggled.connect(self._preview.set_show_stitch_points)
        self._preview.set_show_stitch_points(self._show_points_check.isChecked())

        self._auto_center_check = QCheckBox(self.tr("Auto-center"))
        self._auto_center_check.setChecked(True)
        self._auto_center_check.setToolTip(
            self.tr("Scroll the preview so the selected stitch stays centred."))

        hint = QLabel(self.tr("Ctrl+scroll to zoom. Click a stitch to select it."))
        hint.setStyleSheet("color: gray;")

        self._range_label = QLabel()
        self._first_button = self._nav_button(
            "|<", self.tr("Select the first stitch"), self._select_first)
        self._previous_button = self._nav_button(
            "<", self.tr("Select the previous stitch"), self._select_previous)
        self._next_button = self._nav_button(
            ">", self.tr("Select the next stitch"), self._select_next)
        self._last_button = self._nav_button(
            ">|", self.tr("Select the last stitch"), self._select_last)

        nav_layout = QHBoxLayout()
        nav_layout.addWidget(self._first_button)
        nav_layout.addWidget(self._previous_button)
        nav_layout.addWidget(self._next_button)
        nav_layout.addWidget(self._last_button)
        nav_layout.addWidget(self._show_points_check)
        nav_layout.addWidget(self._auto_center_check)
        nav_layout.addStretch(1)
        nav_layout.addWidget(hint)

        right_layout = QVBoxLayout()
        right_layout.addWidget(self._scroll, 1)
        right_layout.addLayout(nav_layout)
        right_layout.addWidget(self._range_label)

        body = QHBoxLayout()
        body.addLayout(left_layout)
        body.addLayout(right_layout, 1)

        # ── Apply / Cancel ──
        self._buttons = QDialogButtonBox()
        self._apply_button = self._buttons.addButton(
            self.tr("Apply"), QDialogButtonBox.AcceptRole)
        self._buttons.addButton(self.tr("Cancel"), QDialogButtonBox.RejectRole)
        self._buttons.accepted.connect(self._apply)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(self._buttons)

        self._refresh_views()
        if self._color_list.count():
            self._color_list.setCurrentRow(0)

    # ── Widget setup helpers ──

    def _nav_button(self, text, tooltip, slot):
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setFixedWidth(34)
        button.clicked.connect(slot)
        return button

    def showEvent(self, event):
        super().showEvent(event)
        if not self._did_fit:
            self._did_fit = True
            # Fit once the layout has given the scroll area its real size.
            QTimer.singleShot(0, self._preview.fit_to_viewport)

    def _swatch_icon(self, color_index):
        """Return a small bordered colour swatch for the list."""
        r, g, b = self._colors[min(color_index, len(self._colors) - 1)]
        pixmap = QPixmap(14, 14)
        pixmap.fill(QColor(r, g, b))
        painter = QPainter(pixmap)
        painter.setPen(QColor(120, 120, 120))
        painter.drawRect(0, 0, 13, 13)
        painter.end()
        return QIcon(pixmap)

    # ── Element queries ──

    def _is_coord(self, index):
        """True when the element at *index* is a selectable stitch position."""
        return (index is not None
                and 0 <= index < len(self._elements)
                and self._elements[index][0] in (ELEM_STITCH, ELEM_SECTION))

    def _selectable_indices(self):
        """Return the indices the stitch selection may land on."""
        return [i for i, e in enumerate(self._elements) if elem_has_coords(e)]

    def _first_stitch_after(self, marker_index):
        """Return the first stitch index after *marker_index*, or ``None``."""
        for index in range(marker_index + 1, len(self._elements)):
            if self._elements[index][0] in (ELEM_STITCH, ELEM_SECTION):
                return index
        return None

    def _color_in_effect(self, index):
        """Return the palette index in effect at *index* (0 when unset)."""
        color_index = 0
        for elem in self._elements[:index]:
            if elem[0] == ELEM_COLOR:
                color_index = int(elem[1])
        return max(0, min(color_index, len(self._colors) - 1))

    def _removal_target(self):
        """Return the marker index Remove would delete, or ``None``.

        Remove deletes the record directly before the selected stitch.  A marker
        at the very end of the pattern has no stitch behind it, so its own list
        entry can be removed instead — otherwise it could never be deleted.
        """
        if self._current_index is not None and self._current_index > 0:
            candidate = self._current_index - 1
            if self._elements[candidate][0] == ELEM_COLOR:
                return candidate
        if self._current_index is None and self._marker_index is not None:
            return self._marker_index
        return None

    def _row_for_marker(self, marker_index):
        """Return the list row showing the marker at *marker_index*, or ``None``."""
        if marker_index is None:
            return None
        for row in range(self._color_list.count()):
            if self._color_list.item(row).data(Qt.UserRole) == marker_index:
                return row
        return None

    # ── View synchronisation ──

    def _refresh_views(self):
        """Rebuild the marker list and repaint the preview from the working copy."""
        self._color_list.blockSignals(True)
        self._color_list.clear()
        for index, elem in enumerate(self._elements):
            if elem[0] != ELEM_COLOR:
                continue
            color_index = min(int(elem[1]), len(self._colors) - 1)
            r, g, b = self._colors[color_index]
            item = QListWidgetItem(
                self._swatch_icon(color_index),
                self.tr("{0} → color {1}").format(index, color_index + 1))
            item.setData(Qt.UserRole, index)
            item.setToolTip(self.tr(
                "Color change at {0}: palette color {1}, RGB({2}, {3}, {4})"
            ).format(index, color_index + 1, r, g, b))
            self._color_list.addItem(item)
            if index == self._marker_index:
                self._color_list.setCurrentItem(item)
        self._color_list.blockSignals(False)

        if self._current_index is not None and self._is_coord(self._current_index):
            # The marker is a record, not a position: highlight the stitch that
            # starts the colour it selects.
            self._preview.set_selection([self._current_index], self._current_index)
        else:
            self._preview.set_selection([], None)

        self._update_actions()
        self._update_status()
        self._update_apply_state()

    def _update_actions(self):
        """Enable Add, Remove and Edit for the current anchor and marker."""
        add_enabled = (
            self._is_coord(self._current_index)
            and not (self._current_index > 0
                     and self._elements[self._current_index - 1][0] == ELEM_COLOR))
        self._add_button.setEnabled(add_enabled)
        self._remove_button.setEnabled(self._removal_target() is not None)
        self._edit_button.setEnabled(
            self._marker_index is not None
            and 0 <= self._marker_index < len(self._elements)
            and self._elements[self._marker_index][0] == ELEM_COLOR)

    def _update_status(self):
        """Show where the selected colour change starts, or the selected stitch."""
        if self._marker_index is not None and self._current_index is not None:
            color_index = min(int(self._elements[self._marker_index][1]),
                              len(self._colors) - 1)
            self._range_label.setText(
                self.tr("Color {0}, first stitch {1}").format(
                    color_index + 1, self._current_index))
        elif self._current_index is not None:
            self._range_label.setText(
                self.tr("Stitch: {0}").format(self._current_index))
        elif self._marker_index is not None:
            self._range_label.setText(
                self.tr("Color change at {0} (no stitch after it)").format(
                    self._marker_index))
        else:
            self._range_label.setText(self.tr("No color change selected"))

    def _update_apply_state(self):
        """Enable Apply only while the working copy differs from the pattern."""
        self._apply_button.setEnabled(self._has_changes())

    def _has_changes(self):
        """True when the working copy differs from the pattern."""
        return bool(self._palette_edits) or self._elements != self._pattern.elements

    def _scroll_preview_to(self, index):
        """Centre the preview on the point at *index* (when Auto-center is on)."""
        if not self._auto_center_check.isChecked():
            return
        if not self._is_coord(index):
            return
        elem = self._elements[index]
        sx, sy = self._preview.canvas_to_screen(elem[1], elem[2])
        viewport = self._scroll.viewport()
        self._scroll.horizontalScrollBar().setValue(
            int(sx - viewport.width() / 2))
        self._scroll.verticalScrollBar().setValue(
            int(sy - viewport.height() / 2))

    # ── Selection ──

    def _on_list_selection_changed(self):
        """Anchor on the first stitch of the colour the selected marker starts."""
        items = self._color_list.selectedItems()
        if not items:
            self._marker_index = None
            self._current_index = None
        else:
            self._marker_index = int(items[0].data(Qt.UserRole))
            self._current_index = self._first_stitch_after(self._marker_index)

        self._refresh_views()
        if self._current_index is not None:
            self._scroll_preview_to(self._current_index)

    def _on_canvas_point_clicked(self, index):
        """Select a stitch clicked in the preview; no marker is selected then."""
        self._current_index = index
        self._marker_index = None

        self._color_list.blockSignals(True)
        self._color_list.clearSelection()
        self._color_list.blockSignals(False)

        self._refresh_views()

    def _select_index(self, index):
        """Make *index* the single selected stitch."""
        self._current_index = index
        self._marker_index = None

        self._color_list.blockSignals(True)
        self._color_list.clearSelection()
        self._color_list.blockSignals(False)

        self._refresh_views()
        self._scroll_preview_to(index)

    def _select_first(self):
        selectable = self._selectable_indices()
        if selectable:
            self._select_index(selectable[0])

    def _select_last(self):
        selectable = self._selectable_indices()
        if selectable:
            self._select_index(selectable[-1])

    def _select_previous(self):
        selectable = self._selectable_indices()
        if not selectable:
            return
        if self._current_index is None:
            self._select_index(selectable[-1])
            return
        earlier = [index for index in selectable if index < self._current_index]
        if earlier:
            self._select_index(earlier[-1])

    def _select_next(self):
        selectable = self._selectable_indices()
        if not selectable:
            return
        if self._current_index is None:
            self._select_index(selectable[0])
            return
        later = [index for index in selectable if index > self._current_index]
        if later:
            self._select_index(later[0])

    # ── Editing the working copy ──

    def _add_marker(self):
        """Insert a colour change directly before the selected stitch."""
        index = self._current_index
        if not self._is_coord(index):
            return
        if index > 0 and self._elements[index - 1][0] == ELEM_COLOR:
            return    # the stitch already follows a marker

        # Start from the colour in effect, so the new marker changes nothing
        # until its colour is picked with Edit.
        self._elements.insert(index, (ELEM_COLOR, self._color_in_effect(index)))
        self._current_index = index + 1     # the anchor stitch moved one back
        self._marker_index = index
        self._refresh_views()

    def _remove_marker(self):
        """Remove the colour change directly before the selected stitch."""
        target = self._removal_target()
        if target is None:
            return
        del self._elements[target]
        if self._current_index is not None and target < self._current_index:
            self._current_index -= 1
        self._marker_index = None
        self._refresh_views()

    def _edit_marker(self):
        """Choose a new palette colour for the selected colour change."""
        marker = self._marker_index
        if marker is None or not 0 <= marker < len(self._elements):
            return
        elem = self._elements[marker]
        if elem[0] != ELEM_COLOR:
            return
        current = min(int(elem[1]), len(self._colors) - 1)
        dlg = PaletteColorDialog(self._colors, current, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        self._stage_palette_edits(dlg.edited_colors)
        self._apply_marker_color(marker, dlg.selected_index)

    def _stage_palette_edits(self, edits):
        """Keep palette colours changed in the picker until Apply.

        The preview paints from this dialog's palette copy, so the entries are
        updated right away; the pattern receives them together with the element
        edits, which keeps Cancel a full discard.

        Returns:
            bool: True when at least one entry changed here.
        """
        changed = False
        for index, rgb in edits.items():
            if 0 <= index < len(self._colors):
                self._colors[index] = rgb
                self._palette_edits[index] = rgb
                changed = True
        if changed:
            # A palette colour is not part of the element list, so the list
            # icons, the preview and the Apply state need their own refresh.
            self._refresh_views()
            self._preview.update()
        return changed

    def _apply_marker_color(self, marker_index, color_index):
        """Set the palette entry of the colour-change record at *marker_index*."""
        elem = self._elements[marker_index]
        if elem[0] != ELEM_COLOR:
            return
        color_index = max(0, min(int(color_index), len(self._colors) - 1))
        if int(elem[1]) == color_index:
            return
        self._elements[marker_index] = (ELEM_COLOR, color_index)
        self._refresh_views()

    # ── Dialog result ──

    def _apply(self):
        """Replace the pattern's element list as one undo step and close."""
        self._apply_palette_edits()
        self._pattern.replace_elements(self._elements)
        self.accept()

    def _apply_palette_edits(self):
        """Store the staged palette colours on the pattern, one undo step each."""
        for index, rgb in sorted(self._palette_edits.items()):
            if index < len(self._pattern.colors):
                self._pattern.set_palette_color(index, rgb)
        self._palette_edits.clear()
