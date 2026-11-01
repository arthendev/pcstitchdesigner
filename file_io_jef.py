"""Janome JEF embroidery reader (.jef files) — import only.

File format:
  Header: the first 0x74 (116) bytes describe the design:

    0x00  u32   offset of the stitch stream (normally 0x74 + 8 × thread count)
    0x04  u32   0x14 — constant marker
    0x08  char  date and time the design was written (16 bytes)
    0x18  u32   thread count: the first thread plus one per colour change
    0x1C  u32   length of the stitch stream in 16-bit words
    0x20  u32   Janome hoop code (1 = 50×50 mm, 3 = 126×110 mm, …)

  Thread table: one u32 per thread starting at 0x74. The value is an index into
  the JEF thread table (JEF_COLORS); a negative value names the same thread.
  The table is followed by one 0x0D marker word per thread, which is why the
  stitch stream starts at 0x74 + 8 × thread count.

  Stitch stream: two bytes per record.

    dx dy             stitch — signed 8-bit steps of 0.1 mm
    0x80 0x01 dx dy   colour change / stop (0x03 and 0x05 mean the same)
    0x80 0x02 dx dy   jump / trim (0x04 and 0x06 mean the same)
    0x80 0x10         end of design — no coordinate bytes follow

  X grows to the right and Y upwards, which is the canvas orientation, so no
  axis is flipped. One step is 0.1 mm, i.e. 0.6 canvas units (see
  StitchPattern.STITCH_RES_MM).

  (pyembroidery negates Y because its own coordinates grow downwards;
  libembroidery reads Y as stored, which is what this reader does.)

The format has no magic number, so a file is recognised by its header layout:
the stream offset has to equal 0x74 + 8 × thread count. file_io.load_pattern()
also accepts a .jef file whose header does not match that layout, because some
writers pad the header; this module validates the header again on load.

Import only: the pattern palette and hoop coordinates of a hoop design do not
fit the sewing formats, so file_io.save_pattern() refuses .jef paths.

Information sources:
  https://edutechwiki.unige.ch/en/Embroidery_format_JEF
  https://github.com/EmbroidePy/pyembroidery/blob/main/pyembroidery/JefReader.py
"""

import struct

import file_io
from file_io import PatternFileInfo, _tr
from model import (DEFAULT_PALETTE, StitchPattern,
                   ELEM_STITCH, ELEM_COLOR)

HEADER_SIZE = 0x74
MARKER_OFFSET = 0x04
MARKER_VALUE = 0x14
THREAD_COUNT_OFFSET = 0x18
HOOP_OFFSET = 0x20

# Threads are stored as one 32-bit index each, followed by the same number of
# 0x0D marker words, hence 8 bytes per thread before the stitch stream.
THREAD_TABLE_SIZE = 8

# No design has more threads than this; the bound keeps the header signature
# from matching arbitrary binary files.
MAX_THREADS = 256

# Stitch stream commands, recognised by the second byte of a 0x80 0x?? record.
# Janome writers use the neighbouring values for the same meaning, so the sets
# cover the variants libembroidery accepts.
COLOR_CHANGE_COMMANDS = (0x01, 0x03, 0x05)
JUMP_COMMANDS = (0x02, 0x04, 0x06)
COMMAND_END = 0x10

# JEF moves in 0.1 mm; one canvas unit is 1/6 mm, so a JEF step is 0.6 units.
JEF_UNIT_MM = 0.1
JEF_TO_CANVAS = JEF_UNIT_MM / StitchPattern.STITCH_RES_MM

EXTENSIONS = (".jef",)

# Janome's fixed thread table, as used by the writer that creates these files.
# Index 0 is a placeholder (rendered black); each following entry is the thread
# a colour-change record can select.
JEF_COLORS = (
    (0, 0, 0),        # 0  placeholder — "unknown" in the Janome table
    (0, 0, 0),        # 1  black
    (255, 255, 255),  # 2  white
    (255, 255, 23),   # 3  yellow
    (255, 102, 0),    # 4  orange
    (47, 89, 51),     # 5  olive green
    (35, 115, 54),    # 6  green
    (101, 194, 200),  # 7  sky
    (171, 90, 150),   # 8  purple
    (246, 105, 160),  # 9  pink
    (255, 0, 0),      # 10 red
    (177, 112, 78),   # 11 brown
    (11, 47, 132),    # 12 blue
    (228, 195, 93),   # 13 gold
    (72, 26, 5),      # 14 dark brown
    (172, 156, 199),  # 15 pale violet
    (252, 242, 148),  # 16 pale yellow
    (249, 153, 183),  # 17 pale pink
    (250, 179, 129),  # 18 peach
    (201, 164, 128),  # 19 beige
    (151, 5, 51),     # 20 wine red
    (160, 184, 204),  # 21 pale sky
    (127, 194, 28),   # 22 yellow green
    (229, 229, 229),  # 23 silver gray
    (136, 155, 155),  # 24 gray
    (152, 214, 189),  # 25 pale aqua
    (178, 225, 227),  # 26 baby blue
    (54, 139, 160),   # 27 powder blue
    (79, 131, 171),   # 28 bright blue
    (56, 106, 145),   # 29 slate blue
    (7, 22, 80),      # 30 navy blue
    (249, 153, 162),  # 31 salmon pink
    (249, 103, 107),  # 32 coral
    (227, 49, 31),    # 33 burnt orange
    (226, 161, 136),  # 34 cinnamon
    (181, 148, 116),  # 35 umber
    (228, 207, 153),  # 36 blond
    (255, 203, 0),    # 37 sunflower
    (225, 173, 212),  # 38 orchid pink
    (195, 0, 126),    # 39 peony purple
    (128, 0, 75),     # 40 burgundy
    (84, 5, 113),     # 41 royal purple
    (177, 5, 37),     # 42 cardinal red
    (202, 224, 192),  # 43 opal green
    (137, 152, 86),   # 44 moss green
    (92, 148, 26),    # 45 meadow green
    (0, 49, 20),      # 46 dark green
    (93, 174, 148),   # 47 aquamarine
    (76, 191, 143),   # 48 emerald green
    (0, 119, 114),    # 49 peacock green
    (89, 91, 97),     # 50 dark gray
    (255, 255, 242),  # 51 ivory white
    (177, 88, 24),    # 52 hazel
    (203, 138, 7),    # 53 toast
    (152, 108, 128),  # 54 salmon
    (152, 105, 45),   # 55 cocoa brown
    (77, 52, 25),     # 56 sienna
    (76, 51, 11),     # 57 sepia
    (51, 32, 10),     # 58 dark sepia
    (82, 58, 151),    # 59 violet blue
    (13, 33, 126),    # 60 blue ink
    (30, 119, 172),   # 61 sola blue
    (178, 221, 83),   # 62 green dust
    (243, 54, 137),   # 63 crimson
    (222, 100, 158),  # 64 floral pink
    (152, 65, 97),    # 65 wine
    (76, 86, 18),     # 66 olive drab
    (76, 136, 31),    # 67 meadow
    (228, 222, 121),  # 68 mustard
    (203, 138, 26),   # 69 yellow ocher
    (203, 162, 28),   # 70 old gold
    (255, 152, 5),    # 71 honey dew
    (252, 178, 87),   # 72 tangerine
    (255, 229, 5),    # 73 canary yellow
    (240, 51, 31),    # 74 vermilion
    (26, 132, 45),    # 75 bright green
    (56, 108, 174),   # 76 ocean blue
    (227, 196, 180),  # 77 beige gray
    (227, 172, 129),  # 78 bamboo
)


def is_format(header):
    """Return True when *header* starts like a Janome JEF file.

    JEF has no magic number, so the layout itself is the signature: the
    constant marker at 0x04 plus a stitch-stream offset that matches the
    thread table length.
    """
    if len(header) < THREAD_COUNT_OFFSET + 4:
        return False
    marker, = struct.unpack_from("<I", header, MARKER_OFFSET)
    if marker != MARKER_VALUE:
        return False
    thread_count, = struct.unpack_from("<I", header, THREAD_COUNT_OFFSET)
    if thread_count > MAX_THREADS:
        return False
    data_offset, = struct.unpack_from("<I", header, 0)
    return data_offset == HEADER_SIZE + THREAD_TABLE_SIZE * thread_count


def _signed(byte):
    """Interpret one stream byte as a two's-complement signed number."""
    return byte - 256 if byte > 127 else byte


def _drop_trailing_color_changes(elements):
    """Remove colour changes that no stitch follows; return how many."""
    dropped = 0
    while elements and elements[-1][0] == ELEM_COLOR:
        elements.pop()
        dropped += 1
    return dropped


def load(path):
    """Load a Janome JEF file as a hoop pattern.

    The thread table the file carries becomes the pattern palette, so the
    design keeps the thread colours it names.

    Returns ``(pattern, info)``; see file_io.load_pattern() for the contract.
    """
    pattern = StitchPattern()
    info = PatternFileInfo(input_file_format="JEF")

    with open(path, "rb") as f:
        data = f.read()
    if len(data) < HEADER_SIZE:
        raise ValueError(_tr("File too short"))

    data_offset, = struct.unpack_from("<I", data, 0)
    thread_count, = struct.unpack_from("<I", data, THREAD_COUNT_OFFSET)
    hoop_code, = struct.unpack_from("<I", data, HOOP_OFFSET)
    # The thread table sits between the header and the stitch stream, so the
    # offsets have to leave room for it.
    if (thread_count > MAX_THREADS
            or data_offset < HEADER_SIZE + 4 * thread_count
            or data_offset > len(data)):
        raise ValueError(_tr("Invalid file format"))

    colors = []
    for index in range(thread_count):
        raw, = struct.unpack_from("<i", data, HEADER_SIZE + 4 * index)
        colors.append(JEF_COLORS[abs(raw) % len(JEF_COLORS)])
    pattern.colors = colors
    # A file without a thread table is loaded without a palette and the window
    # supplies the default, so colour indices still need a length to cycle in.
    palette_len = len(colors) or len(DEFAULT_PALETTE)

    if file_io.DEBUG:
        print(f"\n\nReading JEF pattern: {thread_count} threads, "
              f"stitch data at {data_offset:#06x}, Janome hoop code {hoop_code}")

    body = data[data_offset:]
    all_elems = []
    x = 0
    y = 0
    stitches = 0
    jumps = 0
    color_changes = 0
    ended = False
    truncated = False
    offset = 0

    while offset + 2 <= len(body):
        b0, b1 = body[offset], body[offset + 1]
        if b0 != 0x80:
            x += _signed(b0)
            y += _signed(b1)
            all_elems.append((ELEM_STITCH, x, y))
            stitches += 1
            offset += 2
            if file_io.DEBUG == 1:
                print(f"Stitch {stitches} at ({x}, {y})")
            continue

        if b1 == COMMAND_END:
            ended = True
            offset += 2
            break
        if offset + 4 > len(body):
            truncated = True
            break
        # A command carries the movement to the next stitch; the following
        # records are deltas from that position, so it has to be applied even
        # though the command itself adds no stitch.
        x += _signed(body[offset + 2])
        y += _signed(body[offset + 3])
        offset += 4

        if b1 in COLOR_CHANGE_COMMANDS:
            color_changes += 1
            all_elems.append((ELEM_COLOR, color_changes % palette_len))
        elif b1 in JUMP_COMMANDS:
            jumps += 1
        else:
            info.warnings.append(_tr(
                "Unsupported JEF command 0x{0:02X}; reading stopped").format(b1))
            break

        if file_io.DEBUG > 1:
            print(f"Command 0x{b1:02X} at ({x}, {y})")

    if not stitches:
        raise ValueError(_tr("The JEF file contains no stitches"))

    if not ended and (truncated or len(body) % 2):
        info.warnings.append(_tr(
            "The JEF file ends in the middle of a stitch record"))

    color_changes -= _drop_trailing_color_changes(all_elems)

    # JEF coordinates are relative to the design origin, so the design is
    # centred in the smallest hoop that holds it — the same placement the DST
    # reader uses.
    pattern.stitch_type, converted, warning = file_io.place_in_hoop(
        all_elems, JEF_TO_CANVAS)
    if warning:
        info.warnings.append(warning)

    info.stitch_type = pattern.stitch_type
    info.stitch_count = stitches
    info.has_color_palette = bool(colors)
    info.color_count = len(colors)
    info.color_changes = color_changes

    pattern._load_elements(converted)
    if file_io.DEBUG:
        print(f"JEF design placed in {pattern.stitch_type}; {stitches} stitches, "
              f"{jumps} jumps, {color_changes} colour changes")
    return pattern, info
