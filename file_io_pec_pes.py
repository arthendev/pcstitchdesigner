"""PEC / PES embroidery reader (.pec, .pes files) — import only.

PEC is the machine-level block. PES is a container that always embeds a complete
PEC block at the absolute offset named in its own header, so both extensions are
read by the same code.

PEC layout, offsets from the "LA:" label of the design name:
  0x00  char  "LA:" and the design name, 16 characters, space padded
  0x22  u8    thumbnail stride in bytes
  0x23  u8    thumbnail height in pixels
  0x30  u8    number of colour changes (0xFF = no colour table)
  0x31  u8[]  one PEC colour index per thread slot
  0x200 u16   zero
  0x202 u24   length of the stitch block, measured from 0x200 to its end
  0x205 char  "\\x31\\xFF\\xF0"
  0x208 s16   design width in 0.1 mm
  0x20A s16   design height in 0.1 mm
  0x20C u16   0x01E0
  0x20E u16   0x01B0
  0x210       stitch stream, followed by one 1-bit thumbnail per thread

A standalone .pec file carries the eight-byte "#PEC0001" magic before the
label, so its field offsets are eight bytes higher (the colour count sits at
0x38, the stitch stream at 0x218). A PES header points straight at the label,
which is why this module measures everything from there.

Stitch stream — relative moves in 0.1 mm, Y grows downwards, so it is negated
to match the canvas:
  b7..b0             short stitch: two signed 7-bit deltas
  1 c b11..b8 …      long form: bit 15 marks the long form, bits 14..12 carry
                     the command (0x10 jump, 0x20 trim) and bits 11..0 are a
                     signed 12-bit delta; X and Y each use this form
  0xFE 0xB0 xx       colour change (xx is ignored)
  0xFF 0x00          end of the stitch stream (a lone 0xFF also ends it when it
                     is the last byte of the block)

PES header:
  0x00  char  "#PES" and a four-digit version
  0x08  u32   absolute offset of the embedded PEC block
  Version 5 and 6 headers also name their threads (catalog number, RGB,
  description, brand, chart); those colours are preferred over the PEC table.

Colours: every thread slot names an index into the 64-colour table
(PEC_COLORS, 1-based). A colour change advances to the next slot, and slots
that name the same thread (Wilcom writes them without de-duplicating) share
one palette entry.

Import only: the palette and hoop coordinates do not fit the sewing formats, so
file_io.save_pattern() refuses .pec and .pes paths.

Information sources:
  https://edutechwiki.unige.ch/en/Embroidery_format_PEC
  https://edutechwiki.unige.ch/en/Embroidery_format_PES
  https://github.com/EmbroidePy/pyembroidery/blob/main/pyembroidery/PecReader.py
"""

import struct

import file_io
from file_io import PatternFileInfo, _tr
from model import (DEFAULT_PALETTE, StitchPattern,
                   ELEM_STITCH, ELEM_COLOR)

EXTENSIONS = (".pec", ".pes")

PEC_MAGIC = b"#PEC0001"
PEC_LABEL = b"LA:"
PES_MAGIC = b"#PES"
PES_PEC_OFFSET = 8  # u32 at this offset holds the PEC block's absolute position

# PEC header fields, relative to the "LA:" label (see the module docstring).
COLOR_COUNT_OFFSET = 0x30
COLOR_TABLE_OFFSET = 0x31
STITCH_BLOCK_OFFSET = 0x200
STITCH_LENGTH_OFFSET = 0x202
STITCH_DATA_OFFSET = 0x210

# 0xFF in the colour-count byte means the file carries no colour table.
NO_COLOR_TABLE = 0xFF

LONG_FORM = 0x80
JUMP_CODE = 0x10
TRIM_CODE = 0x20
COLOR_CHANGE = 0xFE
COLOR_CHANGE_SUBCODE = 0xB0
END_BYTE = 0xFF

# PEC moves in 0.1 mm; one canvas unit is 1/6 mm, so a PEC step is 0.6 units.
PEC_UNIT_MM = 0.1
PEC_TO_CANVAS = PEC_UNIT_MM / StitchPattern.STITCH_RES_MM

# No design names more threads than this; the bound keeps a misread header from
# allocating a huge palette.
MAX_THREADS = 256

# Fixed 64-colour table. Index 0 is a placeholder (rendered black);
# PEC thread slots use 1..64, the same numbering the embroidery machine shows.
PEC_COLORS = (
    (0, 0, 0),        # 0  placeholder
    (14, 31, 124),    # 1  Prussian Blue
    (10, 85, 163),    # 2  Blue
    (0, 135, 119),    # 3  Teal Green
    (75, 107, 175),   # 4  Cornflower Blue
    (237, 23, 31),    # 5  Red
    (209, 92, 0),     # 6  Reddish Brown
    (145, 54, 151),   # 7  Magenta
    (228, 154, 203),  # 8  Light Lilac
    (145, 95, 172),   # 9  Lilac
    (158, 214, 125),  # 10 Mint Green
    (232, 169, 0),    # 11 Deep Gold
    (254, 186, 53),   # 12 Orange
    (255, 255, 0),    # 13 Yellow
    (112, 188, 31),   # 14 Lime Green
    (186, 152, 0),    # 15 Brass
    (168, 168, 168),  # 16 Silver
    (125, 111, 0),    # 17 Russet Brown
    (255, 255, 179),  # 18 Cream Brown
    (79, 85, 86),     # 19 Pewter
    (0, 0, 0),        # 20 Black
    (11, 61, 145),    # 21 Ultramarine
    (119, 1, 118),    # 22 Royal Purple
    (41, 49, 51),     # 23 Dark Gray
    (42, 19, 1),      # 24 Dark Brown
    (246, 74, 138),   # 25 Deep Rose
    (178, 118, 36),   # 26 Light Brown
    (252, 187, 197),  # 27 Salmon Pink
    (254, 55, 15),    # 28 Vermilion
    (240, 240, 240),  # 29 White
    (106, 28, 138),   # 30 Violet
    (168, 221, 196),  # 31 Seacrest
    (37, 132, 187),   # 32 Sky Blue
    (254, 179, 67),   # 33 Pumpkin
    (255, 243, 107),  # 34 Cream Yellow
    (208, 166, 96),   # 35 Khaki
    (209, 84, 0),     # 36 Clay Brown
    (102, 186, 73),   # 37 Leaf Green
    (19, 74, 70),     # 38 Peacock Blue
    (135, 135, 135),  # 39 Gray
    (216, 204, 198),  # 40 Warm Gray
    (67, 86, 7),      # 41 Dark Olive
    (253, 217, 222),  # 42 Flesh Pink
    (249, 147, 188),  # 43 Pink
    (0, 56, 34),      # 44 Deep Green
    (178, 175, 212),  # 45 Lavender
    (104, 106, 176),  # 46 Wisteria Violet
    (239, 227, 185),  # 47 Beige
    (247, 56, 102),   # 48 Carmine
    (181, 75, 100),   # 49 Amber Red
    (19, 43, 26),     # 50 Olive Green
    (199, 1, 86),     # 51 Dark Fuchsia
    (254, 158, 50),   # 52 Tangerine
    (168, 222, 235),  # 53 Light Blue
    (0, 103, 62),     # 54 Emerald Green
    (78, 41, 144),    # 55 Purple
    (47, 126, 32),    # 56 Moss Green
    (255, 204, 204),  # 57 Flesh Pink
    (255, 217, 17),   # 58 Harvest Gold
    (9, 91, 166),     # 59 Electric Blue
    (240, 249, 112),  # 60 Lemon Yellow
    (227, 243, 91),   # 61 Fresh Green
    (255, 153, 0),    # 62 Orange
    (255, 240, 141),  # 63 Cream Yellow
    (255, 200, 200),  # 64 Applique
)

# PES versions whose header names its threads, and the fixed skips that lead
# from the end of the description strings to the thread count. The tuples are
# (skip to hoop name or image name, skip to image name, skip after image name);
# None marks a version without the hoop-name string.
_THREAD_HEADER_LAYOUTS = {
    b"0050": (24, None, 24),
    b"0055": (24, None, 24),
    b"0056": (24, None, 24),
    b"0060": (36, None, 24),
    b"0070": (36, None, 24),
    b"0080": (38, None, 26),
    b"0090": (14, 30, 34),
    b"0100": (14, 38, 34),
}


def is_format(header):
    """Return True when *header* starts like a PEC file or a PES container."""
    return header[:len(PEC_MAGIC)] == PEC_MAGIC or header[:len(PES_MAGIC)] == PES_MAGIC


def _signed7(byte):
    """Interpret a short-form coordinate byte as a 7-bit signed number."""
    return byte - 128 if byte > 63 else byte


def _signed12(value):
    """Interpret the low 12 bits of *value* as a signed number."""
    value &= 0xFFF
    return value - 0x1000 if value > 0x7FF else value


def _skip_strings(data, pos, count, limit):
    """Skip *count* length-prefixed strings and return the position after them.

    Raises:
        ValueError: when a string would run past *limit*.
    """
    for _ in range(count):
        if pos >= limit:
            raise ValueError("string header past the end of the section")
        length = data[pos]
        pos += 1 + length
        if pos > limit:
            raise ValueError("string past the end of the section")
    return pos


def _read_pes_threads(data, limit):
    """Return the thread colours named by a PES header, or [] when it has none.

    Only the version 5 and 6 headers describe threads; older versions keep the
    colours in the PEC block, which the caller falls back to. A header whose
    layout does not match — extra programmable fills, motifs or feather
    patterns, or one that is cut short — also returns [] instead of guessing,
    so the design still loads with the colours the file stores for the machine.
    """
    layout = _THREAD_HEADER_LAYOUTS.get(data[4:8])
    if layout is None:
        return []
    skip_to_first_name, skip_to_image, skip_after_image = layout
    try:
        # Name, category, author, keywords and comments.
        pos = _skip_strings(data, 12 + 4, 5, limit)
        pos += skip_to_first_name
        if skip_to_image is not None:
            pos = _skip_strings(data, pos, 1, limit)  # hoop name
            pos += skip_to_image
        pos = _skip_strings(data, pos, 1, limit)      # image file name
        pos += skip_after_image
        fills, motifs, feathers, thread_count = struct.unpack_from("<4H", data, pos)
        pos += 8
    except (ValueError, struct.error):
        return []

    if fills or motifs or feathers or thread_count > MAX_THREADS:
        return []

    colors = []
    try:
        for _ in range(thread_count):
            pos = _skip_strings(data, pos, 1, limit)  # catalog number
            if pos + 3 > limit:
                return []
            red, green, blue = data[pos], data[pos + 1], data[pos + 2]
            pos += 3 + 5  # RGB plus the four-byte type and padding
            pos = _skip_strings(data, pos, 3, limit)  # description, brand, chart
            colors.append((red, green, blue))
    except (ValueError, IndexError):
        return []
    return colors


def _slot_colors(pec_indices, pes_threads):
    """Return one thread colour for each PEC thread slot.

    A PEC slot names a colour from the 64-colour table. When the PES header names
    its own threads, a slot with the same PEC index always means the same
    thread, so repeated indices share one thread; PE-Design writes one header
    thread per used thread that way.
    """
    if not pec_indices:
        return []
    if not pes_threads or len(pes_threads) >= len(pec_indices):
        # One header thread per slot: the file used each thread once.
        if pes_threads:
            return list(pes_threads[:len(pec_indices)])
        return [PEC_COLORS[index % len(PEC_COLORS)] for index in pec_indices]

    mapping = {}
    colors = []
    next_thread = 0
    for index in pec_indices:
        if index not in mapping:
            if next_thread < len(pes_threads):
                mapping[index] = pes_threads[next_thread]
                next_thread += 1
            else:
                mapping[index] = PEC_COLORS[index % len(PEC_COLORS)]
        colors.append(mapping[index])
    return colors


def _unique_palette(slot_colors):
    """Return the distinct thread colours and each slot's palette index."""
    colors = []
    slot_index = []
    for rgb in slot_colors:
        if rgb not in colors:
            colors.append(rgb)
        slot_index.append(colors.index(rgb))
    return colors, slot_index


def _read_stitch_stream(data, start, end, slot_index):
    """Decode the PEC stitch stream between *start* and *end*.

    Returns:
        tuple: ``(elements, stitches, jumps, color_changes, truncated)``.
        *elements* holds ``ELEM_STITCH`` and ``ELEM_COLOR`` records; jumps and
        trims only move the needle, so they leave the needle without adding a
        stitch, just as the other third-party readers do.
    """
    elements = []
    # Without a colour table the window supplies the default palette, so the
    # change records still need a length to cycle in.
    slot = slot_index or list(range(len(DEFAULT_PALETTE)))
    x = 0
    y = 0
    stitches = 0
    jumps = 0
    color_changes = 0
    truncated = False
    pos = start

    while pos < end:
        b0 = data[pos]
        # 0xFF opens a long-form record as well (command bits plus data), so it
        # only ends the stream as the explicit 0xFF 0x00 record or as the last
        # byte of the block.
        if b0 == END_BYTE and (pos + 1 >= end or data[pos + 1] == 0x00):
            break
        if pos + 2 > end:
            truncated = True
            break
        b1 = data[pos + 1]

        if b0 == COLOR_CHANGE and b1 == COLOR_CHANGE_SUBCODE:
            if pos + 3 > end:
                truncated = True
                break
            color_changes += 1
            elements.append((ELEM_COLOR, slot[color_changes % len(slot)]))
            pos += 3
            continue

        command = 0
        # The long-form flag is per axis: a short X may be followed by a long Y,
        # so each coordinate decides its own length.
        if b0 & LONG_FORM:
            command |= b0
            dx = _signed12((b0 << 8) | b1)
            pos += 2
            if pos >= end:
                truncated = True
                break
            y_first = data[pos]
        else:
            dx = _signed7(b0)
            y_first = b1
            pos += 1

        if y_first & LONG_FORM:
            command |= y_first
            if pos + 1 >= end:
                truncated = True
                break
            dy = _signed12((y_first << 8) | data[pos + 1])
            pos += 2
        else:
            dy = _signed7(y_first)
            pos += 1

        # PEC stores Y downwards; the canvas has its origin at the bottom left.
        x += dx
        y -= dy
        if command & (JUMP_CODE | TRIM_CODE):
            jumps += 1
        else:
            elements.append((ELEM_STITCH, x, y))
            stitches += 1

        if file_io.DEBUG == 1:
            print(f"PEC record at ({x}, {y})")

    return elements, stitches, jumps, color_changes, truncated


def load(path):
    """Load a PEC file or the PEC block of a PES file as a hoop pattern.

    The thread table becomes the pattern palette, so the design keeps the
    colours the file (or the PES header) names.

    Returns ``(pattern, info)``; see file_io.load_pattern() for the contract.
    """
    pattern = StitchPattern()
    info = PatternFileInfo()

    with open(path, "rb") as f:
        data = f.read()

    if data[:len(PEC_MAGIC)] == PEC_MAGIC:
        # Standalone PEC: the label follows the magic.
        label = len(PEC_MAGIC)
        info.input_file_format = "PEC"
        pes_threads = []
    elif data[:len(PES_MAGIC)] == PES_MAGIC:
        if len(data) < PES_PEC_OFFSET + 4:
            raise ValueError(_tr("File too short"))
        pec_offset, = struct.unpack_from("<I", data, PES_PEC_OFFSET)
        info.input_file_format = "PES"
        if data[pec_offset:pec_offset + len(PEC_MAGIC)] == PEC_MAGIC:
            # A writer that points at the magic rather than the label.
            label = pec_offset + len(PEC_MAGIC)
        elif data[pec_offset:pec_offset + len(PEC_LABEL)] == PEC_LABEL:
            label = pec_offset
        else:
            raise ValueError(_tr("Invalid file format"))
        pes_threads = _read_pes_threads(data, pec_offset)
        if file_io.DEBUG:
            print(f"\n\nReading PES {data[4:8].decode('latin-1')} with "
                  f"{len(pes_threads)} header threads, PEC block at {label}")
    else:
        raise ValueError(_tr("Invalid file format"))

    if label + STITCH_DATA_OFFSET > len(data):
        raise ValueError(_tr("File too short"))

    color_byte = data[label + COLOR_COUNT_OFFSET]
    if color_byte == NO_COLOR_TABLE:
        pec_indices = b""
    else:
        table_length = color_byte + 1
        table_end = label + COLOR_TABLE_OFFSET + table_length
        if table_end > label + STITCH_BLOCK_OFFSET:
            raise ValueError(_tr("Invalid file format"))
        pec_indices = data[label + COLOR_TABLE_OFFSET:table_end]

    slot_colors = _slot_colors(pec_indices, pes_threads)
    colors, slot_index = _unique_palette(slot_colors)
    pattern.colors = colors

    if file_io.DEBUG:
        print(f"Reading {info.input_file_format} pattern with {len(colors)} "
              f"threads ({len(pec_indices)} slots)")

    # The length field covers the stitch block header and the stitch stream, so
    # it tells exactly where the thumbnails begin. A nonsensical value is
    # ignored: the 0xFF terminator still ends the stream.
    block_length = int.from_bytes(
        data[label + STITCH_LENGTH_OFFSET:label + STITCH_LENGTH_OFFSET + 3],
        "little")
    stitch_end = label + STITCH_BLOCK_OFFSET + block_length
    if not label + STITCH_DATA_OFFSET <= stitch_end <= len(data):
        stitch_end = len(data)

    elements, stitches, jumps, color_changes, truncated = _read_stitch_stream(
        data, label + STITCH_DATA_OFFSET, stitch_end, slot_index)

    if not stitches:
        raise ValueError(_tr("The PEC file contains no stitches"))
    if truncated:
        info.warnings.append(_tr(
            "The PEC file ends in the middle of a stitch record"))

    color_changes -= file_io.drop_trailing_color_changes(elements)

    # PEC coordinates are deltas from the design origin, so only the units are
    # converted here. The hoop is chosen by file_io.load_pattern(), after the
    # size in the file has been reported.
    info.needs_hoop_placement = True
    info.stitch_count = stitches
    info.has_color_palette = bool(colors)
    info.color_count = len(colors)
    info.color_changes = color_changes

    pattern._load_elements(file_io.to_canvas_units(elements, PEC_TO_CANVAS))
    if file_io.DEBUG:
        print(f"{info.input_file_format} design read; {stitches} stitches, "
              f"{jumps} jumps, {color_changes} colour changes")
    return pattern, info
