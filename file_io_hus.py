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
  HUS uses the LZSS variant Greenleaf licensed from Robert Jung, nearly the same
  as ARJ's. A stream is a series of bit-coded blocks. Each block starts with a
  16-bit token count, then three Huffman tables are stored as code lengths: a
  pre-token table that builds the character table's lengths, the character table
  and the distance table. Tokens 0..255 are literal bytes, 256..509 copy
  (token - 253) bytes from up to 1024 bytes back, and 510 ends the stream.

The thread table is the fixed 29-colour palette below (HUS_COLORS).

Import only: the palette and hoop coordinates do not fit the sewing formats, so
file_io.save_pattern() refuses .hus paths.

Information sources:
  https://edutechwiki.unige.ch/en/Embroidery_format_HUS
  https://github.com/EmbroidePy/pyembroidery/blob/main/pyembroidery/HusReader.py
"""

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

# Tokens of the compressed stream.
LITERAL_MAX = 0xFF
END_TOKEN = 510

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


class _Huffman:
    """Canonical Huffman table read from a list of code lengths.

    The table is indexed by the next *width* bits of the stream, where *width*
    is the longest code length, so one lookup decodes a whole symbol.
    A table without lengths holds a single value instead (a zero-length code).
    """

    def __init__(self, lengths=None, value=0):
        self._lengths = lengths
        self._value = value
        self._table = None
        self._width = 0

    def build(self):
        """Expand the lengths into a lookup table; the lowest index wins a tie."""
        self._width = max(self._lengths)
        if self._width == 0:
            return
        table = []
        size = 1 << self._width
        for bit_length in range(1, self._width + 1):
            size //= 2
            for index, length in enumerate(self._lengths):
                if length == bit_length:
                    table.extend([index] * size)
        self._table = table

    def lookup(self, upcoming):
        """Return ``(value, code_length)`` for the next bits of the stream."""
        if self._table is None:
            return self._value, 0
        value = self._table[upcoming >> (16 - self._width)]
        return value, self._lengths[value]


class _BitReader:
    """Read big-endian bits from a byte array, padding past the end with zeros."""

    def __init__(self, data):
        self._data = data
        self.position = 0
        self.total = len(data) * 8

    def peek(self, count):
        """Return the next *count* bits without consuming them."""
        end = self.position + count - 1
        value = 0
        for index in range(self.position // 8, end // 8 + 1):
            value <<= 8
            if index < len(self._data):
                value |= self._data[index]
        unused = (8 - (end + 1) % 8) % 8
        return (value >> unused) & ((1 << count) - 1)

    def drop(self, count):
        """Consume *count* bits."""
        self.position += count

    def pop(self, count):
        """Return the next *count* bits and consume them."""
        value = self.peek(count)
        self.position += count
        return value


def _read_variable_length(bits):
    """Read a length written as three bits plus unary extension."""
    value = bits.pop(3)
    if value != 7:
        return value
    for _ in range(13):
        if bits.pop(1) == 0:
            break
        value += 1
    return value


def _load_character_length_huffman(bits):
    """Load the pre-token table, which builds the character code lengths."""
    count = bits.pop(5)
    if count == 0:
        return _Huffman(value=bits.pop(5))
    lengths = [0] * count
    index = 0
    while index < count:
        if index == 3:
            # After the first three lengths the stream skips up to three
            # entries that are known to be zero.
            index += bits.pop(2)
            if index >= count:
                break
        lengths[index] = _read_variable_length(bits)
        index += 1
    huffman = _Huffman(lengths)
    huffman.build()
    return huffman


def _load_character_huffman(bits, length_huffman):
    """Load the table the literal/copy tokens are encoded with."""
    count = bits.pop(9)
    if count == 0:
        return _Huffman(value=bits.pop(9))
    lengths = [0] * count
    index = 0
    while index < count:
        value, code_length = length_huffman.lookup(bits.peek(16))
        bits.drop(code_length)
        if value == 0:
            index += 1                      # one zero length
        elif value == 1:
            index += 3 + bits.pop(4)        # a run of zero lengths
        elif value == 2:
            index += 20 + bits.pop(9)       # a long run of zero lengths
        else:
            lengths[index] = value - 2
            index += 1
    huffman = _Huffman(lengths)
    huffman.build()
    return huffman


def _load_distance_huffman(bits):
    """Load the table that names how far back a copy starts."""
    count = bits.pop(5)
    if count == 0:
        return _Huffman(value=bits.pop(5))
    lengths = [_read_variable_length(bits) for _ in range(count)]
    huffman = _Huffman(lengths)
    huffman.build()
    return huffman


def _read_distance(bits, distance_huffman):
    """Return how far back the next copy starts."""
    value, code_length = distance_huffman.lookup(bits.peek(16))
    bits.drop(code_length)
    if value == 0:
        return 0
    shift = value - 1
    return (1 << shift) + bits.pop(shift)


def _expand(data, expected_size):
    """Decompress one HUS stream into at most *expected_size* bytes plus END.

    Raises:
        ValueError: when the stream is not a valid compressed block.
    """
    bits = _BitReader(data)
    output = bytearray()
    character_huffman = None
    distance_huffman = None
    block_tokens = 0
    limit = expected_size + 1  # the declared count plus a possible END token

    while bits.position < bits.total and len(output) < limit:
        if block_tokens <= 0:
            block_tokens = bits.pop(16)
            length_huffman = _load_character_length_huffman(bits)
            character_huffman = _load_character_huffman(bits, length_huffman)
            distance_huffman = _load_distance_huffman(bits)
            if block_tokens <= 0:
                # The stream ran out before the declared record count; the
                # caller reports the truncation.
                break
        block_tokens -= 1

        token, code_length = character_huffman.lookup(bits.peek(16))
        bits.drop(code_length)
        if token <= LITERAL_MAX:
            output.append(token)
            continue
        if token == END_TOKEN:
            break

        length = token - 253  # the shortest copy is three bytes
        back = _read_distance(bits, distance_huffman) + 1
        start = len(output) - back
        if start < 0:
            raise ValueError("copy reaches before the start of the stream")
        if back > length:
            output += output[start:start + length]
        else:
            # The copy overlaps what it produces, so it has to be built byte by
            # byte instead of sliced.
            for offset in range(length):
                output.append(output[start + offset])

    return output


def _signed8(byte):
    """Interpret one stream byte as a two's-complement signed number."""
    return byte - 256 if byte > 127 else byte


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
        commands = _expand(data[command_offset:x_offset], stitch_count)
        x_stream = _expand(data[x_offset:y_offset], stitch_count)
        y_stream = _expand(data[y_offset:], stitch_count)
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
        dx = _signed8(dx_byte)
        dy = _signed8(dy_byte)
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
