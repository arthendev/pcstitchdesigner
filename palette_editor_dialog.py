"""Dialog for editing the thread palette of an embroidery pattern."""

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView, QColorDialog, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from model import DEFAULT_PALETTE, ELEM_COLOR


class _PaletteListWidget(QListWidget):
    """Palette list whose rows can be reordered by dragging."""

    order_changed = pyqtSignal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.order_changed.emit()


class PaletteEditorDialog(QDialog):
    """Edit the thread palette of an embroidery pattern.

    The dialog works on a copy: swatch clicks, Add and Remove only touch that
    copy. Apply writes the palette and the renumbered colour-change records to
    the pattern in one undo step; Cancel discards everything.

    Removing an entry renumbers every later ELEM_COLOR record, and records that
    pointed at the removed entry move to the colour that slid into its place.
    That keeps the design's thread colours stable while the palette shrinks.
    """

    SWATCH_SIZE = 18

    def __init__(self, pattern, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Edit Color Palette"))
        self.resize(420, 480)

        self._pattern = pattern
        self._colors = [tuple(c) for c in pattern.colors]
        self._elements = list(pattern.elements)

        self._list = _PaletteListWidget()
        self._list.setIconSize(QSize(self.SWATCH_SIZE, self.SWATCH_SIZE))
        self._list.setDragDropMode(QAbstractItemView.InternalMove)
        self._list.setDefaultDropAction(Qt.MoveAction)
        self._list.itemDoubleClicked.connect(self._edit_color)
        self._list.itemSelectionChanged.connect(self._update_buttons)
        self._list.order_changed.connect(self._on_order_changed)

        self._add_button = QPushButton(self.tr("Add"))
        self._add_button.setToolTip(
            self.tr("Append a thread colour and choose its colour."))
        self._add_button.clicked.connect(self._add_color)

        self._edit_button = QPushButton(self.tr("Edit"))
        self._edit_button.setToolTip(
            self.tr("Change the selected thread colour."))
        self._edit_button.clicked.connect(self._edit_selected_color)

        self._remove_button = QPushButton(self.tr("Remove"))
        self._remove_button.setToolTip(
            self.tr("Remove the selected thread colour."))
        self._remove_button.clicked.connect(self._remove_color)

        hint = QLabel(self.tr(
            "Double-click a colour, or press Edit, to change it."))
        hint.setStyleSheet("color: gray;")

        button_row = QHBoxLayout()
        button_row.addWidget(self._add_button)
        button_row.addWidget(self._edit_button)
        button_row.addWidget(self._remove_button)
        button_row.addStretch(1)

        buttons = QDialogButtonBox()
        self._apply_button = buttons.addButton(
            self.tr("Apply"), QDialogButtonBox.AcceptRole)
        buttons.addButton(self.tr("Cancel"), QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("Thread colors")))
        layout.addWidget(self._list, 1)
        layout.addWidget(hint)
        layout.addLayout(button_row)
        layout.addWidget(buttons)

        self._rebuild()
        if self._list.count():
            self._list.setCurrentRow(0)
        self._update_buttons()

    # ── List helpers ──

    def _item_text(self, index):
        """Return the list text for palette entry *index* (1-based number)."""
        r, g, b = self._colors[index]
        return self.tr("{0}: RGB({1}, {2}, {3})").format(
            index + 1, r, g, b)

    def _swatch_icon(self, index):
        """Return the colour swatch shown in front of entry *index*."""
        r, g, b = self._colors[index]
        pixmap = QPixmap(self.SWATCH_SIZE, self.SWATCH_SIZE)
        pixmap.fill(QColor(r, g, b))
        painter = QPainter(pixmap)
        painter.setPen(QColor(120, 120, 120))
        painter.drawRect(0, 0, self.SWATCH_SIZE - 1, self.SWATCH_SIZE - 1)
        painter.end()
        return QIcon(pixmap)

    def _rebuild(self, select_index=None):
        """Rebuild the list from the working palette."""
        self._list.blockSignals(True)
        self._list.clear()
        for index in range(len(self._colors)):
            item = QListWidgetItem(self._swatch_icon(index),
                                   self._item_text(index))
            item.setData(Qt.UserRole, index)
            self._list.addItem(item)
        if select_index is not None and 0 <= select_index < self._list.count():
            self._list.setCurrentRow(select_index)
        self._list.blockSignals(False)
        self._update_apply_state()

    def _refresh_row(self, index):
        """Update one row after its colour changed."""
        item = self._list.item(index)
        if item is None:
            return
        item.setIcon(self._swatch_icon(index))
        item.setText(self._item_text(index))

    def _update_buttons(self):
        """Enable Edit/Remove only while a palette entry is selected."""
        row = self._list.currentRow()
        has_selection = 0 <= row < len(self._colors)
        self._edit_button.setEnabled(has_selection)
        self._remove_button.setEnabled(has_selection)

    def _on_order_changed(self):
        """Apply a drag-reordered list to the working palette and records.

        Every item still carries the palette index it had before the drop, so
        that old index gives both the colour and its new position.
        """
        remap = {}
        colors = []
        for row in range(self._list.count()):
            old_index = int(self._list.item(row).data(Qt.UserRole))
            remap[old_index] = row
            colors.append(self._colors[old_index])
        self._colors = colors

        # A colour-change record has to follow its thread to the new position.
        for position, elem in enumerate(self._elements):
            if elem[0] == ELEM_COLOR and int(elem[1]) in remap:
                self._elements[position] = (ELEM_COLOR, remap[int(elem[1])])

        for row in range(self._list.count()):
            item = self._list.item(row)
            item.setData(Qt.UserRole, row)
            item.setText(self._item_text(row))
        self._update_apply_state()

    def _update_apply_state(self):
        """Enable Apply only while the working copy differs from the pattern."""
        self._apply_button.setEnabled(self._has_changes())

    def _has_changes(self):
        """True when the working palette or element list differs."""
        return (self._colors != [tuple(c) for c in self._pattern.colors]
                or self._elements != self._pattern.elements)

    # ── Editing the working copy ──

    def _edit_color(self, item):
        """Open the colour picker for a double-clicked swatch."""
        self._pick_color(self._list.row(item))

    def _edit_selected_color(self):
        """Open the colour picker for the selected palette entry."""
        self._pick_color(self._list.currentRow())

    def _pick_color(self, row):
        """Let the user replace the colour at palette entry *row*."""
        if not 0 <= row < len(self._colors):
            return
        r, g, b = self._colors[row]
        chosen = QColorDialog.getColor(
            QColor(r, g, b), self,
            self.tr("Change Color {0}").format(row + 1))
        if not chosen.isValid():
            return
        rgb = (chosen.red(), chosen.green(), chosen.blue())
        if rgb == self._colors[row]:
            return
        self._colors[row] = rgb
        self._refresh_row(row)
        self._update_apply_state()

    def _add_color(self):
        """Append a colour and open the picker preset to it."""
        index = len(self._colors)
        self._colors.append(
            tuple(DEFAULT_PALETTE[index % len(DEFAULT_PALETTE)]))
        self._rebuild(select_index=index)
        self._update_buttons()
        self._pick_color(index)

    def _remove_color(self):
        """Remove the selected colour and renumber the colour-change records."""
        row = self._list.currentRow()
        if not 0 <= row < len(self._colors):
            return
        del self._colors[row]
        self._renumber_colour_records(row)
        self._rebuild(
            select_index=min(row, len(self._colors) - 1) if self._colors else None)
        self._update_buttons()

    def _renumber_colour_records(self, removed):
        """Adjust ELEM_COLOR indices after palette entry *removed* is gone."""
        new_last = len(self._colors) - 1
        for position, elem in enumerate(self._elements):
            if elem[0] != ELEM_COLOR:
                continue
            index = int(elem[1])
            if index > removed:
                self._elements[position] = (ELEM_COLOR, index - 1)
            elif index == removed:
                # The thread is gone: use the entry that slid into its place,
                # or the last one when the final entry was removed.
                self._elements[position] = (
                    ELEM_COLOR, min(removed, new_last) if new_last >= 0 else 0)

    # ── Dialog result ──

    def _apply(self):
        """Write palette and renumbered records to the pattern as one step."""
        self._pattern.set_palette_and_elements(self._colors, self._elements)
        self.accept()
