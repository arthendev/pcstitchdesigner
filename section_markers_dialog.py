"""Dialog for editing the section markers of the active stitch pattern."""

import math

from PyQt5.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QPainter, QPen, QPolygon
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from model import (ELEM_COLOR, ELEM_SECTION, ELEM_STITCH, elem_has_coords,
                   is_hoop_stitch_type)


class SectionPreviewCanvas(QWidget):
    """Read-only stitch preview with click-to-select and Ctrl+wheel zoom.

    The widget reads the dialog's working element list live, so changing that
    list only requires a call to ``update()``.
    """

    point_clicked = pyqtSignal(int)  # base element index of the clicked stitch

    MARGIN = 20
    GRID_STEP = 6            # one millimetre in canvas units
    MIN_SCALE = 1.0
    ZOOM_STEP = 1.25
    POINT_RADIUS = 3
    SECTION_RADIUS = 5
    HIT_RADIUS_PX = 10

    COLOR_BG = QColor(255, 255, 255)
    COLOR_GRID = QColor(228, 228, 228)
    COLOR_BORDER = QColor(0, 80, 200)
    COLOR_LINE = QColor(0, 0, 0)
    COLOR_POINT = QColor(0, 0, 0)
    COLOR_SECTION = QColor(255, 140, 0)     # orange diamond = section marker
    COLOR_HIGHLIGHT = QColor(210, 0, 120)   # magenta = selection

    def __init__(self, elements, colors, canvas_w, canvas_h, parent=None):
        """Create the preview.

        Args:
            elements: The dialog's working copy of the element list.
            colors: Thread palette of the pattern; empty for 9 mm / MAXI.
            canvas_w, canvas_h: Canvas size in canvas units.
        """
        super().__init__(parent)
        self._elements = elements
        self._colors = colors
        self._canvas_w = int(canvas_w)
        self._canvas_h = int(canvas_h)
        self._scale = 2.0
        self._scroll_area = None
        self._selected_indices = []
        self._current_index = None
        self._show_stitch_points = True

        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._update_size()

    # ── State and geometry ──

    def set_scroll_area(self, scroll_area):
        """Attach the scroll area that holds this widget, for cursor-anchored zoom."""
        self._scroll_area = scroll_area

    def set_selection(self, selected_indices, current_index):
        """Highlight *selected_indices* and ring *current_index*.

        Two selected indices are drawn as one continuous range.
        """
        self._selected_indices = list(selected_indices)
        self._current_index = current_index
        self.update()

    def set_show_stitch_points(self, show):
        """Show or hide the stitch/segment point markers.

        Hidden turns the preview into a plain thread view; the selection ring
        stays visible so the current stitch can still be found.
        """
        self._show_stitch_points = bool(show)
        self.update()

    def fit_to_viewport(self):
        """Scale so the whole canvas fits the visible viewport."""
        if self._scroll_area is None:
            return
        viewport = self._scroll_area.viewport()
        avail_w = viewport.width() - 2 * self.MARGIN - 4
        avail_h = viewport.height() - 2 * self.MARGIN - 4
        scale = min(avail_w / self._canvas_w, avail_h / self._canvas_h)
        self._scale = max(self.MIN_SCALE, scale)
        self._update_size()
        self.update()

    def _update_size(self):
        width = int(self._canvas_w * self._scale + 2 * self.MARGIN)
        height = int(self._canvas_h * self._scale + 2 * self.MARGIN)
        self.setFixedSize(QSize(width, height))

    def canvas_to_screen(self, cx, cy):
        """Transform canvas coordinates (origin bottom-left) to widget pixels."""
        return (self.MARGIN + cx * self._scale,
                self.MARGIN + (self._canvas_h - cy) * self._scale)

    def screen_to_canvas(self, sx, sy):
        """Transform widget pixels to canvas coordinates (origin bottom-left)."""
        return ((sx - self.MARGIN) / self._scale,
                self._canvas_h - (sy - self.MARGIN) / self._scale)

    # ── Interaction ──

    def wheelEvent(self, event):
        # Without Ctrl the wheel belongs to the surrounding scroll area.
        if not (event.modifiers() & Qt.ControlModifier):
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta == 0:
            event.accept()
            return
        factor = self.ZOOM_STEP if delta > 0 else 1.0 / self.ZOOM_STEP
        self._zoom_at(self._scale * factor, event.pos())
        event.accept()

    def _zoom_at(self, new_scale, pos):
        """Zoom to *new_scale*, keeping the canvas point under *pos* stationary."""
        new_scale = max(self.MIN_SCALE, new_scale)
        if new_scale == self._scale:
            return
        if self._scroll_area is None:
            self._scale = new_scale
            self._update_size()
            self.update()
            return

        cx, cy = self.screen_to_canvas(pos.x(), pos.y())
        scroll = self._scroll_area
        view_x = pos.x() - scroll.horizontalScrollBar().value()
        view_y = pos.y() - scroll.verticalScrollBar().value()

        self._scale = new_scale
        self._update_size()
        self.update()

        sx, sy = self.canvas_to_screen(cx, cy)
        scroll.horizontalScrollBar().setValue(int(sx - view_x))
        scroll.verticalScrollBar().setValue(int(sy - view_y))

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        index = self._nearest_selectable(event.x(), event.y())
        if index is not None:
            self.point_clicked.emit(index)
        event.accept()

    def _nearest_selectable(self, sx, sy):
        """Return the index of the closest stitch/section marker within reach.

        Colour changes have no position and auto-stitches are generated, so
        neither can be picked.
        """
        best_index = None
        best_distance = float(self.HIT_RADIUS_PX)
        for index, elem in enumerate(self._elements):
            if elem[0] not in (ELEM_STITCH, ELEM_SECTION):
                continue
            px, py = self.canvas_to_screen(elem[1], elem[2])
            distance = math.hypot(sx - px, sy - py)
            if distance <= best_distance:
                best_distance = distance
                best_index = index
        return best_index

    # ── Painting ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.COLOR_BG)

        painter.setPen(QPen(self.COLOR_GRID, 1))
        for x in range(0, self._canvas_w + 1, self.GRID_STEP):
            x0, y0 = self.canvas_to_screen(x, self._canvas_h)
            x1, y1 = self.canvas_to_screen(x, 0)
            painter.drawLine(int(x0), int(y0), int(x1), int(y1))
        for y in range(0, self._canvas_h + 1, self.GRID_STEP):
            x0, y0 = self.canvas_to_screen(0, y)
            x1, y1 = self.canvas_to_screen(self._canvas_w, y)
            painter.drawLine(int(x0), int(y0), int(x1), int(y1))

        painter.setPen(QPen(self.COLOR_BORDER, 2))
        bx0, by0 = self.canvas_to_screen(0, 0)
        bx1, by1 = self.canvas_to_screen(self._canvas_w, self._canvas_h)
        painter.drawRect(int(bx1), int(by1), int(bx0 - bx1), int(by0 - by1))

        entries = self._coord_entries()

        # Stitch lines; a colour change between two points cuts the thread.
        painter.setPen(QPen(self.COLOR_LINE, 1))
        for previous, current in zip(entries, entries[1:]):
            if previous[3] != current[3]:
                continue
            painter.setPen(QPen(self._color_for(current[3]), 1))
            self._draw_segment(painter, previous, current)

        # Wide translucent band under the highlighted section/stitch.
        span = self._highlight_span()
        if span is not None:
            band_pen = QPen(QColor(210, 0, 120, 110), 5)
            band_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(band_pen)
            inside = [entry for entry in entries if span[0] <= entry[0] <= span[1]]
            for previous, current in zip(inside, inside[1:]):
                if previous[3] != current[3]:
                    continue
                self._draw_segment(painter, previous, current)

        if self._show_stitch_points:
            for index, x, y, color_index in entries:
                sx, sy = self.canvas_to_screen(x, y)
                kind = self._elements[index][0]
                radius = self._radius_for(kind)
                if span is not None and span[0] <= index <= span[1]:
                    self._draw_point(painter, sx, sy, kind,
                                     self.COLOR_HIGHLIGHT, radius + 2)
                else:
                    self._draw_point(painter, sx, sy, kind,
                                     self._point_color(index, color_index), radius)

        # Ring around the current stitch so it stands out even in a range.
        if self._current_index is not None:
            elem = self._elements[self._current_index]
            sx, sy = self.canvas_to_screen(elem[1], elem[2])
            radius = self._radius_for(elem[0]) + 6
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(self.COLOR_HIGHLIGHT, 2))
            painter.drawEllipse(int(sx - radius), int(sy - radius),
                                2 * radius, 2 * radius)

    def _coord_entries(self):
        """Return ``[(index, x, y, color_index), ...]`` for the stitch positions."""
        entries = []
        color_index = 0
        for index, elem in enumerate(self._elements):
            if elem[0] == ELEM_COLOR:
                if self._colors:
                    color_index = min(elem[1], len(self._colors) - 1)
                continue
            if not elem_has_coords(elem):
                continue
            entries.append((index, elem[1], elem[2], color_index))
        return entries

    def _color_for(self, color_index):
        """Line colour for a palette index, or black without a palette."""
        if self._colors:
            r, g, b = self._colors[min(color_index, len(self._colors) - 1)]
            return QColor(r, g, b)
        return self.COLOR_LINE

    def _point_color(self, index, color_index):
        """Point colour: markers keep their marker colour, stitches follow the thread."""
        if self._elements[index][0] == ELEM_SECTION:
            return self.COLOR_SECTION
        return self._color_for(color_index)

    @staticmethod
    def _radius_for(kind):
        return (SectionPreviewCanvas.SECTION_RADIUS if kind == ELEM_SECTION
                else SectionPreviewCanvas.POINT_RADIUS)

    def _draw_segment(self, painter, previous, current):
        px0, py0 = self.canvas_to_screen(previous[1], previous[2])
        px1, py1 = self.canvas_to_screen(current[1], current[2])
        painter.drawLine(int(px0), int(py0), int(px1), int(py1))

    def _draw_point(self, painter, sx, sy, kind, color, radius):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        if kind == ELEM_SECTION:
            # Diamond, so a marker stays recognisable next to round stitches.
            diamond = QPolygon([
                QPoint(int(sx), int(sy - radius)),
                QPoint(int(sx + radius), int(sy)),
                QPoint(int(sx), int(sy + radius)),
                QPoint(int(sx - radius), int(sy)),
            ])
            painter.drawPolygon(diamond)
        else:
            painter.drawEllipse(int(sx - radius), int(sy - radius),
                                2 * radius, 2 * radius)

    def _highlight_span(self):
        """Return the inclusive index range to highlight, or ``None``."""
        if len(self._selected_indices) >= 2:
            return min(self._selected_indices), max(self._selected_indices)
        if self._current_index is not None:
            return self._current_index, self._current_index
        if self._selected_indices:
            index = self._selected_indices[0]
            return index, index
        return None


class SectionMarkersDialog(QDialog):
    """Edit which stitch points of a pattern are section markers.

    The dialog works on a copy of ``pattern.elements``: nothing reaches the
    pattern until Apply, Cancel discards the working copy, and Apply pushes a
    single ``SetSectionMarkersCommand`` so the edit is one undo step.
    """

    def __init__(self, pattern, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Edit Section Markers"))
        self.resize(980, 660)

        self._pattern = pattern
        self._elements = list(pattern.elements)
        self._current_index = None
        self._selected_indices = []
        self._did_fit = False

        # ── Left: section marker list with Add / Remove ──
        self._section_list = QListWidget()
        self._section_list.setSelectionMode(QListWidget.ExtendedSelection)
        self._section_list.setMinimumWidth(150)
        self._section_list.itemSelectionChanged.connect(self._on_list_selection_changed)

        self._add_button = QPushButton(self.tr("Add"))
        self._add_button.setToolTip(
            self.tr("Turn the selected stitch into a section marker."))
        self._add_button.clicked.connect(self._add_section)

        self._remove_button = QPushButton(self.tr("Remove"))
        self._remove_button.setToolTip(
            self.tr("Turn the selected section marker back into a stitch."))
        self._remove_button.clicked.connect(self._remove_section)

        section_buttons = QHBoxLayout()
        section_buttons.addWidget(self._add_button)
        section_buttons.addWidget(self._remove_button)

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel(self.tr("Section markers")))
        left_layout.addWidget(self._section_list, 1)
        left_layout.addLayout(section_buttons)

        # ── Right: preview with navigation ──
        self._preview = SectionPreviewCanvas(
            self._elements, pattern.colors,
            pattern.CANVAS_WIDTH, pattern.CANVAS_HEIGHT)
        self._preview.point_clicked.connect(self._on_canvas_point_clicked)

        self._scroll = QScrollArea()
        self._scroll.setWidget(self._preview)
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignCenter)
        self._scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._scroll.setMinimumSize(560, 420)
        self._preview.set_scroll_area(self._scroll)

        # Stitch points clutter the thread view of an embroidery design, which
        # is why they start hidden there and visible for the 9 mm / MAXI grids.
        self._show_points_check = QCheckBox(self.tr("Show stitch points"))
        self._show_points_check.setChecked(not is_hoop_stitch_type(pattern.stitch_type))
        self._show_points_check.toggled.connect(self._preview.set_show_stitch_points)
        self._preview.set_show_stitch_points(self._show_points_check.isChecked())

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
        if self._section_list.count():
            self._section_list.setCurrentRow(0)

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

    # ── Element queries ──

    def _section_indices(self):
        """Return the indices of all section markers in the working copy."""
        return [i for i, e in enumerate(self._elements) if e[0] == ELEM_SECTION]

    def _selectable_indices(self):
        """Return the indices the selection may land on.

        Only stitches and section markers: a colour change carries no position
        and an auto-stitch is generated, so neither can be selected.
        """
        return [i for i, e in enumerate(self._elements)
                if e[0] in (ELEM_STITCH, ELEM_SECTION)]

    def _row_for_index(self, index):
        """Return the list row showing element *index*, or ``None``."""
        if index is None:
            return None
        for row in range(self._section_list.count()):
            if self._section_list.item(row).data(Qt.UserRole) == index:
                return row
        return None

    # ── View synchronisation ──

    def _refresh_views(self):
        """Rebuild the marker list and push the current selection to the preview."""
        self._section_list.blockSignals(True)
        self._section_list.clear()
        for index in self._section_indices():
            elem = self._elements[index]
            item = QListWidgetItem(str(index))
            item.setData(Qt.UserRole, index)
            item.setToolTip(
                self.tr("Stitch {0}: x {1}, y {2}").format(index, elem[1], elem[2]))
            self._section_list.addItem(item)
            if index in self._selected_indices:
                item.setSelected(True)
        row = self._row_for_index(self._current_index)
        if row is not None:
            self._section_list.setCurrentRow(row)
        self._section_list.blockSignals(False)

        self._preview.set_selection(self._selected_indices, self._current_index)
        self._update_actions()
        self._update_status()
        self._update_apply_state()

    def _update_actions(self):
        """Enable Add or Remove for the kind of the currently selected element."""
        element = None
        if self._current_index is not None and 0 <= self._current_index < len(self._elements):
            element = self._elements[self._current_index]
        self._add_button.setEnabled(element is not None and element[0] == ELEM_STITCH)
        self._remove_button.setEnabled(element is not None and element[0] == ELEM_SECTION)

    def _update_status(self):
        """Show the selected stitch index or the selected index range."""
        if len(self._selected_indices) >= 2:
            first, last = min(self._selected_indices), max(self._selected_indices)
            self._range_label.setText(
                self.tr("Range: {0} – {1}").format(first, last))
        elif self._current_index is not None:
            kind = self._elements[self._current_index][0]
            name = self.tr("Section marker") if kind == ELEM_SECTION else self.tr("Stitch")
            self._range_label.setText(
                self.tr("{0}: {1}").format(name, self._current_index))
        else:
            self._range_label.setText(self.tr("No stitch selected"))

    def _update_apply_state(self):
        """Enable Apply only while the working copy differs from the pattern."""
        self._apply_button.setEnabled(self._has_changes())

    def _has_changes(self):
        """True when the working copy differs from the pattern's elements."""
        if len(self._elements) != len(self._pattern.elements):
            return True
        return any(new != old for new, old in
                   zip(self._elements, self._pattern.elements))

    def _scroll_preview_to(self, index):
        """Centre the preview on the point at *index*."""
        if not 0 <= index < len(self._elements):
            return
        elem = self._elements[index]
        if not elem_has_coords(elem):
            return
        sx, sy = self._preview.canvas_to_screen(elem[1], elem[2])
        viewport = self._scroll.viewport()
        self._scroll.horizontalScrollBar().setValue(
            int(sx - viewport.width() / 2))
        self._scroll.verticalScrollBar().setValue(
            int(sy - viewport.height() / 2))

    # ── Selection ──

    def _on_list_selection_changed(self):
        """Highlight one marker, or the whole range between two markers."""
        indices = sorted(
            int(item.data(Qt.UserRole))
            for item in self._section_list.selectedItems())
        self._selected_indices = indices
        self._current_index = indices[0] if len(indices) == 1 else None

        self._preview.set_selection(self._selected_indices, self._current_index)
        self._update_actions()
        self._update_status()
        if self._current_index is not None:
            self._scroll_preview_to(self._current_index)

    def _on_canvas_point_clicked(self, index):
        """Select a stitch clicked in the preview."""
        self._current_index = index
        self._selected_indices = []

        # Keep the list in sync, but the click already decided the state.
        self._section_list.blockSignals(True)
        self._section_list.clearSelection()
        row = self._row_for_index(index)
        if row is not None:
            self._section_list.setCurrentRow(row)
        self._section_list.blockSignals(False)

        self._preview.set_selection([], index)
        self._update_actions()
        self._update_status()

    def _select_index(self, index):
        """Make *index* the single selected stitch."""
        self._current_index = index
        self._selected_indices = []

        self._section_list.blockSignals(True)
        self._section_list.clearSelection()
        row = self._row_for_index(index)
        if row is not None:
            self._section_list.setCurrentRow(row)
        self._section_list.blockSignals(False)

        self._preview.set_selection([], index)
        self._update_actions()
        self._update_status()
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

    def _add_section(self):
        """Turn the selected stitch into a section marker."""
        index = self._current_index
        if index is None:
            return
        elem = self._elements[index]
        if elem[0] != ELEM_STITCH:
            return
        self._elements[index] = (ELEM_SECTION, elem[1], elem[2])
        self._after_edit(index)

    def _remove_section(self):
        """Turn the selected section marker back into a stitch."""
        index = self._current_index
        if index is None:
            return
        elem = self._elements[index]
        if elem[0] != ELEM_SECTION:
            return
        self._elements[index] = (ELEM_STITCH, elem[1], elem[2])
        self._after_edit(index)

    def _after_edit(self, index):
        """Re-sync list, preview and buttons after the kind at *index* changed."""
        self._selected_indices = []
        self._current_index = index
        self._refresh_views()

    # ── Dialog result ──

    def _apply(self):
        """Push the edited markers to the pattern as one undo step and close."""
        edits = [(index, element)
                 for index, element in enumerate(self._elements)
                 if element != self._pattern.elements[index]]
        self._pattern.set_section_markers(edits)
        self.accept()
