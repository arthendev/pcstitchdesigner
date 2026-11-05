"""VIP embroidery reader (.vip files) — import only.

VIP is a successor to HUS: the same three compressed stitch streams, 
but with real 24-bit thread colours instead of palette indices.

File format:
  Header (little-endian):

    0x00 u32   magic 0x0190FC5D ("5D FC 90 01")
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
    0x2A u32   offset of the design name record, zero when the file has none
    0x2E byte[] one encoded RGBA quadruplet per thread

  Three streams follow the header, compressed exactly as HUS compresses them
  (see greenleaf_lzss), one byte per stitch for the command, for the X delta and
  for the Y delta. X and Y are relative moves in 0.1 mm, Y measured upwards like
  the canvas, so no axis is flipped.

  Command bytes:
    0x80 stitch        0x81 jump        0x84 colour change
    0x88 trim          0x90 end

  The colour table is (R, G, B, 0) per thread, each byte XOR-ed with a fixed key
  byte and with the byte before it. After the table come one 32-bit type/tension
  number per thread, then the name record: a 32-bit character
count followed by UTF-16 text.

Import only: the palette and hoop coordinates do not fit the sewing formats, so
file_io.save_pattern() refuses .vip paths.

Information source:
  https://www.jasonweiler.com/HUSandVIPFileFormatInfo.html
  https://github.com/Embroidermodder/Embroidermodder/blob/main/src/libembroidery/format-vip.c
"""

import struct

import greenleaf_lzss
import file_io
from file_io import PatternFileInfo, _tr
from model import (DEFAULT_PALETTE, StitchPattern,
                   ELEM_STITCH, ELEM_COLOR)

EXTENSIONS = (".vip",)

MAGIC = b"\x5d\xfc\x90\x01"
# The colour table starts right after the header; HUS keeps thread indices in
# the same place.
HEADER_SIZE = 0x2E

COMMAND_STITCH = 0x80
COMMAND_JUMP = 0x81
COMMAND_COLOR_CHANGE = 0x84
COMMAND_TRIM = 0x88
COMMAND_END = 0x90

# VIP moves in 0.1 mm; one canvas unit is 1/6 mm, so a VIP step is 0.6 units.
VIP_UNIT_MM = 0.1
VIP_TO_CANVAS = VIP_UNIT_MM / StitchPattern.STITCH_RES_MM

# Colour decoding table. Its length caps the thread count the format can 
# express.
COLOR_KEY = bytes.fromhex(
    "2E82E46F38A9DCC67BB628ACFDAA8A4E"
    "762EF0E4251B8A684E92B9B495F03EEF"
    "F74024183931BBE153A81FB13A07FBCB"
    "E60081500E40E12C73500D91D60A5DD6"
    "8BB862AE4700535AB780AA28F75D705E"
    "2C0B98E3A0986047899B82FB40C9B400"
    "0E686A1E0985C05381D19889AFE8854F"
    "E3698903A12E8FCFED919F581ED6843C"
    "0927BDF4C390C0511B2B63BCB93D404D"
    "626FE08CF55D08FD3D5036D7C9C943E4"
    "2DCB95B6F40DEAC2FD663F5EBD69062A"
    "0319472BDF38EA4F804995B2D6F99A75"
    "F4D89B1DB0A469DBA921796FD8DE33FE"
    "9F04E59A6B9B7383627CB96676F25BC9"
    "5EFC74AA6CF1CD93CEE98053033B974B"
    "3976C2C156CB70FD3B3E5257815D568D"
    "5190D476D7D516026DF24DE10E964FA1"
    "3AA0605964041AE467B6ED3F7420551F"
    "FB23929153C865AB9D51D673DE01B180"
    "B7C0D6801C2E3C8363EEBC3325E20E7A"
    "67DE3F7114499C92930D269A0EDAED6F"
    "A4890C1BF0A1DFE19E3C0478E4AB6DFF"
    "9CAFCAC788179CE5B7336DDCED8F6C18"
    "1D7106B1C5E2CF137781C5B70A140A6B"
    "4026A088D1626AB35012B99BB5839B37"
)

# Sanity bounds for a corrupt header.
MAX_STITCHES = 5_000_000
MAX_COLORS = len(COLOR_KEY) // 4
MAX_NAME_LENGTH = 256


def _decode_colors(encoded, count):
    """Return the thread colours the colour table holds.

    Each byte is XOR-ed with its key byte and with the byte before it, which
    turns the stored table into (R, G, B, 0) quadruplets.
    """
    colors = []
    previous = 0
    quadruplet = []
    for index in range(count * 4):
        value = encoded[index]
        quadruplet.append(value ^ COLOR_KEY[index] ^ previous)
        previous = value
        if len(quadruplet) == 4:
            colors.append((quadruplet[0], quadruplet[1], quadruplet[2]))
            quadruplet = []
    return colors


def _slot_colors(slot_colors):
    """Return the distinct thread colours and each slot's palette index."""
    colors = []
    slot_index = []
    for rgb in slot_colors:
        if rgb not in colors:
            colors.append(rgb)
        slot_index.append(colors.index(rgb))
    return colors, slot_index


def _read_name(data, name_offset, command_offset):
    """Return the design name the header points at, or "" when it has none."""
    # A zero offset means the file has no name; anything else has to point at a
    # record that still fits in front of the stitch streams.
    if not name_offset or name_offset + 4 > command_offset:
        return ""
    length, = struct.unpack_from("<I", data, name_offset)
    if length == 0 or length > MAX_NAME_LENGTH:
        return ""
    start = name_offset + 4
    return data[start:start + 2 * length].decode("utf-16-le", "replace").rstrip("\x00")


def is_format(header):
    """Return True when *header* starts like a VIP file."""
    return len(header) >= 4 and header[0:4] == MAGIC


def load(path):
    """Load a VIP file as a hoop pattern.

    VIP stores the real thread colours, so the pattern palette is the one the
    design was made with. The design name, when the file has one, is reported
    as the pattern's note.

    Returns ``(pattern, info)``; see file_io.load_pattern() for the contract.
    """
    pattern = StitchPattern()
    info = PatternFileInfo(input_file_format="VIP")

    with open(path, "rb") as f:
        data = f.read()
    if len(data) < HEADER_SIZE:
        raise ValueError(_tr("File too short"))
    if data[0:4] != MAGIC:
        raise ValueError(_tr("Invalid file format"))

    stitch_count, color_count = struct.unpack_from("<II", data, 4)
    command_offset, x_offset, y_offset = struct.unpack_from("<III", data, 0x14)
    name_offset, = struct.unpack_from("<I", data, 0x2A)

    table_end = HEADER_SIZE + 4 * color_count
    if (stitch_count > MAX_STITCHES or color_count > MAX_COLORS
            or not table_end <= command_offset <= x_offset <= y_offset <= len(data)):
        raise ValueError(_tr("Invalid file format"))

    slot_colors = _decode_colors(data[HEADER_SIZE:table_end], color_count)
    colors, slot_index = _slot_colors(slot_colors)
    pattern.colors = colors
    info.note = _read_name(data, name_offset, command_offset) or None

    if file_io.DEBUG:
        print(f"\n\nReading VIP pattern: {stitch_count} records, "
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
                "Unsupported VIP command 0x{0:02X}; reading stopped").format(command))
            break

    if not stitches:
        raise ValueError(_tr("The VIP file contains no stitches"))
    if truncated:
        info.warnings.append(_tr(
            "The VIP file ends in the middle of a stitch record"))

    color_changes -= file_io.drop_trailing_color_changes(elements)

    # VIP coordinates are deltas from the design origin, so only the units are
    # converted here. The hoop is chosen by file_io.load_pattern(), after the
    # size in the file has been reported.
    info.needs_hoop_placement = True
    info.stitch_count = stitches
    info.has_color_palette = bool(colors)
    info.color_count = len(colors)
    info.color_changes = color_changes

    pattern._load_elements(file_io.to_canvas_units(elements, VIP_TO_CANVAS))
    if file_io.DEBUG:
        print(f"VIP design read; {stitches} stitches, {jumps} jumps, "
              f"{color_changes} colour changes")
    return pattern, info
