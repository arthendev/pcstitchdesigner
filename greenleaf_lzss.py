"""Greenleaf LZSS decompression, shared by the HUS and VIP readers.

HUS and VIP store three separately compressed streams (stitch commands, X deltas
and Y deltas) in the LZSS variant Greenleaf licensed from Robert Jung, nearly
the same as ARJ's. Only the compression lives here; what the decompressed bytes
mean is each format module's business.

A stream is a series of bit-coded blocks. Each block starts with a 16-bit token
count, then three Huffman tables stored as code lengths: a pre-token table that
builds the character table's lengths, the character table and the distance
table. Tokens 0..255 are literal bytes, 256..509 copy (token - 253) bytes from
up to 1024 bytes back, and 510 ends the stream. Bits are read most significant
first.

Information sources:
  https://community.kde.org/Projects/Liberty/File_Formats/Husqvarna_HUS
  https://www.jasonweiler.com/HUSandVIPFileFormatInfo.html
"""

LITERAL_MAX = 0xFF
END_TOKEN = 510
# A copy token names the byte count as (token - COPY_BASE); the shortest copy
# is three bytes.
COPY_BASE = 253


class Huffman:
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


class BitReader:
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
        return Huffman(value=bits.pop(5))
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
    huffman = Huffman(lengths)
    huffman.build()
    return huffman


def _load_character_huffman(bits, length_huffman):
    """Load the table the literal/copy tokens are encoded with."""
    count = bits.pop(9)
    if count == 0:
        return Huffman(value=bits.pop(9))
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
    huffman = Huffman(lengths)
    huffman.build()
    return huffman


def _load_distance_huffman(bits):
    """Load the table that names how far back a copy starts."""
    count = bits.pop(5)
    if count == 0:
        return Huffman(value=bits.pop(5))
    lengths = [_read_variable_length(bits) for _ in range(count)]
    huffman = Huffman(lengths)
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


def decompress(data, expected_size):
    """Decompress one stream into at most *expected_size* bytes plus END.

    A stream that runs out before the declared size yields the bytes it did
    hold, so a truncated file still opens; the caller compares the length with
    *expected_size* and reports the truncation.

    Raises:
        ValueError: when the stream is not a valid compressed block.
    """
    bits = BitReader(data)
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

        length = token - COPY_BASE
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
