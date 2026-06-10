"""P-Memory dialog for displaying machine memory slots and triggering transfers."""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton, QLabel, QMessageBox,
    QProgressBar, QApplication, QWidget, QSizePolicy,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QPen, QBrush, QColor
from machine_comm import MachineComm, MachineCommError


class _PatternPreviewWidget(QWidget):
    """Simple widget that renders a list of (x, y) stitch points."""

    _MARGIN = 16

    def __init__(self, points, stitch_type, parent=None):
        super().__init__(parent)
        self._points = points
        self._stitch_type = stitch_type
        self.setMinimumSize(400, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), Qt.white)

        if not self._points:
            painter.drawText(self.rect(), Qt.AlignCenter, "No points to display.")
            return

        xs = [p[0] for p in self._points]
        ys = [p[1] for p in self._points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        draw_w = self.width() - 2 * self._MARGIN
        draw_h = self.height() - 2 * self._MARGIN

        span_x = max_x - min_x or 1
        span_y = max_y - min_y or 1

        scale = min(draw_w / span_x, draw_h / span_y)

        # Centre the pattern within the available drawing area
        offset_x = self._MARGIN + (draw_w - span_x * scale) / 2
        offset_y = self._MARGIN + (draw_h - span_y * scale) / 2

        def to_screen(x, y):
            # Machine y=0 is bottom; screen y=0 is top — invert y.
            return (
                int(offset_x + (x - min_x) * scale),
                int(offset_y + (max_y - y) * scale),
            )

        # Connecting lines
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        for i in range(1, len(self._points)):
            x1, y1 = to_screen(*self._points[i - 1])
            x2, y2 = to_screen(*self._points[i])
            painter.drawLine(x1, y1, x2, y2)

        # Stitch points
        # r = 3
        # painter.setPen(Qt.NoPen)
        # for i, pt in enumerate(self._points):
        #     sx, sy = to_screen(*pt)
        #     if i == 0:
        #         painter.setBrush(QBrush(QColor(0, 180, 0)))
        #     elif i == len(self._points) - 1:
        #         painter.setBrush(QBrush(QColor(200, 0, 0)))
        #     else:
        #         painter.setBrush(QBrush(QColor(0, 0, 0)))
        #     painter.drawEllipse(sx - r, sy - r, 2 * r, 2 * r)


class _PatternPreviewDialog(QDialog):
    """Simple read-only preview of a loaded P-Memory slot."""

    def __init__(self, points, stitch_type, slot_index, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Preview - Slot {slot_index} ({stitch_type})")
        self.resize(640, 360)

        layout = QVBoxLayout(self)
        self._preview = _PatternPreviewWidget(points, stitch_type, self)
        layout.addWidget(self._preview, 1)

        info = QLabel(self.tr("{0} stitch points").format(len(points)))
        info.setAlignment(Qt.AlignCenter)
        layout.addWidget(info)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


class PMemoryDialog(QDialog):
    """Shows the machine P-Memory directory and an action button.

    Layout::

        ┌───────────────────────────────────────────┐
        │ Slot │ Type  │ Size (bytes)  │            │
        │──────┼───────┼───────────────│  [action]  │
        │  1   │ 9mm   │ 1024          │            │
        │  2   │ MAXI  │ 2048          │            │
        ├───────────────────────────────────────────┤
        │ Free memory: 5120 bytes                   │
        ├───────────────────────────────────────────┤
        │  [Progressbar]               [Close]      │
        └───────────────────────────────────────────┘

    Args:
        pmem_info (dict | None): Decoded P-Memory data::

                {
                    'num_slots':   int,
                    'free_memory': int,
                    'slots': [{'type': '9mm' | 'MAXI', 'size': int}, ...],
                }

            Pass ``None`` to show an empty table.
        action (str): One of ``'send'``, ``'load'``, ``'insert'``, ``'delete'``.
        comm (MachineComm): Open machine communication instance.
        pattern_bytes (bytes | None): Serialised pattern data used by the
            ``'send'`` action.  Ignored for other actions.
        parent: Parent widget.
    """

    ACTION_SEND = "send"
    ACTION_LOAD = "load"
    ACTION_INSERT = "insert"
    ACTION_DELETE = "delete"

    _PROGRESS_BAR_HIDDEN_STYLE = (
        "QProgressBar { background: transparent; border: none; color: transparent; }"
        "QProgressBar::chunk { background: transparent; }"
    )

    def __init__(self, pmem_info, action, comm, machine_model, pattern=None, parent=None):
        super().__init__(parent)
        self._comm = comm
        self._action = action
        self._machine_model = machine_model
        self._pattern = pattern
        self._pmem_info = pmem_info or {"num_slots": 0, "free_memory": 0, "slots": []}
        self._transmission_ended = False
        self.loaded_points = None
        self.loaded_slot_type = None

        # Pre-compute the actual byte count that will be sent to the machine.
        # encode_machine_stitch_data accounts for auto-stitches and any MAXI
        # intermediate stitches that _translate_maxi_points may insert.
        if self._action == self.ACTION_SEND and self._pattern is not None:
            try:
                _, final_points = MachineComm.encode_pmemory_stitch_data(self._pattern)
                st = self._pattern.stitch_type
                self._needed_bytes = (len(final_points) * 2 if st == "9mm"
                                      else len(final_points) * 3 if st == "MAXI"
                                      else 0)
            except Exception:
                self._needed_bytes = 0
        else:
            self._needed_bytes = 0

        self._setup_ui()

    # ── UI construction ──────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle(self.tr("P-Memory"))
        self.setFixedWidth(320)
        self.setMinimumHeight(340)
        # Open with height of 500px
        self.resize(self.width(), 500)

        outer = QVBoxLayout(self)
        outer.setSpacing(6)

        # ── Top area: table (left) + action button (right) ──
        top = QHBoxLayout()
        top.setSpacing(8)

        left = QVBoxLayout()
        left.setSpacing(4)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([self.tr("Slot"), self.tr("Type"), self.tr("Size")])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(16)
        self._table.verticalHeader().setMinimumSectionSize(16)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._table.setColumnWidth(1, 60)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self._table.setColumnWidth(2, 80)
        self._table.itemSelectionChanged.connect(self._update_action_btn_state)
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)

        left.addWidget(self._table)

        self._free_label = QLabel()
        left.addWidget(self._free_label)

        self._populate_table(self._pmem_info)

        top.addLayout(left)

        # ── Right: action button ──
        right = QVBoxLayout()
        right.setAlignment(Qt.AlignTop)

        if self._action == self.ACTION_SEND:
            self._action_btn = QPushButton(self.tr("Write"))
            self._action_btn.setEnabled(False)
            self._action_btn.clicked.connect(self._on_write)
            mem_label = QLabel(self.tr("Needed memory:") + "\n" + self.tr("{0} bytes").format(self._needed_bytes))
            mem_label.setAlignment(Qt.AlignCenter)
            right.addWidget(mem_label)
        elif self._action == self.ACTION_DELETE:
            self._action_btn = QPushButton(self.tr("Delete"))
            self._action_btn.setEnabled(False)
            self._action_btn.clicked.connect(self._on_delete)
        elif self._action == self.ACTION_LOAD:
            self._action_btn = QPushButton(self.tr("Load"))
            self._action_btn.setEnabled(False)
            self._action_btn.clicked.connect(self._on_load)
        else:
            # Insert – reuses the same machine-read logic as Load
            self._action_btn = QPushButton(self.tr("Insert"))
            self._action_btn.setEnabled(False)
            self._action_btn.clicked.connect(self._on_load)

        right.addWidget(self._action_btn)

        self._preview_btn = QPushButton(self.tr("Preview"))
        self._preview_btn.setEnabled(False)
        self._preview_btn.clicked.connect(self._on_preview)
        right.addWidget(self._preview_btn)

        top.addLayout(right)

        outer.addLayout(top)

        # ── Bottom: progress bar (left) + Close button (right) ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setMaximumHeight(18)
        self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
        btn_row.addWidget(self._progress_bar, 1)

        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self._on_close)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

    # ── Slot helpers ─────────────────────────────────────────────────────────

    def _selected_slot_index(self):
        """Return the 0-based index of the selected row, or None."""
        rows = self._table.selectionModel().selectedRows()
        return rows[0].row() if rows else None
    def _selected_slot_size(self):
        """Return the stitch count of the selected slot, or 0."""
        row = self._selected_slot_index()
        if row is None:
            return 0
        item = self._table.item(row, 2)  # "Size (stitches)" column
        try:
            return int(item.text()) if item else 0
        except ValueError:
            return 0

    def _on_row_double_clicked(self, _row, _col):
        """Trigger the action on double-click if the button is currently enabled."""
        if self._action_btn.isEnabled():
            self._action_btn.click()

    def _update_action_btn_state(self):
        """Enable the action button and Preview button based on the current selection."""
        has_non_empty_slot = self._selected_slot_size() > 0
        if self._action == self.ACTION_SEND:
            self._action_btn.setEnabled(self._selected_slot_index() is not None)
        elif self._action in (self.ACTION_DELETE, self.ACTION_LOAD, self.ACTION_INSERT):
            self._action_btn.setEnabled(has_non_empty_slot)
        self._preview_btn.setEnabled(has_non_empty_slot)

    def _populate_table(self, pmem_info):
        """Fill the table with slot data from pmem_info and update the free-memory label."""
        slots = pmem_info.get("slots", [])
        self._table.setRowCount(len(slots))
        for row, slot in enumerate(slots):
            slot_item = QTableWidgetItem(str(row))
            slot_item.setTextAlignment(Qt.AlignCenter)
            type_item = QTableWidgetItem(slot.get("type") or "---")
            type_item.setTextAlignment(Qt.AlignCenter)
            size_item = QTableWidgetItem(str(slot.get("size", 0)))
            size_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 0, QTableWidgetItem("P" + str(row)))
            self._table.setItem(row, 1, type_item)
            self._table.setItem(row, 2, size_item)
        self._free_label.setText(self.tr("Free memory: {0} bytes").format(pmem_info.get('free_memory', 0)))
    # ── Actions ──────────────────────────────────────────────────────────────

    def _on_preview(self):
        """Load the selected slot and display it in a temporary preview window."""
        slot_index = self._selected_slot_index()
        if slot_index is None:
            return

        slot_info = self._pmem_info['slots'][slot_index]
        slot_type = slot_info.get('type')
        if not slot_type:
            return
        slot_size = slot_info.get('size', 0)

        self._preview_btn.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.setStyleSheet("")

        def _load_progress(done, total):
            if total > 0:
                self._progress_bar.setValue(done * 100 // total)
            QApplication.processEvents()

        try:
            raw_data = self._comm.load_pmemory_slot(
                slot_index, slot_type,
                total_size=slot_size, progress_callback=_load_progress,
            )
        except Exception as exc:
            self._comm._log_error(str(exc))
            self._progress_bar.setValue(0)
            self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
            self._preview_btn.setEnabled(True)
            QMessageBox.critical(self, self.tr("Error"), str(exc))
            return

        self._progress_bar.setValue(0)
        self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
        self._preview_btn.setEnabled(True)

        try:
            points = MachineComm.decode_pmemory_pattern(raw_data, slot_type)
        except Exception as exc:
            self._comm._log_error(str(exc))
            QMessageBox.critical(self, self.tr("Error"), self.tr("Failed to decode pattern:") + "\n" + str(exc))
            return

        _PatternPreviewDialog(points, slot_type, slot_index, parent=self).exec_()

    def _on_write(self):
        """Send the pattern to the machine (command → header → stitch data)."""
        if self._pattern is None:
            QMessageBox.warning(self, self.tr("P-Memory"), self.tr("No pattern data to send."))
            return
        slot_index = self._selected_slot_index()
        if slot_index is None:
            return

        # MAXI stitches are not yet supported for PFAFF Creative 1475 CD.
        if "1475" in self._machine_model and self._pattern.stitch_type == "MAXI":
            QMessageBox.critical(
                self, self.tr("Not Supported"),
                self.tr("Sending MAXI stitches is not yet implemented for PFAFF Creative 1475 CD")
            )
            return

        # If the selected slot is not empty, ask the user to clear it first.
        if self._selected_slot_size() > 0:
            answer = QMessageBox.question(
                self,
                self.tr("Slot Not Empty"),
                self.tr("Slot {0} already contains data.").format(slot_index) + "\n" + self.tr("Delete it before writing?"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return  # Keep dialog open

            # Delete the slot first.
            try:
                self._comm.delete_pmemory_slot(slot_index)
            except Exception as exc:
                self._comm._log_error(str(exc))
                QMessageBox.critical(self, self.tr("Error"), str(exc))
                return

            # Refresh the table so the slot shows as empty.
            try:
                raw = self._comm.query_pmemory_index()
                pmem_info = MachineComm.decode_pmemory_index(raw, self._machine_model)
            except Exception as exc:
                self._comm._log_error(str(exc))
                QMessageBox.critical(
                    self, self.tr("Error"),
                    self.tr("Failed to refresh P-Memory after delete:") + "\n" + str(exc)
                )
                self._end_transmission()
                self.reject()
                return
            self._pmem_info = pmem_info
            self._populate_table(pmem_info)

        # Check available free memory before attempting to write.
        free = self._pmem_info.get('free_memory', 0)
        if self._needed_bytes > free:
            QMessageBox.warning(
                self, self.tr("Insufficient Memory"),
                self.tr("The pattern requires {0} bytes but only {1} bytes are free in P-Memory.").format(self._needed_bytes, free)
                + "\n\n" + 
                self.tr("Please delete one or more slots to free up space.")
            )
            return

        self._action_btn.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.setStyleSheet("")

        def _send_progress(done, total):
            if total > 0:
                self._progress_bar.setValue(done * 100 // total)
            QApplication.processEvents()

        try:
            self._comm.send_pmemory_slot(
                slot_index, self._pattern, machine_model=self._machine_model, progress_callback=_send_progress
            )
        except Exception as exc:
            self._comm._log_error(str(exc))
            self._progress_bar.setValue(0)
            self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
            self._end_transmission()
            self.reject()
            QMessageBox.critical(
                self.parent(),
                "Error",
                str(exc),
            )
            return

        self._end_transmission()
        self.accept()

    def _on_delete(self):
        """Delete the selected P-Memory slot and refresh the table."""
        slot_index = self._selected_slot_index()
        if slot_index is None:
            return

        answer = QMessageBox.question(
            self,
            self.tr("Confirm Delete"),
            self.tr("Delete pattern {0} from P-Memory?").format(slot_index),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        self._action_btn.setEnabled(False)
        try:
            self._comm.delete_pmemory_slot(slot_index)
        except Exception as exc:
            self._comm._log_error(str(exc))
            self._action_btn.setEnabled(True)
            QMessageBox.critical(self, self.tr("Error"), str(exc))
            return

        # Refresh P-Memory info from the machine
        try:
            raw = self._comm.query_pmemory_index()
            pmem_info = MachineComm.decode_pmemory_index(raw, self._machine_model)
        except Exception as exc:
            self._comm._log_error(str(exc))
            QMessageBox.critical(self, self.tr("Error"), self.tr("Error during communication"))
            self._end_transmission()
            self.reject()
            return

        self._pmem_info = pmem_info
        self._populate_table(pmem_info)
        self._update_action_btn_state()

    def _on_load(self):
        """Load the selected P-Memory slot and return the decoded points to the caller."""
        slot_index = self._selected_slot_index()
        if slot_index is None:
            return

        slot_type = self._pmem_info['slots'][slot_index].get('type')
        if not slot_type:
            return

        slot_size = self._pmem_info['slots'][slot_index].get('size', 0)

        self._action_btn.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.setStyleSheet("")

        def _load_progress(done, total):
            if total > 0:
                self._progress_bar.setValue(done * 100 // total)
            QApplication.processEvents()

        try:
            raw_data = self._comm.load_pmemory_slot(
                slot_index, slot_type,
                total_size=slot_size, progress_callback=_load_progress,
            )
        except Exception as exc:
            self._comm._log_error(str(exc))
            self._progress_bar.setValue(0)
            self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
            self._action_btn.setEnabled(True)
            QMessageBox.critical(self, self.tr("Error"), str(exc))
            return

        try:
            points = MachineComm.decode_pmemory_pattern(raw_data, slot_type)
        except Exception as exc:
            self._comm._log_error(str(exc))
            self._progress_bar.setValue(0)
            self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
            self._end_transmission()
            self.reject()
            QMessageBox.critical(
                self.parent(), self.tr("Error"),
                self.tr("Failed to decode pattern:") + "\n" + str(exc)
            )
            return

        self.loaded_points = points
        self.loaded_slot_type = slot_type
        self._end_transmission()
        self.accept()

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
