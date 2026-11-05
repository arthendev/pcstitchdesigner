"""VP3 embroidery reader (.vp3 files) — import only.

Unlike the delta-only formats, VP3 is a series of nested tagged sections
(all big-endian):

  Header
    "%vsm%" NUL, then u16be byte length and the UTF-16BE production string.

  Section 0x020 — embroidery summary (tag 00 02 00)
    u32be  bytes left in the file
    string design name (u16be byte length, UTF-16BE)
    s32be  extents right, top, left, bottom in micrometres
    u32be  stitch-time count (an estimate, not the exact stitch count)
    u16be  thread-change count, u8 unknown, u16be hoop count

  Section 0x030 — hoop (tag 00 03 00)
    u32be  bytes left in the design, s32be centre X and Y,
    3 unknown bytes, 6 x s32be hoop extents and size,
    string notes, 2 unknown bytes, 4 x u32be (4096, 0, 0, 4096),
    "xxPP" (or "xxUU"), 2 unknown bytes,
    string production string, u16be colour-block count

  Colour blocks — one per thread (tag 00 05 00)
    u32be  body length (bytes after this seven-byte header)
    s32be  start X and Y in micrometres
    u8     colour entries in this thread, then 6 bytes each
           (unknown, RGB, two unknown bytes), then one spare byte
    u8     thread material, u8 thread weight
    string catalogue number, string description, string brand (UTF-8)
    s32be  position shift X and Y
    stitch run (tag 00 01 00): u32be run length, 3 unknown bytes, then records
    padding to the end of the block

A stitch record is two signed bytes, the X and Y delta in 0.1 mm. A leading 0x80
is an escape: 0x80 0x00 and 0x80 0x03 are empty records, 0x80 0x01 starts a jump
whose 16-bit X and Y follow and is closed by 0x80 0x02. VP3 measures Y downwards,
so it is negated for the canvas.

Each colour block is one thread, so the block colours become the pattern palette
and one colour change separates consecutive blocks.

Information sources:
  https://community.kde.org/Projects/Liberty/File_Formats/Viking_Pfaff
  https://edutechwiki.unige.ch/en/Embroidery_format_VP3
  https://www.jasonweiler.com/VP3FileFormatInfo.html
"""

import struct

import file_io
from file_io import PatternFileInfo, _tr
from model import StitchPattern, ELEM_STITCH, ELEM_COLOR

EXTENSIONS = (".vp3",)

MAGIC = b"%vsm%\x00"

TAG_SUMMARY = b"\x00\x02\x00"
TAG_HOOP = b"\x00\x03\x00"
TAG_THREAD = b"\x00\x05\x00"
TAG_RUN = b"\x00\x01\x00"
# Both spellings occur: the section marker seen in the samples of each writer.
DESIGN_MAGIC = (b"xxPP", b"xxUU")

# The summary tag holds a u16be length and the UTF-16BE design name.
HEADER_SIZE = 8  # magic plus the production-string length

ESCAPE = 0x80
ESCAPE_EMPTY = (0x00, 0x03)
ESCAPE_JUMP = 0x01
JUMP_END = b"\x80\x02"
RUN_HEADER = 3  # bytes between the run length and the first stitch record

# VP3 moves in 0.1 mm; one canvas unit is 1/6 mm, so a VP3 step is 0.6 units.
VP3_UNIT_MM = 0.1
VP3_TO_CANVAS = VP3_UNIT_MM / StitchPattern.STITCH_RES_MM

# Sanity bounds for a corrupt header.
MAX_STITCHES = 5_000_000
MAX_BLOCKS = 10_000


def _u16(data, pos):
    """Return the big-endian u16 at *pos*."""
    return struct.unpack_from(">H", data, pos)[0]


def _u32(data, pos):
    """Return the big-endian u32 at *pos*."""
    return struct.unpack_from(">I", data, pos)[0]


def _s16(data, pos):
    """Return the big-endian s16 at *pos*."""
    return struct.unpack_from(">h", data, pos)[0]


def _s32(data, pos):
    """Return the big-endian s32 at *pos*."""
    return struct.unpack_from(">i", data, pos)[0]


def _wide_string(data, pos):
    """Return ``(text, next_pos)`` for a u16be-length UTF-16BE string."""
    length = _u16(data, pos)
    start = pos + 2
    if start + length > len(data):
        raise ValueError(_tr("Invalid file format"))
    return data[start:start + length].decode("utf-16-be", "replace"), start + length


def _narrow_string(data, pos):
    """Return the position after a u16be-length UTF-8 string."""
    length = _u16(data, pos)
    end = pos + 2 + length
    if end > len(data):
        raise ValueError(_tr("Invalid file format"))
    return end


def _find_blocks(data):
    """Return ``(design name, colour-block count, first block offset)``.

    Raises:
        ValueError: when the tagged sections are not where the format puts them.
    """
    pos = HEADER_SIZE
    pos += _u16(data, 6)                 # production string
    if data[pos:pos + 3] != TAG_SUMMARY:
        raise ValueError(_tr("Invalid file format"))
    pos += 7
    name, pos = _wide_string(data, pos)
    pos += 16                            # design extents in micrometres
    declared_stitches = _u32(data, pos)
    pos += 9                             # stitch-time count, changes, byte, hoops
    if declared_stitches > MAX_STITCHES:
        raise ValueError(_tr("Invalid file format"))
    if data[pos:pos + 3] != TAG_HOOP:
        raise ValueError(_tr("Invalid file format"))
    pos += 7
    pos += 8 + 3 + 24                    # centre, unknown bytes, hoop extents
    _notes, pos = _wide_string(data, pos)
    pos += 2 + 16                        # unknown bytes, four 4096-style values
    if data[pos:pos + 4] not in DESIGN_MAGIC:
        raise ValueError(_tr("Invalid file format"))
    pos += 6
    _producer, pos = _wide_string(data, pos)
    blocks = _u16(data, pos)
    if blocks > MAX_BLOCKS:
        raise ValueError(_tr("Invalid file format"))
    return name, blocks, pos + 2


def _read_thread(data, pos, block_end):
    """Return ``(colour, next_pos)`` for the thread table inside a block."""
    entries = data[pos]
    if entries == 0:
        raise ValueError(_tr("Invalid file format"))
    # The first entry is a flag byte followed by the thread's RGB triple.
    colour = (data[pos + 2], data[pos + 3], data[pos + 4])
    pos += 1 + 6 * entries + 1           # count, entries, one spare byte
    pos += 2                             # material, weight
    for _ in range(3):                   # catalogue, description, brand
        pos = _narrow_string(data, pos)
    if pos > block_end:
        raise ValueError(_tr("Invalid file format"))
    return colour, pos


def _decode_run(data, pos, end, elements, x, y):
    """Decode one stitch run into *elements*; return ``(x, y, counts, short)``.

    *short* is True when the run ends in the middle of a jump record.
    """
    stitches = 0
    jumps = 0
    short = False
    while pos + 1 < end:
        dx = data[pos]
        dy = data[pos + 1]
        pos += 2
        if dx == ESCAPE:
            if dy in ESCAPE_EMPTY:
                continue
            if dy == ESCAPE_JUMP:
                if pos + 4 > end:
                    short = True
                    break
                x += _s16(data, pos)
                y -= _s16(data, pos + 2)
                pos += 4
                if data[pos:pos + 2] == JUMP_END:
                    pos += 2
                jumps += 1
                continue
            # 0x80 0x02 closes a jump that was already consumed; anything else
            # is a record this reader does not know, so skip it.
            continue
        x += file_io.signed_byte(dx)
        y -= file_io.signed_byte(dy)
        elements.append((ELEM_STITCH, x, y))
        stitches += 1
    return x, y, stitches, jumps, short


def is_format(header):
    """Return True when *header* starts like a VP3 file."""
    return header[0:len(MAGIC)] == MAGIC


def load(path):
    """Load a VP3 file as a hoop pattern.

    The thread of each colour block becomes a palette entry and a colour change
    record separates consecutive blocks, so the design keeps its sewing order
    and its colours.

    Returns ``(pattern, info)``; see file_io.load_pattern() for the contract.
    """
    pattern = StitchPattern()
    info = PatternFileInfo(input_file_format="VP3")

    with open(path, "rb") as f:
        data = f.read()
    if len(data) < HEADER_SIZE:
        raise ValueError(_tr("File too short"))
    if not is_format(data):
        raise ValueError(_tr("Invalid file format"))

    name, blocks, pos = _find_blocks(data)
    # Writers pad the design name to a fixed width.
    info.note = name.strip() or None

    if file_io.DEBUG:
        print(f"\n\nReading VP3 pattern: {blocks} colour blocks, "
              f"first at {pos}, design name {name!r}")

    colors = []
    elements = []
    x = 0
    y = 0
    stitches = 0
    jumps = 0
    color_changes = 0
    short_run = False
    for index in range(blocks):
        if data[pos:pos + 3] != TAG_THREAD:
            info.warnings.append(_tr(
                "The VP3 file ends in the middle of a colour block"))
            break
        block_end = pos + 7 + _u32(data, pos + 3)
        if block_end > len(data):
            info.warnings.append(_tr(
                "The VP3 file ends in the middle of a colour block"))
            block_end = len(data)

        colour, cursor = _read_thread(data, pos + 15, block_end)
        cursor += 8                      # position shift in micrometres
        if data[cursor:cursor + 3] != TAG_RUN:
            info.warnings.append(_tr(
                "The VP3 file ends in the middle of a colour block"))
            break
        run_end = cursor + 7 + _u32(data, cursor + 3)

        if colour not in colors:
            colors.append(colour)
        if index:
            # Each block is one thread; a colour change separates the blocks.
            color_changes += 1
            elements.append((ELEM_COLOR, colors.index(colour)))
        x, y, run_stitches, run_jumps, short = _decode_run(
            data, cursor + 7 + RUN_HEADER, min(run_end, block_end), elements,
            x, y)
        stitches += run_stitches
        jumps += run_jumps
        short_run = short_run or short
        pos = block_end

    if not stitches:
        raise ValueError(_tr("The VP3 file contains no stitches"))
    if short_run:
        info.warnings.append(_tr(
            "The VP3 file ends in the middle of a stitch record"))

    pattern.colors = colors
    color_changes -= file_io.drop_trailing_color_changes(elements)

    # VP3 coordinates are deltas from the design origin, so only the units are
    # converted here. The hoop is chosen by file_io.load_pattern(), after the
    # size in the file has been reported.
    info.needs_hoop_placement = True
    info.stitch_count = stitches
    info.has_color_palette = bool(colors)
    info.color_count = len(colors)
    info.color_changes = color_changes

    pattern._load_elements(file_io.to_canvas_units(elements, VP3_TO_CANVAS))
    if file_io.DEBUG:
        print(f"VP3 design read; {stitches} stitches, {jumps} jumps, "
              f"{color_changes} colour changes")
    return pattern, info
