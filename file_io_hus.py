"""HUS embroidery reader (.hus files) — import only.

File format:
  Header (little-endian):

    0x00 u32   magic; the upper half is always 0x00C8, the lower half varies
    0x04 u32   number of stitch records
    0x08 u32   number of threads
    0x0C s16   design extent, positive X
    0x0E s16   design extent, positive Y
    0x10 s16   design extent, negative X
    0x12 s16   design extent, negative Y
    0x14 u32   offset of the command stream
    0x18 u32   offset of the X stream
    0x1C u32   offset of the Y stream
    0x20 char  eight bytes of unknown text
    0x28 u16   unknown, always zero in the samples seen
    0x2A u16[] one HUS thread index per thread

  Three streams follow, each compressed on its own: one byte per stitch for the
  command, one signed byte per stitch for the X delta and one signed byte per
  stitch for the Y delta. X and Y are relative moves in 0.1 mm, Y measured
  upwards like the canvas, so no axis is flipped.

  Command bytes:
    0x80 stitch        0x81 jump        0x84 colour change
    0x88 trim          0x90 end

Compression:
  Each stream is decompressed by greenleaf_lzss.decompress(), the codec HUS and
  VIP share; that module documents the block layout and the token numbering.

The thread table is the fixed 29-colour palette below (HUS_COLORS).

Information sources:
  https://edutechwiki.unige.ch/en/Embroidery_format_HUS
  https://github.com/EmbroidePy/pyembroidery/blob/main/pyembroidery/HusReader.py
"""

import greenleaf_lzss
import struct

import file_io
from file_io import PatternFileInfo, _tr
from model import (DEFAULT_PALETTE, StitchPattern,
                   ELEM_STITCH, ELEM_COLOR)

EXTENSIONS = (".hus",)

# The magic is a u32 whose high half is fixed; the low half differs between
# files (5BAF and 5DFC both occur in the samples seen).
MAGIC_HIGH = b"\xc8\x00"
HEADER_SIZE = 0x2A

COMMAND_STITCH = 0x80
COMMAND_JUMP = 0x81
COMMAND_COLOR_CHANGE = 0x84
COMMAND_TRIM = 0x88
COMMAND_END = 0x90

# HUS moves in 0.1 mm; one canvas unit is 1/6 mm, so a HUS step is 0.6 units.
HUS_UNIT_MM = 0.1
HUS_TO_CANVAS = HUS_UNIT_MM / StitchPattern.STITCH_RES_MM

# Sanity bounds for a corrupt header or stream.
MAX_STITCHES = 5_000_000
MAX_COLORS = 256

# Fixed 29-colour thread table, in the order the header indexes it.
HUS_COLORS = (
    (0, 0, 0),        # 0  black
    (0, 0, 231),      # 1  blue
    (0, 198, 0),      # 2  green
    (255, 0, 0),      # 3  red
    (132, 0, 132),    # 4  purple
    (255, 255, 0),    # 5  yellow
    (132, 132, 132),  # 6  grey
    (132, 132, 231),  # 7  light blue
    (0, 255, 132),    # 8  light green
    (255, 123, 49),   # 9  orange
    (255, 140, 165),  # 10 pink
    (132, 82, 0),     # 11 brown
    (255, 255, 255),  # 12 white
    (0, 0, 132),      # 13 dark blue
    (0, 132, 0),      # 14 dark green
    (123, 0, 0),      # 15 dark red
    (255, 99, 132),   # 16 light red
    (82, 41, 82),     # 17 dark purple
    (255, 0, 255),    # 18 light purple
    (255, 222, 0),    # 19 dark yellow
    (255, 255, 156),  # 20 light yellow
    (82, 82, 82),     # 21 dark grey
    (214, 214, 214),  # 22 light grey
    (255, 82, 8),     # 23 dark orange
    (255, 156, 90),   # 24 light orange
    (255, 82, 181),   # 25 dark pink
    (255, 198, 222),  # 26 light pink
    (82, 49, 0),      # 27 dark brown
    (181, 165, 132),  # 28 light brown
)


def is_format(header):
    """Return True when *header* starts like a HUS file."""
    return len(header) >= 4 and header[2:4] == MAGIC_HIGH


def _slot_index(slot_colors):
    """Return the distinct thread colours and each slot's palette index."""
    colors = []
    slot_index = []
    for rgb in slot_colors:
        if rgb not in colors:
            colors.append(rgb)
        slot_index.append(colors.index(rgb))
    return colors, slot_index


def load(path):
    """Load a HUS file as a hoop pattern.

    The thread table becomes the pattern palette, so the design keeps the
    colours the file names.

    Returns ``(pattern, info)``; see file_io.load_pattern() for the contract.
    """
    pattern = StitchPattern()
    info = PatternFileInfo(input_file_format="HUS")

    with open(path, "rb") as f:
        data = f.read()
    if len(data) < HEADER_SIZE:
        raise ValueError(_tr("File too short"))
    if data[2:4] != MAGIC_HIGH:
        raise ValueError(_tr("Invalid file format"))

    stitch_count, = struct.unpack_from("<I", data, 4)
    color_count, = struct.unpack_from("<I", data, 8)
    command_offset, = struct.unpack_from("<I", data, 0x14)
    x_offset, = struct.unpack_from("<I", data, 0x18)
    y_offset, = struct.unpack_from("<I", data, 0x1C)

    table_end = HEADER_SIZE + 2 * color_count
    if (stitch_count > MAX_STITCHES or color_count > MAX_COLORS
            or not table_end <= command_offset <= x_offset <= y_offset <= len(data)):
        raise ValueError(_tr("Invalid file format"))

    slot_colors = []
    for index in range(color_count):
        value, = struct.unpack_from("<H", data, HEADER_SIZE + 2 * index)
        slot_colors.append(HUS_COLORS[value % len(HUS_COLORS)])
    colors, slot_index = _slot_index(slot_colors)
    pattern.colors = colors

    if file_io.DEBUG:
        print(f"\n\nReading HUS pattern: {stitch_count} records, "
              f"{color_count} threads, streams at {command_offset}/"
              f"{x_offset}/{y_offset}")

    try:
        commands = greenleaf_lzss.decompress(data[command_offset:x_offset],
                                             stitch_count)
        x_stream = greenleaf_lzss.decompress(data[x_offset:y_offset],
                                             stitch_count)
        y_stream = greenleaf_lzss.decompress(data[y_offset:], stitch_count)
    except ValueError:
        raise ValueError(_tr("Invalid file format"))

    # Without a colour table the window supplies the default palette, so the
    # change records still need a length to cycle in.
    slot = slot_index or list(range(len(DEFAULT_PALETTE)))
    elements = []
    x = 0
    y = 0
    stitches = 0
    jumps = 0
    color_changes = 0
    truncated = (len(commands) < stitch_count
                 or len(x_stream) < stitch_count
                 or len(y_stream) < stitch_count)

    for command, dx_byte, dy_byte in zip(commands, x_stream, y_stream):
        dx = file_io.signed_byte(dx_byte)
        dy = file_io.signed_byte(dy_byte)
        if command == COMMAND_STITCH:
            x += dx
            y += dy
            elements.append((ELEM_STITCH, x, y))
            stitches += 1
        elif command == COMMAND_JUMP:
            x += dx
            y += dy
            jumps += 1
        elif command == COMMAND_COLOR_CHANGE:
            x += dx
            y += dy
            color_changes += 1
            elements.append((ELEM_COLOR, slot[color_changes % len(slot)]))
        elif command == COMMAND_TRIM:
            # A trim only moves the needle; the model has no record for it.
            x += dx
            y += dy
            jumps += 1
        elif command == COMMAND_END:
            break
        else:
            info.warnings.append(_tr(
                "Unsupported HUS command 0x{0:02X}; reading stopped").format(command))
            break

    if not stitches:
        raise ValueError(_tr("The HUS file contains no stitches"))
    if truncated:
        info.warnings.append(_tr(
            "The HUS file ends in the middle of a stitch record"))

    color_changes -= file_io.drop_trailing_color_changes(elements)

    # HUS coordinates are deltas from the design origin, so only the units are
    # converted here. The hoop is chosen by file_io.load_pattern(), after the
    # size in the file has been reported.
    info.needs_hoop_placement = True
    info.stitch_count = stitches
    info.has_color_palette = bool(colors)
    info.color_count = len(colors)
    info.color_changes = color_changes

    pattern._load_elements(file_io.to_canvas_units(elements, HUS_TO_CANVAS))
    if file_io.DEBUG:
        print(f"HUS design read; {stitches} stitches, {jumps} jumps, "
              f"{color_changes} colour changes")
    return pattern, info
