"""P-Memory dialog for displaying machine memory slots and triggering transfers."""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton, QLabel, QMessageBox,
    QProgressBar, QApplication,
)
from PyQt5.QtCore import Qt
from machine_comm import MachineComm, MachineCommError


class PMemoryDialog(QDialog):
    """Shows the machine P-Memory directory and an action button.

    Layout::

        ┌──────────────────────────────────────────┐
        │ Slot │ Type  │ Size (bytes)  │            │
        │──────┼───────┼───────────────│  [action]  │
        │  1   │ 9mm   │ 1024          │            │
        │  2   │ MAXI  │ 2048          │            │
        ├──────────────────────────────────────────┤
        │ Free memory: 5120 bytes                   │
        ├──────────────────────────────────────────┤
        │                              [Close]      │
        └──────────────────────────────────────────┘

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

    def __init__(self, pmem_info, action, comm, pattern=None, parent=None):
        super().__init__(parent)
        self._comm = comm
        self._action = action
        self._pattern = pattern
        self._pmem_info = pmem_info or {"num_slots": 0, "free_memory": 0, "slots": []}
        self._transmission_ended = False
        self.loaded_points = None
        self.loaded_slot_type = None

        self._setup_ui()

    # ── UI construction ──────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle("P-Memory")
        self.setFixedWidth(320)
        self.setMinimumHeight(340)

        outer = QVBoxLayout(self)
        outer.setSpacing(6)

        # ── Top area: table (left) + action button (right) ──
        top = QHBoxLayout()
        top.setSpacing(8)

        left = QVBoxLayout()
        left.setSpacing(4)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Slot", "Type", "Size"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._table.setColumnWidth(1, 60)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self._table.setColumnWidth(2, 80)
        self._table.itemSelectionChanged.connect(self._update_action_btn_state)

        left.addWidget(self._table)

        self._free_label = QLabel()
        left.addWidget(self._free_label)

        self._populate_table(self._pmem_info)

        top.addLayout(left)

        # ── Right: action button ──
        right = QVBoxLayout()
        right.setAlignment(Qt.AlignTop)

        if self._action == self.ACTION_SEND:
            self._action_btn = QPushButton("Write")
            self._action_btn.setEnabled(False)
            self._action_btn.clicked.connect(self._on_write)
            if self._pattern is not None:
                st = self._pattern.stitch_type
                n  = len(self._pattern.points)
                needed = n * 2 if st == "9mm" else n * 3 if st == "MAXI" else 0
            else:
                needed = 0
            mem_label = QLabel(f"Needed memory:\n{needed} bytes")
            mem_label.setAlignment(Qt.AlignCenter)
            right.addWidget(mem_label)
        elif self._action == self.ACTION_DELETE:
            self._action_btn = QPushButton("Delete")
            self._action_btn.setEnabled(False)
            self._action_btn.clicked.connect(self._on_delete)
        elif self._action == self.ACTION_LOAD:
            self._action_btn = QPushButton("Load")
            self._action_btn.setEnabled(False)
            self._action_btn.clicked.connect(self._on_load)
        else:
            # Insert – not yet implemented
            self._action_btn = QPushButton("Insert")
            self._action_btn.setEnabled(False)

        right.addWidget(self._action_btn)
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

        close_btn = QPushButton("Close")
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

    def _update_action_btn_state(self):
        """Enable the action button based on the current selection."""
        if self._action == self.ACTION_SEND:
            self._action_btn.setEnabled(self._selected_slot_index() is not None)
        elif self._action in (self.ACTION_DELETE, self.ACTION_LOAD, self.ACTION_INSERT):
            self._action_btn.setEnabled(self._selected_slot_size() > 0)

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
            self._table.setItem(row, 0, slot_item)
            self._table.setItem(row, 1, type_item)
            self._table.setItem(row, 2, size_item)
        self._free_label.setText(f"Free memory: {pmem_info.get('free_memory', 0)} bytes")
    # ── Actions ──────────────────────────────────────────────────────────────

    def _on_write(self):
        """Send the pattern to the machine (command → header → stitch data)."""
        if self._pattern is None:
            QMessageBox.warning(self, "P-Memory", "No pattern data to send.")
            return
        slot_index = self._selected_slot_index()
        if slot_index is None:
            return

        # If the selected slot is not empty, ask the user to clear it first.
        if self._selected_slot_size() > 0:
            answer = QMessageBox.question(
                self,
                "Slot Not Empty",
                f"Slot {slot_index} already contains data.\n"
                "Delete it before writing?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return  # Keep dialog open

            # Delete the slot first.
            try:
                self._comm.delete_pmemory_slot(slot_index)
            except Exception as exc:
                QMessageBox.critical(self, "Machine Error", str(exc))
                return

            # Refresh the table so the slot shows as empty.
            try:
                raw = self._comm.query_pmemory()
                pmem_info = MachineComm.decode_pmemory(raw, "")
            except Exception as exc:
                QMessageBox.critical(
                    self, "Machine Error",
                    f"Failed to refresh P-Memory after delete:\n{exc}"
                )
                self._end_transmission()
                self.reject()
                return
            self._pmem_info = pmem_info
            self._populate_table(pmem_info)

        self._action_btn.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.setStyleSheet("")

        def _send_progress(done, total):
            if total > 0:
                self._progress_bar.setValue(done * 100 // total)
            QApplication.processEvents()

        try:
            self._comm.send_pmemory_slot(
                slot_index, self._pattern, progress_callback=_send_progress
            )
        except Exception as exc:
            self._progress_bar.setValue(0)
            self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
            self._end_transmission()
            self.reject()
            QMessageBox.critical(
                self.parent(),
                "Machine Error",
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

        self._action_btn.setEnabled(False)
        try:
            self._comm.delete_pmemory_slot(slot_index)
        except Exception as exc:
            self._action_btn.setEnabled(True)
            QMessageBox.critical(self, "Machine Error", str(exc))
            return

        # Refresh P-Memory info from the machine
        try:
            raw = self._comm.query_pmemory()
            pmem_info = MachineComm.decode_pmemory(raw, "")
        except Exception as exc:
            QMessageBox.critical(self, "Machine Error", f"Error during communication")
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
            self._progress_bar.setValue(0)
            self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
            self._action_btn.setEnabled(True)
            QMessageBox.critical(self, "Machine Error", str(exc))
            return

        try:
            points = MachineComm.decode_machine_pattern(raw_data, slot_type)
        except Exception as exc:
            self._progress_bar.setValue(0)
            self._progress_bar.setStyleSheet(self._PROGRESS_BAR_HIDDEN_STYLE)
            self._end_transmission()
            self.reject()
            QMessageBox.critical(
                self.parent(), "Machine Error",
                f"Failed to decode pattern: {exc}"
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
