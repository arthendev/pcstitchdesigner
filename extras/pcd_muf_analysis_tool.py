#!/usr/bin/env python3
"""
PCD .MUF Files Analysis Tool
Parses PCDPARA3.MUF (index&parameters file), PCDKOOR3.MUF (coordinate file)
and BILDER.MUF (preview images) to extract machine stitch patterns and display
them in a GUI. These are binary files from PCD 2.2.

Format:
  PCDPARA3.MUF → read in 116-byte chunks.
    Bytes 4–6 (0-indexed): 3-byte little-endian offset into PCDKOOR3.MUF

  PCDKOOR3.MUF at that offset:
    Bytes 0     → pattern_y0  (1 byte)
    Bytes 1     → pattern_yn  (1 byte)
    Bytes 2–3   → pattern_length (2-byte little-endian)
    Bytes 4…    → pattern_dxdy (2 × pattern_length bytes)

  BILDER.MUF → 48×48 px B&W preview images, 288 bytes each.
    Bytes encode 8 pixels each (MSB = leftmost pixel), row-wise top-left.
"""

import sys
import os
import json
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QStatusBar, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QDialog, QDialogButtonBox, QTextEdit, QSplitter, QGroupBox,
    QFormLayout, QMessageBox, QCheckBox, QScrollArea, QToolTip,
    QLayout,
)
from PyQt5.QtCore import Qt, QRectF, QPointF, QSizeF
from PyQt5.QtGui import QFont, QColor, QPainter, QPen, QPainterPath, QBrush, QPixmap


# ═══════════════════════════════════════════════════════════════════════════
# Thumbnail renderer (used by both preview widget and main table)
# ═══════════════════════════════════════════════════════════════════════════

THUMB_W, THUMB_H = 210, 48

# Coordinate pairs to skip when drawing (unsigned byte values)
_SKIP_PAIRS = {(0x00, 0x48), (0x00, 0x42)}


def render_thumbnail(dxdy_bytes: list[int], pattern_y0: int) -> QPixmap:
    """Render a tiny stitch-pattern preview as a QPixmap."""
    pix = QPixmap(THUMB_W, THUMB_H)
    pix.fill(QColor(255, 255, 255))

    if not dxdy_bytes:
        return pix

    signed = [b if b < 128 else b - 256 for b in dxdy_bytes]
    y = float(pattern_y0)
    x = 0.0
    points = [(x, y)]
    for i in range(0, len(signed) - 1, 2):
        dx_s, dy_s = signed[i], signed[i + 1]
        # Convert back to unsigned for skip check
        dx_u = dx_s if dx_s >= 0 else dx_s + 256
        dy_u = dy_s if dy_s >= 0 else dy_s + 256
        if (dx_u, dy_u) in _SKIP_PAIRS:
            continue
        x += dx_s
        y += dy_s
        points.append((x, y))

    if len(points) < 2:
        return pix

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x == min_x:
        max_x = min_x + 1
    if max_y == min_y:
        max_y = min_y + 1

    margin = 3
    draw_w = THUMB_W - 2 * margin
    draw_h = THUMB_H - 2 * margin
    scale = min(draw_w / (max_x - min_x), draw_h / (max_y - min_y))
    ox = margin + (draw_w - (max_x - min_x) * scale) / 2.0
    oy = margin + draw_h

    def tx(px: float, py: float):
        return (ox + (px - min_x) * scale,
                oy - (py - min_y) * scale)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)

    # Line
    painter.setPen(QPen(QColor(0, 0, 0), 1))
    for i in range(len(points) - 1):
        x1, y1 = tx(*points[i])
        x2, y2 = tx(*points[i + 1])
        painter.drawLine(int(x1), int(y1), int(x2), int(y2))

    painter.end()
    return pix


# ═══════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════

PARA_CHUNK_SIZE = 116        # bytes per entry in the index file
PARA_OFFSET_BYTE_IDX = 4    # byte position of the 3-byte LE offset

# BILDER.MUF image format
BILDER_BYTES_PER_IMAGE = 288   # 48 × 48 ÷ 8
BILDER_IMG_SIZE = 48


def bilder_to_image(data: bytes) -> 'QImage':
    """Convert 288 bytes of B&W bitmap into a QImage (48×48)."""
    from PyQt5.QtGui import QImage
    img = QImage(BILDER_IMG_SIZE, BILDER_IMG_SIZE, QImage.Format_Mono)
    img.fill(0)
    byte_idx = 0
    for y in range(BILDER_IMG_SIZE):
        for x_byte in range(BILDER_IMG_SIZE // 8):
            if byte_idx >= len(data):
                break
            b = data[byte_idx]
            byte_idx += 1
            for bit in range(8):
                px = x_byte * 8 + bit
                if px >= BILDER_IMG_SIZE:
                    break
                if b & (0x80 >> bit):
                    img.setPixel(px, y, 1)
    return img


# ═══════════════════════════════════════════════════════════════════════════
# Parsing logic
# ═══════════════════════════════════════════════════════════════════════════

def parse_files(para_path: str, koor_path: str) -> list[dict]:
    """
    Parse both files and return a list of pattern dictionaries.

    Each dict has:
      'index'          – 0-based pattern number
      'para_raw'       – raw 116 bytes from PCDPARA3 (list of ints)
      'para_offset'    – decoded 3-byte LE offset
      'pattern_y0'     – 1 raw byte from PCDKOOR3 at para_offset
      'pattern_yn'     – 1 raw byte from PCDKOOR3 at para_offset+1
      'pattern_length' – decoded 2-byte LE stitch length
      'pattern_dxdy'   – list of bytes (2 × pattern_length)
      'is_ref_table'   – True if para_offset==0 (reference table entry)
      'sublist'        – list of sub-pattern dicts (only for ref-table entries)
    """
    with open(para_path, 'rb') as f:
        para_data = f.read()

    with open(koor_path, 'rb') as f:
        koor_data = f.read()

    koor_len = len(koor_data)
    total_chunks = len(para_data) // PARA_CHUNK_SIZE
    patterns: list[dict] = []

    # ── helper: decode a single pattern from KOOR at a given offset ──
    def _decode_one(offset: int, sub_idx: int = -1) -> dict:
        sp: dict = {
            'sub_index': sub_idx,
            'para_offset': offset,
            'pattern_y0': 0,
            'pattern_yn': 0,
            'pattern_length': 0,
            'pattern_dxdy': [],
        }
        if offset + 4 <= koor_len:
            sp['pattern_y0'] = koor_data[offset]
            sp['pattern_yn'] = koor_data[offset + 1]
            len_lo = koor_data[offset + 2]
            len_hi = koor_data[offset + 3]
            sp['pattern_length'] = (len_hi << 8) | len_lo
            dxdy_start = offset + 4
            dxdy_end = dxdy_start + 2 * (sp['pattern_length'] - 1)
            if dxdy_end <= koor_len:
                sp['pattern_dxdy'] = list(koor_data[dxdy_start:dxdy_end])
        return sp

    for idx in range(total_chunks):
        chunk_start = idx * PARA_CHUNK_SIZE
        chunk = para_data[chunk_start:chunk_start + PARA_CHUNK_SIZE]

        # Decode the 3-byte little-endian offset from bytes 4-6
        b4, b5, b6 = chunk[PARA_OFFSET_BYTE_IDX:PARA_OFFSET_BYTE_IDX + 3]
        para_offset = b4 | (b5 << 8) | (b6 << 16)

        # Extract bytes 1-2 of para_raw individually
        para_scale_1 = chunk[1]
        para_scale_2 = chunk[2]

        entry: dict = {
            'index': idx,
            'para_raw': list(chunk),
            'para_offset': para_offset,
            'para_scale_1': para_scale_1,
            'para_scale_2': para_scale_2,
            'pattern_y0': 0,
            'pattern_yn': 0,
            'pattern_length': 0,
            'pattern_dxdy': [],
            'is_ref_table': False,
            'sublist': [],
        }

        if para_offset == 0 and koor_len >= 0x3B4:
            # ── Reference table at 0x000000 .. 0x0003B3 ──
            entry['is_ref_table'] = True
            ref_end = 0x3B4  # exclusive
            for ref_off in range(0, ref_end, 4):
                lo = koor_data[ref_off]
                hi = koor_data[ref_off + 1]
                sub_offset = lo | (hi << 8)
                if sub_offset > 0 and sub_offset + 4 <= koor_len:
                    sp = _decode_one(sub_offset, sub_idx=len(entry['sublist']))
                    entry['sublist'].append(sp)
            # Also set top-level fields from the first sub-pattern for display
            if entry['sublist']:
                first = entry['sublist'][0]
                entry['pattern_y0'] = first['pattern_y0']
                entry['pattern_yn'] = first['pattern_yn']
                entry['pattern_length'] = first['pattern_length']
                entry['pattern_dxdy'] = first['pattern_dxdy']
            entry['span_x'], entry['span_y'], entry['stitch_type'] = \
                compute_span_and_type(entry['pattern_dxdy'], entry['pattern_y0'])

        elif para_offset + 4 <= koor_len:
            sp = _decode_one(para_offset)
            entry['pattern_y0'] = sp['pattern_y0']
            entry['pattern_yn'] = sp['pattern_yn']
            entry['pattern_length'] = sp['pattern_length']
            entry['pattern_dxdy'] = sp['pattern_dxdy']

        entry['span_x'], entry['span_y'], entry['stitch_type'] = \
            compute_span_and_type(entry['pattern_dxdy'], entry['pattern_y0'])

        patterns.append(entry)

    return patterns


def compute_span_and_type(dxdy_bytes: list[int], pattern_y0: int):
    """Return (span_x, span_y, stitch_type) from dx/dy data."""
    if not dxdy_bytes:
        return 0, 0, "MAXI"
    signed = [b if b < 128 else b - 256 for b in dxdy_bytes]
    x, y = 0.0, float(pattern_y0)
    xs, ys = [x], [y]
    for i in range(0, len(signed) - 1, 2):
        dx_s, dy_s = signed[i], signed[i + 1]
        dx_u = dx_s if dx_s >= 0 else dx_s + 256
        dy_u = dy_s if dy_s >= 0 else dy_s + 256
        if (dx_u, dy_u) in _SKIP_PAIRS:
            continue
        x += dx_s
        y += dy_s
        xs.append(x)
        ys.append(y)
    span_x = int(max(xs) - min(xs))
    span_y = int(max(ys) - min(ys))
    
    if span_x <= 198 and span_y <= 54:
        stitch_type = "9mm"
    elif span_y <= 54:
        stitch_type = "9mm+"
    else:
        stitch_type = "MAXI"
        
    return span_x, span_y, stitch_type


# ═══════════════════════════════════════════════════════════════════════════
# Pattern preview widget
# ═══════════════════════════════════════════════════════════════════════════

class PatternPreviewWidget(QWidget):
    """Draws a stitch pattern as connected line segments from dx/dy pairs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(150, 100)
        self.setSizePolicy(
            self.sizePolicy().horizontalPolicy(),
            self.sizePolicy().verticalPolicy(),
        )
        self._points: list[QPointF] = []
        self._bbox = QRectF(0, 0, 1, 1)
        self._show_points = True

    def set_pattern(self, dxdy_bytes: list[int], pattern_y0: int):
        """Convert signed dx/dy bytes into accumulated points and repaint."""
        self._points.clear()
        if not dxdy_bytes:
            self._bbox = QRectF(0, 0, 1, 1)
            self.update()
            return

        # Convert to signed: 0..127 → +0..+127, 128..255 → −128..−1
        signed = [b if b < 128 else b - 256 for b in dxdy_bytes]

        # Starting Y from pattern_y0
        x, y = 0.0, float(pattern_y0)
        self._points.append(QPointF(x, y))

        for i in range(0, len(signed) - 1, 2):
            dx_s, dy_s = signed[i], signed[i + 1]
            dx_u = dx_s if dx_s >= 0 else dx_s + 256
            dy_u = dy_s if dy_s >= 0 else dy_s + 256
            if (dx_u, dy_u) in _SKIP_PAIRS:
                continue
            x += dx_s
            y += dy_s
            self._points.append(QPointF(x, y))

        # Compute bounding box
        if self._points:
            xs = [pt.x() for pt in self._points]
            ys = [pt.y() for pt in self._points]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            # Prevent zero-size bbox
            if max_x == min_x:
                max_x = min_x + 1
            if max_y == min_y:
                max_y = min_y + 1
            self._bbox = QRectF(min_x, min_y, max_x - min_x, max_y - min_y)

        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if len(self._points) < 2:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        margin = 20
        draw_w = w - 2 * margin
        draw_h = h - 2 * margin

        if draw_w <= 0 or draw_h <= 0:
            painter.end()
            return

        # Scale points to fit widget (uniform scale, Y flipped)
        sx = draw_w / self._bbox.width()
        sy = draw_h / self._bbox.height()
        scale = min(sx, sy)

        offset_x = margin + (draw_w - self._bbox.width() * scale) / 2.0
        # Y is flipped; pattern always fills full draw_h vertically
        offset_y = margin + draw_h

        def tx(pt: QPointF) -> QPointF:
            return QPointF(
                offset_x + (pt.x() - self._bbox.x()) * scale,
                offset_y - (pt.y() - self._bbox.y()) * scale,
            )

        # Background
        painter.fillRect(self.rect(), QColor(255, 255, 255))

        # Grid (light gray)
        pen_grid = QPen(QColor(230, 230, 230), 1)
        painter.setPen(pen_grid)
        for gx in range(margin, w - margin + 1, 40):
            painter.drawLine(gx, margin, gx, h - margin)
        for gy in range(margin, h - margin + 1, 40):
            painter.drawLine(margin, gy, w - margin, gy)

        # Bounding box border
        pen_bbox = QPen(QColor(180, 180, 180), 1)
        painter.setPen(pen_bbox)
        tl = tx(QPointF(self._bbox.x(), self._bbox.y()))
        br = tx(QPointF(self._bbox.x() + self._bbox.width(),
                         self._bbox.y() + self._bbox.height()))
        painter.drawRect(QRectF(tl, br).normalized())

        # Stitch line
        pen_line = QPen(QColor(0, 0, 0), 2)
        painter.setPen(pen_line)
        path = QPainterPath()
        pt0 = tx(self._points[0])
        path.moveTo(pt0)
        for pt in self._points[1:]:
            path.lineTo(tx(pt))
        painter.drawPath(path)

        # Points (small black dots) — only when enabled
        if self._show_points:
            pen_dot = QPen(QColor(0, 0, 0), 1)
            brush_dot = QBrush(QColor(0, 0, 0))
            painter.setPen(pen_dot)
            painter.setBrush(brush_dot)
            for pt in self._points:
                t = tx(pt)
                painter.drawEllipse(t, 2, 2)

            # Start point (green, on top)
            if self._points:
                painter.setBrush(QBrush(QColor(80, 220, 80)))
                painter.setPen(QPen(QColor(80, 220, 80), 1))
                t0 = tx(self._points[0])
                painter.drawEllipse(t0, 3, 3)

            # End point (red, always on top)
            if len(self._points) > 1:
                painter.setBrush(QBrush(QColor(220, 60, 60)))
                painter.setPen(QPen(QColor(220, 60, 60), 1))
                t_end = tx(self._points[-1])
                painter.drawEllipse(t_end, 3, 3)

        painter.end()


# ═══════════════════════════════════════════════════════════════════════════
# Detail dialog – shows all fields for a single pattern
# ═══════════════════════════════════════════════════════════════════════════

class PatternDetailDialog(QDialog):
    """Dialog showing every decoded field of a stitch pattern."""

    def __init__(self, patterns: list[dict], index: int, parent=None):
        super().__init__(parent)
        self._patterns = patterns
        self._index = index
        self.setWindowTitle(f"Pattern #{patterns[index]['index']} — Detail")
        self.resize(900, 750)
        self.setMinimumSize(700, 500)
        self._build_ui()

    @property
    def _entry(self):
        return self._patterns[self._index]

    @staticmethod
    def _summary_html(e: dict) -> str:
        return (
            f"<b>Index:</b> {e['index']} &nbsp;&nbsp;"
            f"<b>Offset:</b> {e['para_offset']} (0x{e['para_offset']:06X}) &nbsp;&nbsp;"
            f"<b>Length:</b> {e['pattern_length']} &nbsp;&nbsp;"
            f"<b>Y0:</b> 0x{e['pattern_y0']:02X} &nbsp;&nbsp;"
            f"<b>YN:</b> 0x{e['pattern_yn']:02X} &nbsp;&nbsp;"
            f"<b>Scale:</b> 0x{e.get('para_scale_1', 0):02X} 0x{e.get('para_scale_2', 0):02X} &nbsp;&nbsp;"
            f"<b>Span X:</b> {e.get('span_x', 0)} &nbsp;&nbsp;"
            f"<b>Span Y:</b> {e.get('span_y', 0)} &nbsp;&nbsp;"
            f"<b>Type:</b> {e.get('stitch_type', 'MAXI')}"
        )

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Summary line ──
        self._summary = QLabel(self._summary_html(self._entry))
        self._summary.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self._summary.setStyleSheet("padding: 6px; background: #eef;")
        layout.addWidget(self._summary)

        # ── Splitter: preview (top) | text dump (bottom) ──
        e = self._entry
        splitter = QSplitter(Qt.Vertical)

        self._preview = PatternPreviewWidget()
        self._preview.set_pattern(e['pattern_dxdy'], e['pattern_y0'])
        splitter.addWidget(self._preview)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Consolas", 10))
        self._text.setStyleSheet("QTextEdit { background: #fafafa; }")
        splitter.addWidget(self._text)

        splitter.setStretchFactor(0, 1)  # preview: 1/5th
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter, stretch=1)

        self._populate_text()

        # ── Controls: checkbox + nav buttons ──
        ctrl_row = QHBoxLayout()

        self._chk_points = QCheckBox("Show stitch points")
        self._chk_points.setChecked(True)
        self._chk_points.toggled.connect(self._on_toggle_points)
        ctrl_row.addWidget(self._chk_points)

        ctrl_row.addStretch()

        self._btn_prev = QPushButton("◀  Prev")
        self._btn_prev.clicked.connect(lambda: self._navigate(-1))
        ctrl_row.addWidget(self._btn_prev)

        self._lbl_nav = QLabel(f"{self._index + 1} / {len(self._patterns)}")
        self._lbl_nav.setAlignment(Qt.AlignCenter)
        self._lbl_nav.setMinimumWidth(80)
        ctrl_row.addWidget(self._lbl_nav)

        self._btn_next = QPushButton("Next  ▶")
        self._btn_next.clicked.connect(lambda: self._navigate(+1))
        ctrl_row.addWidget(self._btn_next)

        ctrl_row.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        ctrl_row.addWidget(btn_close)

        layout.addLayout(ctrl_row)
        self._update_nav_buttons()

    def _populate_text(self):
        e = self._entry
        lines = []

        lines.append("═" * 52)
        lines.append(f"  PATTERN  #{e['index']}")
        lines.append("═" * 52)
        lines.append("")

        # para_raw – show as hex dump
        lines.append("▸ para_raw  (116 bytes from PCDPARA3.MUF)")
        raw = e['para_raw']
        for offset in range(0, len(raw), 16):
            chunk = raw[offset:offset + 16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"  {offset:04X}  {hex_part:<48s}  {ascii_part}")
        lines.append("")

        lines.append(f"▸ para_offset  = {e['para_offset']}  (0x{e['para_offset']:06X})  [para_raw bytes 4-6, LE]")
        lines.append(f"▸ para_scale_1 = {e.get('para_scale_1', 0)}  (0x{e.get('para_scale_1', 0):02X})  [para_raw byte 1]")
        lines.append(f"▸ para_scale_2 = {e.get('para_scale_2', 0)}  (0x{e.get('para_scale_2', 0):02X})  [para_raw byte 2]")
        lines.append("")

        lines.append(f"▸ pattern_y0   = {e['pattern_y0']}  (0x{e['pattern_y0']:02X})")
        lines.append(f"▸ pattern_yn   = {e['pattern_yn']}  (0x{e['pattern_yn']:02X})")

        lines.append(f"▸ pattern_length = {e['pattern_length']}  (0x{e['pattern_length']:04X})")
        lines.append("")

        # pattern_dxdy as hex dump
        dxdy = e['pattern_dxdy']
        lines.append(f"▸ pattern_dxdy  ({len(dxdy)} bytes, {len(dxdy)//2} pairs)")
        if dxdy:
            for offset in range(0, len(dxdy), 16):
                chunk = dxdy[offset:offset + 16]
                hex_part = " ".join(f"{b:02X}" for b in chunk)
                lines.append(f"  {offset:04X}  {hex_part}")
        else:
            lines.append("  (empty)")
        lines.append("")

        self._text.setPlainText("\n".join(lines))

    def _on_toggle_points(self, checked: bool):
        self._preview._show_points = checked
        self._preview.update()

    def _navigate(self, delta: int):
        new_idx = self._index + delta
        if 0 <= new_idx < len(self._patterns):
            self._index = new_idx
            e = self._entry
            self.setWindowTitle(f"Pattern #{e['index']} — Detail")
            self._summary.setText(self._summary_html(e))
            self._preview.set_pattern(e['pattern_dxdy'], e['pattern_y0'])
            self._populate_text()
            self._lbl_nav.setText(f"{self._index + 1} / {len(self._patterns)}")
            self._update_nav_buttons()

    def _update_nav_buttons(self):
        self._btn_prev.setEnabled(self._index > 0)
        self._btn_next.setEnabled(self._index < len(self._patterns) - 1)


# ═══════════════════════════════════════════════════════════════════════════
# Reference-table detail dialog – shows all sub-patterns
# ═══════════════════════════════════════════════════════════════════════════

class RefTableDetailDialog(QDialog):
    """Dialog showing all sub-patterns decoded from a reference-table entry."""

    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self._entry = entry
        self._sublist = entry.get('sublist', [])
        self._current_sub = 0
        self.setWindowTitle(f"Reference Table — Entry #{entry['index']}  "
                            f"({len(self._sublist)} sub-patterns)")
        self.resize(950, 750)
        self.setMinimumSize(750, 500)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Summary ──
        e = self._entry
        raw_preview = ", ".join(
            f"{b:02X}" for b in e['para_raw'][:12]) + " …"
        summary = QLabel(
            f"<b>Entry #{e['index']}</b> &nbsp; "
            f"<b>para_offset:</b> 0x000000 (ref table) &nbsp; "
            f"<b>Sub-patterns:</b> {len(self._sublist)} &nbsp; "
            f"<b>para_raw:</b> {raw_preview}"
        )
        summary.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        summary.setStyleSheet("padding: 6px; background: #eef;")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        # ── Splitter: preview | sub-info text ──
        splitter = QSplitter(Qt.Vertical)

        self._preview = PatternPreviewWidget()
        splitter.addWidget(self._preview)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Consolas", 10))
        self._text.setStyleSheet("QTextEdit { background: #fafafa; }")
        splitter.addWidget(self._text)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter, stretch=1)

        # ── Controls ──
        ctrl_row = QHBoxLayout()

        self._chk_points = QCheckBox("Show stitch points")
        self._chk_points.setChecked(True)
        self._chk_points.toggled.connect(self._on_toggle_points)
        ctrl_row.addWidget(self._chk_points)

        ctrl_row.addStretch()

        self._btn_prev = QPushButton("◀  Prev Sub")
        self._btn_prev.clicked.connect(lambda: self._navigate_sub(-1))
        ctrl_row.addWidget(self._btn_prev)

        self._lbl_nav = QLabel()
        self._lbl_nav.setAlignment(Qt.AlignCenter)
        self._lbl_nav.setMinimumWidth(80)
        ctrl_row.addWidget(self._lbl_nav)

        self._btn_next = QPushButton("Next Sub  ▶")
        self._btn_next.clicked.connect(lambda: self._navigate_sub(+1))
        ctrl_row.addWidget(self._btn_next)

        ctrl_row.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        ctrl_row.addWidget(btn_close)

        layout.addLayout(ctrl_row)
        self._update_sub_buttons()
        self._show_sub(0)

    def _show_sub(self, idx: int):
        if 0 <= idx < len(self._sublist):
            self._current_sub = idx
            sp = self._sublist[idx]
            self._preview.set_pattern(sp['pattern_dxdy'], sp['pattern_y0'])
            self._populate_sub_text(sp)
            self._lbl_nav.setText(f"Sub {idx + 1} / {len(self._sublist)}")
            self._update_sub_buttons()

    def _populate_sub_text(self, sp: dict):
        lines = []
        lines.append("═" * 52)
        lines.append(f"  SUB-PATTERN  {self._current_sub}  "
                     f"(entry #{self._entry['index']})")
        lines.append("═" * 52)
        lines.append("")
        lines.append(f"▸ para_offset  = {sp['para_offset']}  "
                     f"(0x{sp['para_offset']:06X})")
        lines.append("")
        lines.append(f"▸ pattern_y0   = 0x{sp['pattern_y0']:02X}  "
                     f"({sp['pattern_y0']})")
        lines.append(f"▸ pattern_yn   = 0x{sp['pattern_yn']:02X}  "
                     f"({sp['pattern_yn']})")
        lines.append("")
        lines.append(f"▸ pattern_length = {sp['pattern_length']}  "
                     f"(0x{sp['pattern_length']:04X})")
        lines.append("")
        dxdy = sp['pattern_dxdy']
        lines.append(f"▸ pattern_dxdy  ({len(dxdy)} bytes, "
                     f"{len(dxdy)//2} pairs)")
        if dxdy:
            for offset in range(0, len(dxdy), 16):
                chunk = dxdy[offset:offset + 16]
                hex_part = " ".join(f"{b:02X}" for b in chunk)
                lines.append(f"  {offset:04X}  {hex_part}")
        else:
            lines.append("  (empty)")
        lines.append("")
        self._text.setPlainText("\n".join(lines))

    def _on_toggle_points(self, checked: bool):
        self._preview._show_points = checked
        self._preview.update()

    def _navigate_sub(self, delta: int):
        new_idx = self._current_sub + delta
        if 0 <= new_idx < len(self._sublist):
            self._show_sub(new_idx)

    def _update_sub_buttons(self):
        self._btn_prev.setEnabled(self._current_sub > 0)
        self._btn_next.setEnabled(self._current_sub < len(self._sublist) - 1)


# ═══════════════════════════════════════════════════════════════════════════
# FlowLayout – wraps child widgets automatically (Qt example pattern)
# ═══════════════════════════════════════════════════════════════════════════

class FlowLayout(QLayout):
    """A layout that arranges children left-to-right, wrapping to next row."""

    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        self._items: list = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return int(self._do_layout(QRectF(0, 0, width, 0), True))

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(QRectF(rect), False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSizeF()
        for item in self._items:
            ms = item.minimumSize()
            size = size.expandedTo(QSizeF(ms.width(), ms.height()))
        margins = self.contentsMargins()
        size += QSizeF(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size.toSize()

    def _do_layout(self, rect: QRectF, test_only: bool) -> float:
        margins = self.contentsMargins()
        left = rect.left() + margins.left()
        top = rect.top() + margins.top()
        right = rect.right() - margins.right()
        available = rect.width() - margins.left() - margins.right()

        x = left
        y = top
        line_height = 0

        for item in self._items:
            size = item.sizeHint()
            # Wrap to next line if it doesn't fit
            if x + size.width() > right and line_height > 0:
                x = left
                y += line_height + self.spacing()
                line_height = 0
            if not test_only:
                sz = size
                item.setGeometry(QRectF(QPointF(x, y),
                                        QSizeF(sz.width(), sz.height())).toRect())
            x += size.width() + self.spacing()
            line_height = max(line_height, size.height())

        return float(y + line_height - rect.top() + margins.bottom())


# ═══════════════════════════════════════════════════════════════════════════
# Byte stats dialog – statistics over first 32 bytes of para_raw
# ═══════════════════════════════════════════════════════════════════════════

BYTE_STATS_COUNT = 32  # how many bytes of para_raw to analyse


class ByteValueDetailDialog(QDialog):
    """Shows all pattern indices + previews for a given byte offset & value."""

    def __init__(self, byte_offset: int, value: int, patterns_info: list[dict],
                 bilder_data: bytes, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Byte {byte_offset} = 0x{value:02X} — Patterns")
        self.resize(750, 550)
        self.setMinimumSize(500, 350)
        self._byte_offset = byte_offset
        self._value = value
        self._patterns_info = patterns_info
        self._bilder_data = bilder_data
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        lbl = QLabel(
            f"<b>Byte offset {self._byte_offset}</b> = "
            f"<b>0x{self._value:02X}</b> ({self._value})  —  "
            f"{len(self._patterns_info)} pattern(s)"
        )
        lbl.setStyleSheet("padding: 6px; background: #eef; font-size: 13px;")
        layout.addWidget(lbl)

        table = QTableWidget(len(self._patterns_info), 3)
        table.setHorizontalHeaderLabels(["Pattern Index", "Preview", "Thumbnail"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(52)

        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        table.setFont(QFont("Consolas", 10))

        for row, pi in enumerate(self._patterns_info):
            idx_item = QTableWidgetItem(str(pi['index']))
            idx_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row, 0, idx_item)

            # BILDER preview
            bl = QLabel()
            if self._bilder_data:
                istart = pi['index'] * BILDER_BYTES_PER_IMAGE
                iend = istart + BILDER_BYTES_PER_IMAGE
                if iend <= len(self._bilder_data):
                    qi = bilder_to_image(self._bilder_data[istart:iend])
                    bl.setPixmap(QPixmap.fromImage(qi))
            bl.setAlignment(Qt.AlignCenter)
            table.setCellWidget(row, 1, bl)

            # Thumbnail
            tl = QLabel()
            thumb = render_thumbnail(pi.get('pattern_dxdy', []),
                                     pi.get('pattern_y0', 0))
            tl.setPixmap(thumb)
            tl.setAlignment(Qt.AlignCenter)
            table.setCellWidget(row, 2, tl)

        layout.addWidget(table)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        ctrl = QHBoxLayout()
        ctrl.addStretch()
        ctrl.addWidget(btn_close)
        layout.addLayout(ctrl)


class ByteStatsDialog(QDialog):
    """Statistics over the first 32 bytes of para_raw across all main patterns."""

    def __init__(self, patterns: list[dict], bilder_data: bytes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Byte Stats — para_raw (first 32 bytes)")
        self.resize(820, 680)
        self.setMinimumSize(600, 400)
        self._patterns = patterns
        self._bilder_data = bilder_data
        self._stats = self._compute_stats()
        self._build_ui()

    def _compute_stats(self) -> list[dict]:
        """For each byte offset 0..31, collect {value: [pattern_indices]}."""
        stats: list[dict] = []
        for off in range(BYTE_STATS_COUNT):
            val_to_indices: dict[int, list[int]] = {}
            for p in self._patterns:
                raw = p.get('para_raw', [])
                if off < len(raw):
                    v = raw[off]
                    val_to_indices.setdefault(v, []).append(p['index'])
            # Re-sort by value for consistent display
            stats.append(dict(sorted(val_to_indices.items())))
        return stats

    def _build_ui(self):
        layout = QVBoxLayout(self)

        lbl = QLabel(
            f"<b>First {BYTE_STATS_COUNT} bytes of para_raw</b> across "
            f"{len(self._patterns)} main patterns.  "
            f"Click a row to see detail per value."
        )
        lbl.setStyleSheet("padding: 6px; background: #eef;")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self._table = QTableWidget(BYTE_STATS_COUNT, 2)
        self._table.setHorizontalHeaderLabels(["Byte Offset", "Values (count)"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.setFont(QFont("Consolas", 10))

        for off, val_map in enumerate(self._stats):
            # Column 0 – byte offset
            off_item = QTableWidgetItem(str(off))
            off_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(off, 0, off_item)

            # Column 1 – value list with counts, sorted by value
            parts = []
            for v in sorted(val_map.keys()):
                count = len(val_map[v])
                parts.append(f"{v:02X} ({count})")
            val_item = QTableWidgetItem("  ".join(parts))
            self._table.setItem(off, 1, val_item)

        layout.addWidget(self._table)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        ctrl = QHBoxLayout()
        ctrl.addStretch()
        ctrl.addWidget(btn_close)
        layout.addLayout(ctrl)

    def _on_row_double_clicked(self, row: int, _col: int):
        """Open detail dialog for the clicked byte offset."""
        if 0 <= row < len(self._stats):
            val_map = self._stats[row]
            # Build a flat list of (value, pattern_info) for all values
            # We'll show the detail in a scrollable dialog
            dlg = ByteOffsetDetailDialog(
                row, val_map, self._patterns, self._bilder_data, self)
            dlg.setAttribute(Qt.WA_DeleteOnClose, True)
            dlg.show()


class ByteOffsetDetailDialog(QDialog):
    """Shows all values for a given byte offset, each with its pattern list."""

    def __init__(self, byte_offset: int, val_map: dict,
                 patterns: list[dict], bilder_data: bytes, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Byte {byte_offset} — All Values Detail")
        self.resize(800, 700)
        self.setMinimumSize(600, 400)
        self._byte_offset = byte_offset
        self._val_map = val_map
        self._patterns = patterns
        self._bilder_data = bilder_data
        self._build_ui()

    def _make_cell(self, pi: int) -> QWidget:
        """Create one clickable preview cell (48x48 image + index below)."""
        btn = QPushButton()
        btn.setFixedSize(52, 66)
        btn.setFlat(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton { border: none; padding: 0; }"
            "QPushButton:hover { background: #d0d0ff; }"
        )
        btn.clicked.connect(lambda _checked, idx=pi: self._on_cell_clicked(idx))

        btn_layout = QVBoxLayout(btn)
        btn_layout.setSpacing(0)
        btn_layout.setContentsMargins(2, 2, 2, 2)

        bl = QLabel()
        bl.setFixedSize(48, 48)
        bl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if self._bilder_data:
            istart = pi * BILDER_BYTES_PER_IMAGE
            iend = istart + BILDER_BYTES_PER_IMAGE
            if iend <= len(self._bilder_data):
                qi = bilder_to_image(self._bilder_data[istart:iend])
                bl.setPixmap(QPixmap.fromImage(qi))
        bl.setAlignment(Qt.AlignCenter)
        btn_layout.addWidget(bl, alignment=Qt.AlignCenter)

        idx_lbl = QLabel(str(pi))
        idx_lbl.setAlignment(Qt.AlignCenter)
        idx_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        idx_lbl.setStyleSheet("font-size: 9px; color: #555;")
        btn_layout.addWidget(idx_lbl)
        return btn

    def _on_cell_clicked(self, pi: int):
        """Open the PatternDetailDialog for the clicked pattern index."""
        p = self._patterns[pi]
        if p.get('is_ref_table'):
            dlg = RefTableDetailDialog(p, self)
        else:
            dlg = PatternDetailDialog(self._patterns, pi, self)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.show()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        lbl = QLabel(
            f"<b>Byte offset {self._byte_offset}</b> — "
            f"{len(self._val_map)} unique value(s) across "
            f"{len(self._patterns)} patterns"
        )
        lbl.setStyleSheet("padding: 6px; background: #eef; font-size: 13px;")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        # Scroll area with sections per value
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(6)
        container_layout.setContentsMargins(4, 4, 4, 4)

        for v in sorted(self._val_map.keys()):
            indices = self._val_map[v]

            val_lbl = QLabel(
                f"<b>0x{v:02X}</b> ({v:3d})  —  {len(indices)} pattern(s)"
            )
            val_lbl.setStyleSheet(
                "padding: 3px 8px; background: #dde; font-size: 12px;"
            )
            container_layout.addWidget(val_lbl)

            # Flow layout – wraps cells automatically
            flow = FlowLayout(margin=0, spacing=2)
            for pi in indices:
                flow.addWidget(self._make_cell(pi))
            container_layout.addLayout(flow)

        container_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        ctrl = QHBoxLayout()
        ctrl.addStretch()
        ctrl.addWidget(btn_close)
        layout.addLayout(ctrl)


# ═══════════════════════════════════════════════════════════════════════════
# Hex file browser – shows KOOR file with coverage highlighting
# ═══════════════════════════════════════════════════════════════════════════

BYTES_PER_ROW = 32
HEX_CHAR_W = 26   # width of "XX " in pixels (monospace)
ADDR_W = 80       # width of address column
OVERVIEW_W = 20   # width of right-side overview bar
ROW_H = 20        # height of one row


class HexViewWidget(QWidget):
    """Custom-painted hex dump with pattern coverage highlighting."""

    # Cached colors
    _GREEN_A = QColor(200, 255, 200)
    _GREEN_B = QColor(160, 240, 160)
    _RED = QColor(255, 180, 180)
    _ADDR_BG = QColor(245, 245, 245)
    _WHITE = QColor(255, 255, 255)
    _BLACK = QColor(0, 0, 0)
    _GREY_TEXT = QColor(100, 100, 100)
    _GREY_LINE = QColor(220, 220, 220)
    _BAR_RED = QColor(255, 180, 180)
    _BAR_GREEN = QColor(100, 200, 100)

    def __init__(self, koor_data: bytes, patterns: list[dict], parent=None):
        super().__init__(parent)
        self._data = koor_data
        self._patterns = patterns
        self._coverage = self._build_coverage()
        self._rows = (len(koor_data) + BYTES_PER_ROW - 1) // BYTES_PER_ROW

        # Pre-compute overview bar fractions and address strings
        self._row_fracs: list[float] = []
        self._row_addrs: list[str] = []
        for row in range(self._rows):
            addr = row * BYTES_PER_ROW
            self._row_addrs.append(f"{addr:08X}")
            cov_count = 0
            row_end = min(addr + BYTES_PER_ROW, len(self._data))
            for off in range(addr, row_end):
                if self._coverage[off] >= 0:
                    cov_count += 1
            denom = row_end - addr
            self._row_fracs.append(cov_count / denom if denom > 0 else 0.0)

        total_w = ADDR_W + BYTES_PER_ROW * HEX_CHAR_W + OVERVIEW_W + 20
        self.setFixedSize(total_w, max(self._rows * ROW_H + 10, 100))
        self.setMouseTracking(True)
        self._hover_pattern = -1
        self._font = QFont("Consolas", 10)

    def _build_coverage(self) -> list[int]:
        """Return array: coverage[byte] = pattern_index or -1."""
        cov = [-1] * len(self._data)
        for p in self._patterns:
            # Mark the reference table area itself (if this is the ref-table entry)
            if p.get('is_ref_table') and p['para_offset'] == 0:
                for off in range(0, min(0x3B4, len(self._data))):
                    if cov[off] == -1:
                        cov[off] = p['index']
            # Mark sub-pattern ranges for ref-table entries
            for sp in p.get('sublist', []):
                start = sp['para_offset']
                end = start + 4 + len(sp['pattern_dxdy'])
                if end > len(self._data):
                    end = len(self._data)
                for off in range(start, end):
                    if cov[off] == -1:
                        cov[off] = p['index']
            # Mark normal pattern range
            if not p.get('is_ref_table'):
                start = p['para_offset']
                end = start + 4 + len(p['pattern_dxdy'])
                if end > len(self._data):
                    end = len(self._data)
                for off in range(start, end):
                    if cov[off] == -1:
                        cov[off] = p['index']
        return cov

    def _pattern_at(self, byte_offset: int) -> int:
        if 0 <= byte_offset < len(self._coverage):
            return self._coverage[byte_offset]
        return -1

    def _bg_for(self, pat_idx: int) -> QColor:
        if pat_idx < 0:
            return self._RED
        return self._GREEN_A if (pat_idx % 2 == 0) else self._GREEN_B

    # ── painting ───────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self._font)

        clip = event.rect()
        first_row = max(0, clip.top() // ROW_H)
        last_row = min(self._rows - 1, clip.bottom() // ROW_H)

        x_hex_start = ADDR_W + 4
        bar_x = ADDR_W + BYTES_PER_ROW * HEX_CHAR_W + 8
        full_w = self.width()

        for row in range(first_row, last_row + 1):
            y = row * ROW_H
            addr = row * BYTES_PER_ROW

            # Address column
            painter.fillRect(0, y, ADDR_W, ROW_H, self._ADDR_BG)
            painter.setPen(self._GREY_TEXT)
            painter.drawText(4, y, ADDR_W - 8, ROW_H,
                             Qt.AlignVCenter | Qt.AlignRight,
                             self._row_addrs[row])

            # Hex bytes
            for col in range(BYTES_PER_ROW):
                off = addr + col
                if off >= len(self._data):
                    break
                byte_val = self._data[off]
                bg = self._bg_for(self._coverage[off])
                x = x_hex_start + col * HEX_CHAR_W

                painter.fillRect(int(x), y, HEX_CHAR_W - 2, ROW_H, bg)
                painter.setPen(self._BLACK)
                # Inline hex formatting avoids f-string per byte
                hi = byte_val >> 4
                lo = byte_val & 0xF
                ch_hi = chr(hi + 48) if hi < 10 else chr(hi + 55)
                ch_lo = chr(lo + 48) if lo < 10 else chr(lo + 55)
                txt = ch_hi + ch_lo
                painter.drawText(int(x) + 2, y, HEX_CHAR_W - 4, ROW_H,
                                 Qt.AlignVCenter | Qt.AlignLeft, txt)

            # Separator
            painter.setPen(self._GREY_LINE)
            painter.drawLine(bar_x - 4, y, bar_x - 4, y + ROW_H)

            # Overview bar
            frac = self._row_fracs[row]
            bar_h = ROW_H - 2
            bar_y = y + 1
            painter.fillRect(int(bar_x), int(bar_y), OVERVIEW_W, int(bar_h),
                             self._BAR_RED)
            if frac > 0:
                green_h = int(bar_h * frac)
                painter.fillRect(int(bar_x), int(bar_y + bar_h - green_h),
                                 OVERVIEW_W, green_h, self._BAR_GREEN)

        painter.end()

    # ── hover ──────────────────────────────────────────────────────

    def mouseMoveEvent(self, event):
        x = event.pos().x()
        y = event.pos().y()
        row = y // ROW_H
        col = (x - ADDR_W - 4) // HEX_CHAR_W
        if 0 <= col < BYTES_PER_ROW and 0 <= row < self._rows:
            off = row * BYTES_PER_ROW + col
            pat = self._pattern_at(off)
        else:
            pat = -1

        if pat != self._hover_pattern:
            self._hover_pattern = pat
            if pat >= 0:
                p = self._patterns[pat]
                tip = (f"Pattern #{p['index']}  |  "
                       f"Offset 0x{p['para_offset']:06X}  |  "
                       f"Len {p['pattern_length']}  |  "
                       f"Y0 {p['pattern_y0']:02X} YN {p['pattern_yn']:02X}")
                QToolTip.showText(event.globalPos(), tip, self)
            else:
                QToolTip.hideText()


class HexFileBrowser(QDialog):
    """Scrollable hex dump of the KOOR file with pattern coverage overlay."""

    def __init__(self, koor_data: bytes, patterns: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("PCDKOOR3.MUF — Hex Browser")
        self.resize(1050, 750)
        self.setMinimumSize(800, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._hex_view = HexViewWidget(koor_data, patterns)
        scroll = QScrollArea()
        scroll.setWidget(self._hex_view)
        scroll.setWidgetResizable(False)
        layout.addWidget(scroll)


# ═══════════════════════════════════════════════════════════════════════════
# Main window
# ═══════════════════════════════════════════════════════════════════════════

class StitchPatternTool(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PCD .MUF Files Analysis Tool")
        self.resize(900, 650)
        self.setMinimumSize(700, 400)

        self._para_path: str = ""
        self._koor_path: str = ""
        self._bilder_path: str = ""
        self._koor_data: bytes = b""
        self._bilder_data: bytes = b""
        self._patterns: list[dict] = []

        self._build_ui()
        self._build_statusbar()
        self._auto_detect_files()

    # ── UI construction ───────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── File selection ──
        file_group = QGroupBox("Input Files")
        file_layout = QVBoxLayout(file_group)

        # PARA file row
        para_row = QHBoxLayout()
        para_row.addWidget(QLabel("Index (PCDPARA3.MUF):"))
        self._para_label = QLabel("(no file selected)")
        self._para_label.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self._para_label.setMinimumWidth(250)
        para_row.addWidget(self._para_label, 1)
        btn_para = QPushButton("Browse...")
        btn_para.clicked.connect(self._browse_para)
        para_row.addWidget(btn_para)
        file_layout.addLayout(para_row)

        # KOOR file row
        koor_row = QHBoxLayout()
        koor_row.addWidget(QLabel("Patterns (PCDKOOR3.MUF):"))
        self._koor_label = QLabel("(no file selected)")
        self._koor_label.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self._koor_label.setMinimumWidth(250)
        koor_row.addWidget(self._koor_label, 1)
        btn_koor = QPushButton("Browse...")
        btn_koor.clicked.connect(self._browse_koor)
        koor_row.addWidget(btn_koor)
        file_layout.addLayout(koor_row)

        # BILDER file row
        bilder_row = QHBoxLayout()
        bilder_row.addWidget(QLabel("Previews (BILDER.MUF):"))
        self._bilder_label = QLabel("(no file selected)")
        self._bilder_label.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self._bilder_label.setMinimumWidth(250)
        bilder_row.addWidget(self._bilder_label, 1)
        btn_bilder = QPushButton("Browse...")
        btn_bilder.clicked.connect(self._browse_bilder)
        bilder_row.addWidget(btn_bilder)
        file_layout.addLayout(bilder_row)

        root.addWidget(file_group)

        # ── Action buttons ──
        action_row = QHBoxLayout()
        action_row.addStretch()

        self._parse_btn = QPushButton("▶  Parse Files")
        self._parse_btn.setEnabled(False)
        self._parse_btn.clicked.connect(self._on_parse)
        self._parse_btn.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 8px 24px; }"
        )
        action_row.addWidget(self._parse_btn)

        self._save_btn = QPushButton("💾  Save JSON")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save_json)
        action_row.addWidget(self._save_btn)

        self._hex_btn = QPushButton("🔍  Hex Browser")
        self._hex_btn.setEnabled(False)
        self._hex_btn.clicked.connect(self._open_hex_browser)
        action_row.addWidget(self._hex_btn)

        self._stats_btn = QPushButton("📊  Byte stats")
        self._stats_btn.setEnabled(False)
        self._stats_btn.clicked.connect(self._open_byte_stats)
        action_row.addWidget(self._stats_btn)

        action_row.addStretch()
        root.addLayout(action_row)

        # ── Info label ──
        self._info_label = QLabel("Select both files and click Parse.")
        self._info_label.setStyleSheet("color: #555; padding: 2px;")
        root.addWidget(self._info_label)

        # ── Table ──
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(["Index", "Offset (hex)", "Length", "Y0 YN", "Scale", "Type", "Preview", "Pattern"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(52)
        self._table.verticalHeader().setMinimumSectionSize(48)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)

        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.Stretch)

        self._table.setFont(QFont("Consolas", 10))
        root.addWidget(self._table, stretch=1)

    def _build_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready.")

    def _auto_detect_files(self):
        """Pre-select MUF files from script directory."""
        script_dir = Path(__file__).resolve().parent
        para_candidate = script_dir / "PCDPARA3.MUF"
        koor_candidate = script_dir / "PCDKOOR3.MUF"
        bilder_candidate = script_dir / "BILDER.MUF"

        if para_candidate.is_file():
            self._para_path = str(para_candidate)
            self._para_label.setText(str(para_candidate))

        if koor_candidate.is_file():
            self._koor_path = str(koor_candidate)
            self._koor_label.setText(str(koor_candidate))

        if bilder_candidate.is_file():
            self._bilder_path = str(bilder_candidate)
            self._bilder_label.setText(str(bilder_candidate))

        self._update_parse_button()

    # ── Slots ─────────────────────────────────────────────────────────

    def _browse_para(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Index File (PCDPARA3.MUF)", "",
            "MUF files (*.MUF);;All files (*)"
        )
        if path:
            self._para_path = path
            self._para_label.setText(path)
            self._update_parse_button()

    def _browse_koor(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Coordinate File (PCDKOOR3.MUF)", "",
            "MUF files (*.MUF);;All files (*)"
        )
        if path:
            self._koor_path = path
            self._koor_label.setText(path)
            self._update_parse_button()

    def _browse_bilder(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Image File (BILDER.MUF)", "",
            "MUF files (*.MUF);;All files (*)"
        )
        if path:
            self._bilder_path = path
            self._bilder_label.setText(path)

    def _update_parse_button(self):
        self._parse_btn.setEnabled(bool(self._para_path) and bool(self._koor_path))

    def _on_parse(self):
        """Run the parser and populate the table."""
        try:
            self._patterns = parse_files(self._para_path, self._koor_path)
            with open(self._koor_path, 'rb') as f:
                self._koor_data = f.read()
            if self._bilder_path:
                with open(self._bilder_path, 'rb') as f:
                    self._bilder_data = f.read()
        except Exception as e:
            QMessageBox.critical(self, "Parse Error", f"Failed to parse files:\n{e}")
            self.status_bar.showMessage("Parse failed.")
            return

        self._populate_table()
        self._save_btn.setEnabled(True)
        self._hex_btn.setEnabled(True)
        self._stats_btn.setEnabled(True)
        self._info_label.setText(
            f"Parsed {len(self._patterns)} patterns  —  "
            f"Double-click a row for details."
        )
        self.status_bar.showMessage(
            f"Loaded {len(self._patterns)} patterns from "
            f"{os.path.basename(self._para_path)} + {os.path.basename(self._koor_path)} + {os.path.basename(self._bilder_path)}."
        )

    def _populate_table(self):
        """Fill the table widget with pattern summary rows."""
        self._table.setRowCount(len(self._patterns))

        for i, p in enumerate(self._patterns):
            # Index
            if p.get('is_ref_table'):
                idx_text = f"[REF] {p['index']}"
            else:
                idx_text = str(p['index'])
            idx_item = QTableWidgetItem(idx_text)
            idx_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(i, 0, idx_item)

            # Offset (hex)
            off_item = QTableWidgetItem(f"0x{p['para_offset']:06X}")
            off_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(i, 1, off_item)

            # Length
            if p.get('is_ref_table'):
                len_text = f"{len(p['sublist'])} sub"
            else:
                len_text = str(p['pattern_length'])
            len_item = QTableWidgetItem(len_text)
            len_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(i, 2, len_item)

            # Y0 / YN – two hex bytes, space delimited
            y0_text = f"{p['pattern_y0']:02X} {p['pattern_yn']:02X}"
            y0_item = QTableWidgetItem(y0_text)
            y0_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(i, 3, y0_item)

            # Scale – bytes 1-2 from para_raw, shown separately
            scale_text = f"{p.get('para_scale_1', 0):02X} {p.get('para_scale_2', 0):02X}"
            scale_item = QTableWidgetItem(scale_text)
            scale_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(i, 4, scale_item)

            # Stitch Type
            type_item = QTableWidgetItem(p.get('stitch_type', 'MAXI'))
            type_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(i, 5, type_item)

            # BILDER preview image
            bilder_label = QLabel()
            if self._bilder_data:
                img_start = i * BILDER_BYTES_PER_IMAGE
                img_end = img_start + BILDER_BYTES_PER_IMAGE
                if img_end <= len(self._bilder_data):
                    qi = bilder_to_image(self._bilder_data[img_start:img_end])
                    pm = QPixmap.fromImage(qi)
                    bilder_label.setPixmap(pm)
            bilder_label.setAlignment(Qt.AlignCenter)
            self._table.setCellWidget(i, 6, bilder_label)

            # Thumbnail preview (pattern line)
            thumb = render_thumbnail(p['pattern_dxdy'], p['pattern_y0'])
            thumb_label = QLabel()
            thumb_label.setPixmap(thumb)
            thumb_label.setAlignment(Qt.AlignCenter)
            self._table.setCellWidget(i, 7, thumb_label)

    def _on_cell_double_clicked(self, row: int, _col: int):
        """Open detail dialog for the double-clicked pattern."""
        if 0 <= row < len(self._patterns):
            p = self._patterns[row]
            if p.get('is_ref_table'):
                dlg = RefTableDetailDialog(p, self)
            else:
                dlg = PatternDetailDialog(self._patterns, row, self)
            dlg.setAttribute(Qt.WA_DeleteOnClose, True)
            dlg.show()

    def _on_save_json(self):
        """Save parsed patterns to a JSON file, with dxdy as signed ints."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save JSON", "patterns.json",
            "JSON files (*.json);;All files (*)"
        )
        if not path:
            return

        try:
            # Build a copy with pattern_dxdy converted to signed bytes
            export = []
            for p in self._patterns:
                entry = dict(p)
                entry['pattern_dxdy'] = [
                    b if b < 128 else b - 256 for b in p['pattern_dxdy']
                ]
                # Also convert sublist dxdy for ref-table entries
                if entry.get('sublist'):
                    entry['sublist'] = [
                        dict(sp, pattern_dxdy=[
                            b if b < 128 else b - 256 for b in sp['pattern_dxdy']
                        ])
                        for sp in p['sublist']
                    ]
                export.append(entry)

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(export, f, indent=2, ensure_ascii=False)
            self.status_bar.showMessage(f"Saved to {path}")
            QMessageBox.information(self, "Saved", f"JSON written to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _open_byte_stats(self):
        """Open the byte statistics dialog for para_raw."""
        if not self._patterns:
            return
        dlg = ByteStatsDialog(self._patterns, self._bilder_data, self)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.show()

    def _open_hex_browser(self):
        """Open the hex file browser for PCDKOOR3.MUF."""
        if not self._koor_data or not self._patterns:
            return
        browser = HexFileBrowser(self._koor_data, self._patterns, self)
        browser.setAttribute(Qt.WA_DeleteOnClose, True)
        browser.show()


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    window = StitchPatternTool()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
