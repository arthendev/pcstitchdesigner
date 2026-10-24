"""Binary VCSM file format (.9mm and .max files).

VCSM format (.9mm and .max files):
  Header:  "VCSM" (4 bytes)
           byte 4: 0xE8 = no optional header block, 0xE9 = optional block present
           byte 8: stitch type (0x00 = 9mm, 0x01 = MAXI)
           bytes 12-14: thread colour, R then G then B
  Optional block (0xE9 only, starting at offset 0x14):
           foot-type length + ASCII, needle-type length + ASCII,
           uint16 tension (value / 100), 2 unknown bytes,
           notes length (0xFF means the next uint16 holds the length) + ASCII notes,
           uint32 template-image size + embedded image bytes
  Body:    uint32 stitch count, then 8-byte records:
           int24 x LE, c0, int24 y LE, c1
           Coordinates use 1/10 mm units
           and are stored as 24-bit signed values.

  Header bytes that are constant across the 238 sample files:
           5-7 = 03 00 00, 9-11 = 00 00 00, 15 = 00,
           16-19 = 01 00 00 00 (16 samples store 0). The meaning of bytes 15-19
           is unknown, so the writer emits the common values above.

Coordinates are scaled to StitchPattern units (1/6 mm) on load and back to
1/10 mm on save, so patterns keep their physical size whichever format they are
stored in. VCSM measures y from the top of the canvas, StitchPattern from the
bottom.
"""

import math
import os
import struct

from PyQt5.QtGui import QColor

import file_io
from file_io import PatternFileInfo, _tr
from model import StitchPattern, ELEM_STITCH, ELEM_SECTION

VCSM_MAGIC = b"VCSM"
VCSM_UNIT_MM = 0.1  # VCSM stitch coordinates are stored in 1/10 mm units

EXTENSIONS = (".9mm", ".max")


def is_format(header):
    """Return True when *header* starts with the VCSM magic."""
    return header[:4] == VCSM_MAGIC


def _signed24(value):
    """Interpret a 24-bit value as a two's-complement signed number."""
    return value - 0x1000000 if value & 0x800000 else value


def _read_vcsm_text(data, pos):
    """Return (text, new_pos) for a length-prefixed ASCII field at *pos*."""
    if pos >= len(data):
        raise ValueError(_tr("Unexpected end of file in VCSM header"))
    length = data[pos]
    pos += 1
    if pos + length > len(data):
        raise ValueError(_tr("Unexpected end of file in VCSM header"))
    return data[pos:pos + length].decode("latin-1"), pos + length


def _configured_line_color():
    """Return the Display > Line colour setting as an (R, G, B) tuple.

    A pattern without a palette is drawn in that colour, so a VCSM file written
    without an explicit thread colour should sew what the user sees on screen.
    """
    from config import Config  # local import: only saving needs the settings

    color = QColor(Config().get_display_preferences()["line_color"])
    if not color.isValid():
        return (0, 0, 0)
    return color.red(), color.green(), color.blue()


def load(path):
    """Load a VCSM pattern (.9mm or .max file).

    Coordinates are stored in 1/10 mm units while StitchPattern works in
    StitchPattern.STITCH_RES_MM (1/6 mm) units, so the coordinates are scaled to
    preserve physical sizes. Every record is a normal stitch point.
    """
    pattern = StitchPattern()
    file_format = os.path.splitext(path)[1].lstrip(".").upper()
    info = PatternFileInfo(input_file_format=file_format or "UNKNOWN")

    with open(path, 'rb') as f:
        data = f.read()

    # The header itself is 20 bytes; 15 are needed to reach the thread colour.
    if len(data) < 15:
        raise ValueError(_tr("File too short"))
    if data[:4] != VCSM_MAGIC:
        raise ValueError(_tr("Invalid file format"))

    marker = data[4]
    if marker not in (0xE8, 0xE9):
        raise ValueError(_tr("Invalid file format"))

    stitch_type_byte = data[8]
    if stitch_type_byte == 0x00:
        pattern.stitch_type = "9mm"
    elif stitch_type_byte == 0x01:
        pattern.stitch_type = "MAXI"
    else:
        raise ValueError(_tr("Invalid/unsupported stitch type"))
    info.stitch_type = pattern.stitch_type

    # Bytes 12-14 hold the thread colour as R, G, B.
    info.thread_color = (data[12], data[13], data[14])

    notes = ""
    if marker == 0xE9:
        pos = 0x14
        foot, pos = _read_vcsm_text(data, pos)
        needle, pos = _read_vcsm_text(data, pos)
        if pos + 2 > len(data):
            raise ValueError(_tr("Unexpected end of file in VCSM header"))
        tension, = struct.unpack_from("<H", data, pos)
        pos += 2
        pos += 2  # two bytes of unknown purpose between tension and notes length
        if pos >= len(data):
            raise ValueError(_tr("Unexpected end of file in VCSM header"))
        notes_len = data[pos]
        pos += 1
        if notes_len == 0xFF:
            if pos + 2 > len(data):
                raise ValueError(_tr("Unexpected end of file in VCSM header"))
            notes_len, = struct.unpack_from("<H", data, pos)
            pos += 2
        if pos + notes_len > len(data):
            raise ValueError(_tr("Unexpected end of file in VCSM header"))
        notes = data[pos:pos + notes_len].decode("latin-1")
        pos += notes_len
        if pos + 4 > len(data):
            raise ValueError(_tr("Unexpected end of file in VCSM header"))
        image_size, = struct.unpack_from("<I", data, pos)
        pos += 4
        if pos + image_size > len(data):
            raise ValueError(_tr("Unexpected end of file in VCSM header"))
        image_data = data[pos:pos + image_size]
        pos += image_size
        info.background_image_data = image_data or None
        info.sewing_foot = foot or None
        info.needle_system = needle or None
        info.tension = tension / 100
        if file_io.DEBUG:
            print(f"VCSM optional block:\nfoot={foot!r}\nneedle={needle!r}\n"
                  f"tension={tension / 100:.2f}\nnotes={notes!r}\n"
                  f"template image={image_size} bytes")
    else:
        pos = 0x18

    if pos + 4 > len(data):
        raise ValueError(_tr("Unexpected end of file while reading stitch count"))
    stitch_count, = struct.unpack_from("<I", data, pos)
    pos += 4

    if file_io.DEBUG:
        print("Reading VCSM " + pattern.stitch_type + " pattern with "
              + str(stitch_count) + " stitch records, thread colour "
              + "#{:02X}{:02X}{:02X}".format(*info.thread_color) + ":")

    # 1/10 mm per unit in the file vs. 1/6 mm per StitchPattern unit.
    scale = VCSM_UNIT_MM / StitchPattern.STITCH_RES_MM
    # VCSM measures y from the top of the canvas, while StitchPattern uses a
    # bottom-left origin, so y must be mirrored vertically.
    canvas_height = StitchPattern.CANVAS_SIZES[pattern.stitch_type][1]

    all_elems = []
    for i in range(stitch_count):
        if pos + 8 > len(data):
            info.warnings.append(_tr(
                "File appears to be truncated: only {0} of {1} records could be read"
            ).format(i, stitch_count))
            break
        x_int = _signed24(int.from_bytes(data[pos:pos + 3], "little"))
        c0 = data[pos + 3]
        y_int = _signed24(int.from_bytes(data[pos + 4:pos + 7], "little"))
        c1 = data[pos + 7]
        pos += 8
        x = x_int * scale
        y = canvas_height - y_int * scale
        all_elems.append((ELEM_STITCH, x, y))

        if file_io.DEBUG == 1:
            print(f"Read record {i}: x={x:8.3f} y={y:8.3f} c0={c0:3d} c1={c1:3d}")

    info.comment = notes or None
    info.stitch_count = len(all_elems)
    info.color_changes = 0
    info.sections_count = 0
    # VCSM has a single thread colour but we ignore it for now (user sets color manually in the settings window)
    # pattern.colors = [info.thread_color]
    # info.has_color_palette = True
    info.color_count = 1

    pattern._load_elements(all_elems)
    return pattern, info


def save(path, pattern, info=None, image_data=None):
    """Save a StitchPattern as a VCSM file (.9mm or .max).

    The optional header block (marker 0xE9) is written only when metadata or a
    template image is available; otherwise the compact 0xE8 layout is used.
    *image_data* overrides the image stored in *info*; pass ``b""`` to omit it.
    """
    if pattern.stitch_type == "9mm":
        stitch_type_byte = 0x00
    elif pattern.stitch_type == "MAXI":
        stitch_type_byte = 0x01
    else:
        raise ValueError(_tr("Invalid/unsupported stitch type"))

    info = info if info is not None else PatternFileInfo()

    foot = (info.sewing_foot or "").encode("latin-1")
    needle = (info.needle_system or "").encode("latin-1")
    notes = (info.comment or "").encode("latin-1")
    if len(foot) > 255 or len(needle) > 255:
        raise ValueError(_tr("VCSM header text exceeds 255 characters"))
    if len(notes) > 0xFFFF:
        raise ValueError(_tr("VCSM notes exceed 65535 characters"))

    if image_data is None:
        image_data = info.background_image_data or b""

    # Tension is stored as hundredths. 2.50 is the value all sample files use
    # when the header block is present but no explicit tension is known.
    tension = 250 if info.tension is None else round(info.tension * 100)
    tension = max(0, min(0xFFFF, tension))

    has_block = bool(foot or needle or notes or image_data or info.tension is not None)

    # Bytes 12-14 hold the thread colour. Fall back to the pattern's first
    # palette entry, then to the Display > Line colour the canvas draws with.
    if info.thread_color is not None:
        thread_color = info.thread_color
    elif pattern.colors:
        thread_color = pattern.colors[0]
    else:
        thread_color = _configured_line_color()
    if not all(0 <= value <= 0xFF for value in thread_color):
        raise ValueError(_tr("Invalid thread colour"))

    data = bytearray(b"VCSM")
    data.append(0xE9 if has_block else 0xE8)
    data += b"\x03\x00\x00"         # bytes 5-7: constant in all sample files
    data.append(stitch_type_byte)
    data += b"\x00\x00\x00"         # bytes 9-11: constant in all sample files
    data += bytes(thread_color) + b"\x00"  # bytes 12-15: thread colour + zero
    data += b"\x01\x00\x00\x00"     # bytes 16-19: 1 in nearly all samples

    if has_block:
        data.append(len(foot))
        data += foot
        data.append(len(needle))
        data += needle
        data += struct.pack("<H", tension)
        data += b"\x00\x00"         # two unknown bytes, zero in all samples
        if len(notes) < 0xFF:
            data.append(len(notes))
        else:
            data.append(0xFF)
            data += struct.pack("<H", len(notes))
        data += notes
        data += struct.pack("<I", len(image_data))
        data += image_data
    else:
        data += b"\x00\x00\x00\x00"  # bytes 0x14-0x17: padding in 0xE8 files

    # 1/10 mm per file unit vs. 1/6 mm per StitchPattern unit, and y is stored
    # from the top of the canvas.
    scale = VCSM_UNIT_MM / StitchPattern.STITCH_RES_MM
    canvas_height = StitchPattern.CANVAS_SIZES[pattern.stitch_type][1]

    records = bytearray()
    for elem in pattern.display_elements:
        if elem[0] not in (ELEM_STITCH, ELEM_SECTION):
            continue  # VCSM has no colour or section records
        # Round away binary floating-point noise (e.g. 18 / 0.6 = 29.999...).
        x = round(elem[1] / scale, 9)
        y = round((canvas_height - elem[2]) / scale, 9)
        x_int = math.floor(x)
        y_int = math.floor(y)
        c0 = 0
        c1 = 0
        records += x_int.to_bytes(3, "little", signed=True)
        records.append(c0)
        records += y_int.to_bytes(3, "little", signed=True)
        records.append(c1)

    data += struct.pack("<I", len(records) // 8)
    data += records

    with open(path, 'wb') as f:
        f.write(data)
    pattern.modified = False
