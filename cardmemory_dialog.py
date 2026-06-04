"""Card Memory dialog for browsing patterns stored on a machine memory card."""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QTabWidget, QWidget,
    QListWidget, QListWidgetItem, QListView, QAbstractItemView,
    QPushButton, QLabel, QMessageBox,
    QApplication, QProgressBar,
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon, QPixmap, QImage, QColor, QTransform

from machine_comm import MachineComm, MachineCommError


class CardMemoryDialog(QDialog):
    """Dialog for browsing and selecting patterns from a machine memory card.

    Layout::

        ┌─────────────────────────────────────────────────────┐
        │ Card No: 1002  |  9mm: 3  MAXI: 1  Embroidery: 2    │
        │ ┌──────────────────────────────────────────────┐    │
        │ │ [9mm] [MAXI] [Embroidery]                    │    │
        │ │ ┌──────────────────────────────────────────┐ │    │
        │ │ │  [thumb]  [thumb]  [thumb]  ...          │ │    │
        │ │ │                                          │ │    │
        │ │ └──────────────────────────────────────────┘ │    │
        │ └──────────────────────────────────────────────┘    │
        │ Name: Rose  |  Size: 1024 bytes                     │
        ├─────────────────────────────────────────────────────┤
        │                           [Load / Insert / Delete]  │
        │                                            [Close]  │
        └─────────────────────────────────────────────────────┘

    Args:
        card_info (dict): Card information as returned by
            :meth:`MachineComm.query_card_index`::

                {
                    'card_no':   int,
                    'n_9mm':     int,
                    'n_maxi':    int,
                    'n_embr':    int,
                }

        patterns (list[dict]): Preview data for each pattern, as returned by
            :meth:`MachineComm.query_card_preview`::

                {
                    'name':         str,
                    'size':         int,
                    'pattern_type': '9mm' | 'MAXI' | 'Embroidery',
                    'slot':         int,
                    'preview_hex':  str,
                }

        action (str): One of :attr:`ACTION_LOAD`, :attr:`ACTION_INSERT`, :attr:`ACTION_DELETE`.
        comm (MachineComm): Open machine communication instance.
        parent: Parent widget.
    """

    ACTION_LOAD   = "load"
    ACTION_INSERT = "insert"
    ACTION_DELETE = "delete"

    # Display scale applied to the raw preview pixmaps before use as icons.
    # 9mm images are 24 px tall, 53 px wide; MAXI 48 x 53 px; Embroidery 48 x 48 px.
    _ICON_SIZE = QSize(53, 48)

    _PROGRESS_BAR_HIDDEN_STYLE = (
        "QProgressBar { background: transparent; border: none; color: transparent; }"
        "QProgressBar::chunk { background: transparent; }"
    )
    # _ICON_SIZE = QSize(106, 96)

    def __init__(self, card_info, previews, action, comm, parent=None):
        super().__init__(parent)
        self._card_info = card_info
        self._previews = previews
        self._action = action
        self._comm = comm
        self._transmission_ended = False

        # Set by _do_load() / accepted when a pattern is successfully loaded
        self.loaded_points = None
        self.loaded_slot_type = None
        self.loaded_name = None

        self._setup_ui()

    # ── UI construction ──────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle(self.tr("Memory Card"))
        self.resize(640, 500)

        outer = QVBoxLayout(self)
        outer.setSpacing(6)

        # ── Card summary ──────────────────────────────────────────────────
        self._card_info_label = QLabel()
        self._update_card_info_label(self._card_info)
        outer.addWidget(self._card_info_label)

        # ── Tab widget (one per pattern type) ────────────────────────────
        self._tabs = QTabWidget()
        # We'll place the tabs and the action/info area in a horizontal middle row
        # so the action button can sit at the right with labels below it.

        self._lists = {}   # ptype → QListWidget
        for tab_name, ptype in (("9mm", "9mm"), ("MAXI", "MAXI"), (self.tr("Embroidery"), "Embroidery")):
            tab_patterns = [p for p in self._previews if p['pattern_type'] == ptype]

            tab_widget = QWidget()
            tab_layout = QVBoxLayout(tab_widget)
            tab_layout.setContentsMargins(4, 4, 4, 4)

            list_widget = QListWidget()
            list_widget.setViewMode(QListWidget.IconMode)
            list_widget.setIconSize(self._ICON_SIZE)
            list_widget.setResizeMode(QListWidget.Adjust)
            list_widget.setSpacing(10)
            list_widget.setSelectionMode(QListWidget.SingleSelection)
            # Disable dragging/moving of icons; allow click selection only
            list_widget.setDragEnabled(False)
            list_widget.setAcceptDrops(False)
            list_widget.setDragDropMode(QAbstractItemView.NoDragDrop)
            list_widget.setMovement(QListView.Static)
            list_widget.itemSelectionChanged.connect(self._on_selection_changed)
            list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)

            for p in tab_patterns:
                pixmap = self._build_pixmap(
                    p['preview_hex'],
                    p['pattern_type'],
                    is_embroidery=(p['pattern_type'] == 'Embroidery'),
                )
                item = QListWidgetItem()
                item.setText(p['name'] or self.tr("(unnamed)"))
                if pixmap is not None:
                    scaled = pixmap.scaled(
                        self._ICON_SIZE,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                    item.setIcon(QIcon(scaled))
                item.setData(Qt.UserRole, p)
                list_widget.addItem(item)

            tab_layout.addWidget(list_widget)
            self._tabs.addTab(tab_widget, tab_name)
            self._lists[ptype] = list_widget

        self._tabs.currentChanged.connect(self._on_tab_changed)

        # ── Middle row: tabs on the left, action button + info on the right
        middle_row = QHBoxLayout()
        middle_row.addWidget(self._tabs, 1)

        right_v = QVBoxLayout()
        # Add some top padding so the action button sits lower from the top edge
        right_v.setContentsMargins(0, 20, 0, 0)
        # Action button (moved to right side of window)
        if self._action == self.ACTION_LOAD:
            action_label = self.tr("Load")
        elif self._action == self.ACTION_INSERT:
            action_label = self.tr("Insert")
        elif self._action == self.ACTION_DELETE:
            action_label = self.tr("Delete")
        else:
            action_label = self.tr("Write")

        self._action_btn = QPushButton(action_label)
        self._action_btn.setEnabled(False)
        self._action_btn.setMinimumWidth(90)
        self._action_btn.clicked.connect(self._on_action)
        right_v.addWidget(self._action_btn, 0, Qt.AlignTop)

        # Name and size labels go below the action button
        self._name_label = QLabel(self.tr("Name: —"))
        self._size_label = QLabel(self.tr("Size: —"))
        right_v.addSpacing(8)
        right_v.addWidget(self._name_label)
        right_v.addWidget(self._size_label)
        right_v.addStretch()

        middle_row.addLayout(right_v)
        outer.addLayout(middle_row, 1)

        # ── Bottom button row ─────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setMaximumHeight(18)
        self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
        btn_row.addWidget(self._progress_bar, 1)

        close_btn = QPushButton(self.tr("Close"))
        close_btn.setMinimumWidth(80)
        close_btn.clicked.connect(self._on_close)
        btn_row.addWidget(close_btn)

        outer.addLayout(btn_row)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _update_card_info_label(self, card_info):
        """Refresh the card-summary label from *card_info*."""
        self._card_info_label.setText(
            self.tr("Card No: {0}   |   9mm: {1}   MAXI: {2}   Embroidery: {3}").format(
                card_info.get('card_no', 0),
                card_info.get('n_9mm', 0),
                card_info.get('n_maxi', 0),
                card_info.get('n_embr', 0),
            )
        )

    def _reload_previews(self, card_info):
        """Reload all preview images from the machine using *card_info*.

        Called on the slow path when the post-delete card-index verification
        reveals an unexpected change.  Clears all list widgets, fetches fresh
        previews for every pattern still on the card, repopulates the lists,
        and updates ``self._card_info`` and ``self._patterns``.
        """
        # Clear all list widgets first
        for lw in self._lists.values():
            lw.clear()

        offs_map = {
            '9mm':        'offs_9mm',
            'MAXI':       'offs_maxi',
            'Embroidery': 'offs_embr',
        }
        count_map = {
            '9mm':        'n_9mm',
            'MAXI':       'n_maxi',
            'Embroidery': 'n_embr',
        }

        total_patterns = sum(card_info.get(count_map[pt], 0) for pt in count_map)
        loaded = 0

        self._progress_bar.setValue(0)
        self._progress_bar.setStyleSheet("")
        QApplication.processEvents()

        new_previews = []
        for ptype, lw in self._lists.items():
            count = card_info.get(count_map[ptype], 0)
            offset = card_info.get(offs_map[ptype], 0)
            for i in range(count):
                card_slot = i + offset
                preview = self._comm.query_card_preview(card_info['card_no_bytes'], card_slot, ptype)
                loaded += 1
                if total_patterns > 0:
                    self._progress_bar.setValue(loaded * 100 // total_patterns)
                QApplication.processEvents()
                pixmap = self._build_pixmap(
                    preview['preview_hex'],
                    ptype,
                    is_embroidery=(ptype == 'Embroidery'),
                )
                item = QListWidgetItem()
                item.setText(preview['name'] or self.tr('(unnamed)'))
                if pixmap is not None:
                    scaled = pixmap.scaled(
                        self._ICON_SIZE,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                    item.setIcon(QIcon(scaled))
                item.setData(Qt.UserRole, preview)
                lw.addItem(item)
                new_previews.append(preview)

        self._previews = new_previews
        self._progress_bar.setValue(0)
        self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
        self._update_card_info_label(card_info)

    def _current_list(self):
        """Return the QListWidget for the currently visible tab."""
        idx = self._tabs.currentIndex()
        ptype = ("9mm", "MAXI", "Embroidery")[idx]
        return self._lists[ptype]

    def _selected_pattern(self):
        """Return the pattern dict for the currently selected item, or None."""
        items = self._current_list().selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    # ── Signals ──────────────────────────────────────────────────────────────

    def _on_tab_changed(self, _index):
        self._on_selection_changed()

    def _on_selection_changed(self):
        p = self._selected_pattern()
        if p is not None:
            self._name_label.setText(self.tr("Name: {0}").format(p['name'] or '—'))
            self._size_label.setText(self.tr("Size: {0} bytes").format(p['size']))
            self._action_btn.setEnabled(True)
        else:
            self._name_label.setText(self.tr("Name: —"))
            self._size_label.setText(self.tr("Size: —"))
            self._action_btn.setEnabled(False)

    def _on_item_double_clicked(self, _item):
        """Trigger the action on double-click when the button is enabled."""
        if self._action_btn.isEnabled():
            self._action_btn.click()

    # ── Actions ──────────────────────────────────────────────────────────────

    def _on_action(self):
        p = self._selected_pattern()
        if p is None:
            return

        if self._action in (self.ACTION_LOAD, self.ACTION_INSERT):
            self._do_load(p)
        elif self._action == self.ACTION_DELETE:
            self._do_delete(p)

    def _do_load(self, pattern):
        """Load stitch data from a memory card slot.

        Computes the absolute card slot from the pattern's slot index and the
        type-specific offset stored in ``self._card_info``, then calls
        :meth:`MachineComm.load_card_slot`.  On success the raw payload is
        stored in ``self.loaded_points`` / ``self.loaded_slot_type`` and the
        dialog is accepted so the caller can retrieve the data.
        """
        ptype    = pattern['pattern_type']
        offs_map = {'9mm': 'offs_9mm', 'MAXI': 'offs_maxi', 'Embroidery': 'offs_embr'}
        card_slot = pattern['slot'] + self._card_info.get(offs_map.get(ptype, ''), 0)

        self._action_btn.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.setStyleSheet("")

        def _load_progress(done, total):
            if total > 0:
                self._progress_bar.setValue(done * 100 // total)
            QApplication.processEvents()

        try:
            raw_data = self._comm.load_card_slot(
                self._card_info['card_no_bytes'],
                card_slot,
                ptype,
                total_size=pattern['size'],
                progress_callback=_load_progress,
            )
        except (MachineCommError, Exception) as exc:
            self._progress_bar.setValue(0)
            self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
            QMessageBox.critical(
                self,
                self.tr("Load Failed"),
                self.tr("Could not load the pattern from the card:")
                + "\n" + str(exc),
            )
            self._end_transmission()
            self.reject()
            return

        try:
            if ptype == '9mm':
                points = MachineComm.decode_card_pattern_9mm(raw_data)
            elif ptype == 'MAXI':
                points = MachineComm.decode_card_pattern_maxi(raw_data)
            else:
                self._progress_bar.setValue(0)
                self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
                self._action_btn.setEnabled(True)
                QMessageBox.information(
                    self,
                    self.tr("Not Yet Implemented"),
                    self.tr("Loading {0} patterns from memory card is not yet supported.").format(ptype),
                )
                return
        except MachineCommError as exc:
            self._progress_bar.setValue(0)
            self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
            QMessageBox.critical(
                self,
                self.tr("Decode Failed"),
                self.tr("Could not decode the pattern data:")
                + "\n" + str(exc),
            )
            self._end_transmission()
            self.reject()
            return

        self._progress_bar.setValue(0)
        self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
        self.loaded_points    = points
        self.loaded_slot_type = ptype
        self.loaded_name      = pattern['name']
        self._end_transmission()
        self.accept()

    def _do_delete(self, pattern):
        """Delete *pattern* from the memory card.

        Workflow:

        1. Ask the user to confirm.
        2. Send KL command via :meth:`MachineComm.delete_card_slot`.
        3. On CTRL_NAK / error → show error message, close dialog.
        4. On CTRL_ACK → re-query card index.
        5. If counts match expectations (card_no same, deleted type count -1,
           others unchanged) → fast path: remove item from list, update label.
        6. Otherwise → slow path: reload all preview images from the machine.
        """
        ptype = pattern['pattern_type']
        name  = pattern['name'] or '(unnamed)'

        # ── 1. Confirmation ───────────────────────────────────────────────
        ret = QMessageBox.question(
            self,
            self.tr("Confirm Delete"),
            self.tr("Delete pattern \"{0}\" ({1}) from the memory card?").format(name, ptype)
            + "\n" + 
            self.tr("This action cannot be undone."),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return

        old_card_info = dict(self._card_info)
        slot_byte = pattern['slot']

        # ── 2. Send KL delete command ────────────────────────────────────
        # Use the card-type offset from the card index to compute the
        # physical slot on the card (slots are reported relative to the
        # type's offset in the index).  This ensures we delete the correct
        # absolute slot on the machine.
        offs_map = {'9mm': 'offs_9mm', 'MAXI': 'offs_maxi', 'Embroidery': 'offs_embr'}
        card_slot = slot_byte + old_card_info.get(offs_map.get(ptype, ''), 0)
        try:
            self._comm.delete_card_slot(
                self._card_info['card_no_bytes'], card_slot, ptype
            )
        except (MachineCommError, Exception) as exc:
            QMessageBox.critical(
                self,
                self.tr("Delete Failed"),
                self.tr("Could not delete the pattern from the card:")
                + "\n" + str(exc),
            )
            self._end_transmission()
            self.reject()
            return

        # ── 3. Re-query card index ────────────────────────────────────────
        try:
            new_card_info = self._comm.query_card_index()
        except (MachineCommError, Exception) as exc:
            QMessageBox.critical(
                self,
                self.tr("Memory Card"),
                self.tr("Pattern deleted, but the card index could not be re-read:")
                + "\n" + str(exc),
            )
            self._end_transmission()
            self.reject()
            return

        # ── 4. Verify expected outcome ────────────────────────────────────
        count_key  = {'9mm': 'n_9mm', 'MAXI': 'n_maxi', 'Embroidery': 'n_embr'}[ptype]
        other_keys = [k for k in ('n_9mm', 'n_maxi', 'n_embr') if k != count_key]

        counts_ok = (
            new_card_info['card_no'] == old_card_info['card_no']
            and new_card_info[count_key] == old_card_info[count_key] - 1
            and all(new_card_info[k] == old_card_info[k] for k in other_keys)
        )

        if counts_ok:
            # ── 5. Fast path: remove item from the list widget only ───────
            # Match and remove by slot and pattern_type rather than object
            # identity — pattern dicts may not be the same object. Also
            # adjust the remaining patterns' slot numbers because slots are
            # not permanent: items after the deleted slot shift down by one.
            lw = self._lists[ptype]
            deleted_slot = slot_byte

            # Remove the corresponding QListWidgetItem by matching slot
            for i in range(lw.count()):
                item = lw.item(i)
                item_p = item.data(Qt.UserRole)
                if item_p and item_p.get('slot') == deleted_slot:
                    lw.takeItem(i)
                    break

            # Build updated patterns list: remove the deleted entry and
            # decrement slot numbers for same-type patterns coming after it.
            new_patterns = []
            for p in self._previews:
                if p.get('pattern_type') == ptype and p.get('slot') == deleted_slot:
                    # skip the deleted pattern
                    continue
                # If same type and slot is after deleted, shift it left
                if p.get('pattern_type') == ptype and p.get('slot', 0) > deleted_slot:
                    p['slot'] = p.get('slot', 0) - 1
                new_patterns.append(p)

            # Update the QListWidget items' UserRole data to point to the
            # updated pattern dicts for this ptype so future actions use
            # the correct slot numbers.
            # Collect patterns for this ptype in display order
            ptype_patterns = [p for p in new_patterns if p.get('pattern_type') == ptype]
            for idx in range(lw.count()):
                item = lw.item(idx)
                if idx < len(ptype_patterns):
                    item.setData(Qt.UserRole, ptype_patterns[idx])

            self._previews = new_patterns
        else:
            # ── 6. Slow path: reload all previews from the machine ────────
            try:
                self._reload_previews(new_card_info)
            except (MachineCommError, Exception) as exc:
                QMessageBox.critical(
                    self,
                    self.tr("Memory Card"),
                    self.tr("Failed to reload previews from the machine:")
                    + "\n" + str(exc),
                )
                self._end_transmission()
                self.reject()
                return

        self._card_info = new_card_info
        self._update_card_info_label(new_card_info)
        self._on_selection_changed()

    def _on_close(self):
        self._end_transmission()
        self.reject()

    def _end_transmission(self):
        """Send CTRL_EOT and close the port (safe to call more than once)."""
        if not self._transmission_ended:
            self._transmission_ended = True
            try:
                self._comm.end_transmission()
            except Exception:
                pass

    # ── Window close button (×) ──────────────────────────────────────────────

    def closeEvent(self, event):
        self._end_transmission()
        super().closeEvent(event)

    # ── Preview rendering ─────────────────────────────────────────────────────

    def _build_pixmap(self, preview_hex: str, pattern_type: str, is_embroidery: bool):
        """Decode the raw preview hex payload into a QPixmap.

        The preview is a column-major black-and-white bitmap.  Each column is
        ``col_height // 8`` bytes tall, where ``col_height`` is 24 for 9mm and
        48 for MAXI / Embroidery.  Within each byte, bit 7 (MSB) is the
        top-most pixel of that 8-pixel group; byte index 0 is the bottom-most
        group in the column.  Embroidery images are rotated 180° after decoding.

        Args:
            preview_hex (str): Hex-encoded payload from
                :meth:`MachineComm.query_card_preview`.
            pattern_type (str): ``'9mm'``, ``'MAXI'``, or ``'Embroidery'``.
            is_embroidery (bool): True when the pattern is an Embroidery type.

        Returns:
            QPixmap | None: Decoded pixmap, or ``None`` on empty / invalid input.
        """
        if not preview_hex:
            return None
        try:
            data = bytes.fromhex(preview_hex)
        except ValueError:
            return None

        col_height = 24 if pattern_type == "9mm" else 48
        bytes_per_col = col_height // 8  # 3 for 9mm, 6 for MAXI/Embroidery

        if len(data) < bytes_per_col:
            return None

        num_cols = len(data) // bytes_per_col
        img = QImage(num_cols, col_height, QImage.Format_RGB32)
        img.fill(QColor(255, 255, 255).rgb())
        black = QColor(0, 0, 0).rgb()

        for col in range(num_cols):
            for byte_idx in range(bytes_per_col):
                byte_val = data[col * bytes_per_col + byte_idx]
                # byte_idx=0 is the bottom-most 8-pixel group;
                # last byte_idx is the top group
                y_base = col_height - 8 - byte_idx * 8
                for bit in range(8):
                    # MSB (bit 7) is the topmost pixel of this 8-pixel segment
                    if (byte_val >> (7 - bit)) & 1:
                        img.setPixel(col, y_base + bit, black)

        if is_embroidery:
            img = img.transformed(QTransform().rotate(180))

        return QPixmap.fromImage(img)
