"""Card Memory dialog for browsing patterns stored on a machine memory card."""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QTabWidget, QWidget,
    QListWidget, QListWidgetItem,
    QPushButton, QLabel, QMessageBox,
    QApplication,
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon, QPixmap, QImage, QColor, QTransform

from machine_comm import MachineComm, MachineCommError


class CardMemoryDialog(QDialog):
    """Dialog for browsing and selecting patterns from a machine memory card.

    Layout::

        ┌─────────────────────────────────────────────────────┐
        │ Card No: 1002  |  9mm: 3  MAXI: 1  Embroidery: 2   │
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
            :meth:`MachineComm.query_card`::

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

        action (str): One of :attr:`ACTION_LOAD`, :attr:`ACTION_SEND`,
            :attr:`ACTION_INSERT`, :attr:`ACTION_DELETE`.
        comm (MachineComm): Open machine communication instance.
        parent: Parent widget.
    """

    ACTION_LOAD   = "load"
    ACTION_SEND   = "send"
    ACTION_INSERT = "insert"
    ACTION_DELETE = "delete"

    # Display scale applied to the raw preview pixmaps before use as icons.
    # 9mm images are 24 px tall; 4× gives 96 px.  MAXI/Embroidery are 48 px.
    _ICON_SIZE = QSize(200, 120)

    def __init__(self, card_info, patterns, action, comm, parent=None):
        super().__init__(parent)
        self._card_info = card_info
        self._patterns = patterns
        self._action = action
        self._comm = comm
        self._transmission_ended = False

        # Set by _do_load() / accepted when a pattern is successfully loaded
        self.loaded_points = None
        self.loaded_slot_type = None

        self._setup_ui()

    # ── UI construction ──────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle(self.tr("Card Memory"))
        self.resize(640, 500)

        outer = QVBoxLayout(self)
        outer.setSpacing(6)

        # ── Card summary ──────────────────────────────────────────────────
        card_no = self._card_info.get('card_no', 0)
        info_label = QLabel(
            self.tr("Card No: {0}   |   9mm: {1}   MAXI: {2}   Embroidery: {3}").format(
                card_no,
                self._card_info.get('n_9mm', 0),
                self._card_info.get('n_maxi', 0),
                self._card_info.get('n_embr', 0),
            )
        )
        outer.addWidget(info_label)

        # ── Tab widget (one per pattern type) ────────────────────────────
        self._tabs = QTabWidget()
        outer.addWidget(self._tabs, 1)

        self._lists = {}   # ptype → QListWidget
        for tab_name, ptype in (("9mm", "9mm"), ("MAXI", "MAXI"), ("Embroidery", "Embroidery")):
            tab_patterns = [p for p in self._patterns if p['pattern_type'] == ptype]

            tab_widget = QWidget()
            tab_layout = QVBoxLayout(tab_widget)
            tab_layout.setContentsMargins(4, 4, 4, 4)

            list_widget = QListWidget()
            list_widget.setViewMode(QListWidget.IconMode)
            list_widget.setIconSize(self._ICON_SIZE)
            list_widget.setResizeMode(QListWidget.Adjust)
            list_widget.setSpacing(10)
            list_widget.setSelectionMode(QListWidget.SingleSelection)
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

        # ── Pattern info area ─────────────────────────────────────────────
        info_row = QHBoxLayout()
        self._name_label = QLabel(self.tr("Name: —"))
        self._size_label = QLabel(self.tr("Size: —"))
        info_row.addWidget(self._name_label)
        info_row.addStretch()
        info_row.addWidget(self._size_label)
        outer.addLayout(info_row)

        # ── Bottom button row ─────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

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
        btn_row.addWidget(self._action_btn)

        close_btn = QPushButton(self.tr("Close"))
        close_btn.setMinimumWidth(80)
        close_btn.clicked.connect(self._on_close)
        btn_row.addWidget(close_btn)

        outer.addLayout(btn_row)

    # ── Helpers ──────────────────────────────────────────────────────────────

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
        elif self._action == self.ACTION_SEND:
            self._do_send(p)

    def _do_load(self, pattern):
        """Placeholder: loading stitch data from card memory is not yet implemented."""
        QMessageBox.information(
            self,
            self.tr("Not Yet Implemented"),
            self.tr(
                "Loading stitch data from card memory is not yet supported.\n"
                "This feature will be available in a future version."
            ),
        )

    def _do_delete(self, pattern):
        """Placeholder: deleting patterns from card memory is not yet implemented."""
        QMessageBox.information(
            self,
            self.tr("Not Yet Implemented"),
            self.tr(
                "Deleting patterns from card memory is not yet supported.\n"
                "This feature will be available in a future version."
            ),
        )

    def _do_send(self, pattern):
        """Placeholder: writing patterns to card memory is not yet implemented."""
        QMessageBox.information(
            self,
            self.tr("Not Yet Implemented"),
            self.tr(
                "Writing patterns to card memory is not yet supported.\n"
                "This feature will be available in a future version."
            ),
        )

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
