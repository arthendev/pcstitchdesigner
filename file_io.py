"""Binary file I/O for stitch patterns.

File format (PCD/PCQ compatible):
  Header:  byte 0x32
           byte 0x00 (9mm) or 0x01 (MAXI) or 0x02 (small hoop) or 0x03 (large hoop)
           uint16 color count
           uint16 element record count
  Colors:  color_count × (R G B padding)
  Body:    record_count × (byte c0, 3-byte x LE, byte c1, 3-byte y LE, byte control)
           record_count does not include color-change records (control == 0x03)!

Element encoding:
  control == 0: ELEM_STITCH  — normal stitch point, coords in x/y + fractional parts in c0/c1
  control == 1: ELEM_AUTO    — automatic stitch point, coords in x/y + fractional parts in c0/c1
  control == 2: ELEM_AUTO    — automatic stitch point, coords in x/y + fractional parts in c0/c1
  control == 3: ELEM_COLOR   — color change; new palette index stored in c0
  control == 4: ELEM_SECTION — section marker (start/end), carries regular stitch coords; not transmitted to machine

Physical resolution of the hardware:
  9mm: 1/6 mm (0.1667 mm) per step
  MAXI: 1/6 mm (0.1667 mm) per step
  Embroidery: ca. 1/10 mm (0.1 mm) per step; exact value: 51/512 mm (0.099609375 mm) per step

All integer values are little-endian.

Information sources:
  https://web.archive.org/web/20160730135244/http://achatina.de/sewing/main/PCS.HTM
  https://github.com/EmbroidePy/pyembroidery/blob/main/pyembroidery/PcsReader.py
  + several PCS files and own experiments

Notes:
  0x03: (color change) might carry coordinates; if so, should they be accounted to total number of data blocks? Which color do they belong to?
        It is not included in the record count in the header; the record count is only for actual stitch points (0x00, 0x01, 0x02, 0x04).
        So we don't know how many records to read in total. Some PCD files spotted in the wild handle it wrongly and are too long or too short.

  0x04 is a section marker; for PCD/PCQ the first and last record has control_byte = 0x04, i.e. whole pattern is a single section
       It is not transmitted to the machine, but we want to preserve it in the pattern (e.g. for selection of a single section)
  
  File might include optional 13-bytes block for the name of BMP background image. Name is null-terminated.
  It occurs directly after the data block.

  File might include optional ASCII comment field of any length, terminated by 0x00. It occurs after the 13-bytes BMP name block (if present).

  There is another (older?) file format for PCD, PCQ. Magic number = 0x31. It omits the color count and color block.

"""

import os
import struct
from dataclasses import dataclass, field
from typing import List, Optional

from PyQt5.QtCore import QCoreApplication
from model import StitchPattern, ELEM_STITCH, ELEM_AUTO, ELEM_COLOR, ELEM_SECTION


def _tr(s):
    return QCoreApplication.translate("FileIO", s)

HEADER_FMT = '<BBH'  # header_byte(1) + stitch_type(1) + color_count(2) = 4 bytes
POINT_FMT = '<B3sB3sB'  # c0(1) + x(3, LE) + c1(1) + y(3, LE) + control_byte(1) = 9 bytes

BG_BLOCK_SIZE = 13  # fixed size of the optional BMP background-name block

DEBUG = 1  # 1: print details of each element record; 2: print only for non-zero control bytes


@dataclass
class PatternFileInfo:
    """What the parser actually found in a pattern file.

    The counts mirror what was read from the file, not what the header claims,
    so callers can compare the two and detect malformed files.
    """

    input_file_format: str = "UNKNOWN"  # file extension, e.g. "PCS", "PCD", "PCQ"
    stitch_type: str = ""  # "9mm", "MAXI", "small hoop", "large hoop"
    stitch_count: int = 0  # parsed records excluding colour changes
    has_color_palette: bool = False
    color_count: int = 0  # parsed palette entries
    color_changes: int = 0  # parsed ELEM_COLOR records
    sections_count: int = 0  # parsed ELEM_SECTION markers
    background_image: Optional[str] = None  # optional BMP file name, None if absent/invalid
    comment: Optional[str] = None  # optional comment, None if absent/invalid
    warnings: List[str] = field(default_factory=list)


def _parse_text_block(block):
    """Decode a NUL-terminated text block, or return None if it is invalid.

    Valid blocks hold only bytes 32..255 before the terminator. Everything
    after the first NUL is ignored. A missing terminator, an empty string or a
    control byte before the terminator makes the block invalid.
    """
    terminator = block.find(b"\x00")
    if terminator < 0:
        return None
    text = block[:terminator]
    if not text:
        return None
    if any(byte < 32 for byte in text):
        return None
    return text.decode("latin-1")


def save_pattern(path, pattern):
    """Save a StitchPattern to a binary file."""
    if pattern.stitch_type == "9mm":
        stitch_type_byte = 0x00
    elif pattern.stitch_type == "MAXI":
        stitch_type_byte = 0x01
    elif pattern.stitch_type == "small hoop":
        stitch_type_byte = 0x02
    elif pattern.stitch_type == "large hoop":
        stitch_type_byte = 0x03
    else:
        raise ValueError(_tr("Invalid/unsupported stitch type"))

    with open(path, 'wb') as f:
        # Header: magic, stitch_type, color_count
        f.write(struct.pack(HEADER_FMT, 0x32, stitch_type_byte, len(pattern.colors)))
        # Colors
        for r, g, b in pattern.colors:
            f.write(struct.pack('BBBx', r, g, b))
        # Element record count
        f.write(struct.pack('<H', len(pattern.display_elements)))
        # Element records
        for elem in pattern.display_elements:
            kind = elem[0]
            if kind == ELEM_STITCH:
                x, y = elem[1], elem[2]
                x_int, y_int = int(x), int(y)
                c0 = round((x - x_int) * 256) % 256
                c1 = round((y - y_int) * 256) % 256
                f.write(struct.pack(POINT_FMT, c0, x_int.to_bytes(3, 'little'), c1, y_int.to_bytes(3, 'little'), 0x00))
            elif kind == ELEM_AUTO:
                x, y = elem[1], elem[2]
                x_int, y_int = int(x), int(y)
                c0 = round((x - x_int) * 256) % 256
                c1 = round((y - y_int) * 256) % 256
                f.write(struct.pack(POINT_FMT, c0, x_int.to_bytes(3, 'little'), c1, y_int.to_bytes(3, 'little'), 0x02))
            elif kind == ELEM_COLOR:
                color_idx = elem[1]
                f.write(struct.pack(POINT_FMT, color_idx, (0).to_bytes(3, 'little'), 0, (0).to_bytes(3, 'little'), 0x03))
            elif kind == ELEM_SECTION:
                x, y = elem[1], elem[2]
                f.write(struct.pack(POINT_FMT, 0, x.to_bytes(3, 'little'), 0, y.to_bytes(3, 'little'), 0x04))
    pattern.modified = False


def load_pattern(path):
    """Load a StitchPattern from a binary file.

    Returns ``(pattern, info)``: the parsed pattern and a PatternFileInfo
    describing the file. Structurally broken headers still raise ValueError;
    a file that ends before the header's record count is loaded partially and
    reported through ``info.warnings``.
    """
    pattern = StitchPattern()
    file_format = os.path.splitext(path)[1].lstrip(".").upper()
    info = PatternFileInfo(input_file_format=file_format or "UNKNOWN")

    with open(path, 'rb') as f:
        # Read and validate header
        header_size = struct.calcsize(HEADER_FMT)
        header_data = f.read(header_size)
        if len(header_data) < header_size:
            raise ValueError(_tr("File too short"))

        magic_number, stitch_type_byte, color_count = struct.unpack(HEADER_FMT, header_data)

        if magic_number != 0x32:
            raise ValueError(_tr("Invalid file format"))

        # Set stitch type
        if stitch_type_byte == 0x00:
            pattern.stitch_type = "9mm"
        elif stitch_type_byte == 0x01:
            pattern.stitch_type = "MAXI"
        elif stitch_type_byte == 0x02:
            pattern.stitch_type = "small hoop"
        elif stitch_type_byte == 0x03:
            pattern.stitch_type = "large hoop"
        else:
            raise ValueError(_tr("Invalid/unsupported stitch type"))

        info.stitch_type = pattern.stitch_type

        # Read colors
        for _ in range(color_count):
            color_data = f.read(3)
            if len(color_data) < 3:
                raise ValueError(_tr("Unexpected end of file while reading colors"))
            r, g, b = struct.unpack('BBB', color_data)
            pattern.colors.append((r, g, b))
            f.read(1)  # skip padding byte

        stitch_count_data = f.read(2)
        if len(stitch_count_data) < 2:
            raise ValueError(_tr("Unexpected end of file while reading stitch count"))
        stitch_count, = struct.unpack('<H', stitch_count_data)

        # Read element records.
        # Legacy format (control == 1) used a sequential color-change counter;
        # new format (control == 3) stores the palette index directly in c0.
        point_size = struct.calcsize(POINT_FMT)
        legacy_color_idx = 0  # tracks sequential color index for legacy 0x01 records

        if DEBUG:
            print("\n\nReading " + pattern.stitch_type + " pattern with "
                  + str(color_count) + " colors and "
                  + str(stitch_count) + " stitch records:")

        all_elems = []
        stitch_records = 0  # parsed records that count towards the header's stitch count
        sections = 0
        truncated = False

        # for i in range(stitch_count):
        i = 0
        extra_records = 0
        while i < stitch_count + extra_records:
            point_data = f.read(point_size)
            if len(point_data) < point_size:
                # Some writers include colour-change records in the header count.
                # If the file ends exactly at that count, it is complete despite
                # the non-standard header, so no warning is warranted.
                if i == stitch_count and not point_data:
                    break
                # The header promises more records than the file holds, so the
                # file was cut short (or the writer under-counted colour changes).
                info.warnings.append(_tr(
                    "File appears to be truncated: only {0} of {1} records could be read"
                ).format(i, stitch_count + extra_records))
                truncated = True
                break
            c0, x_bytes, c1, y_bytes, control_byte = struct.unpack(POINT_FMT, point_data)
            x = int.from_bytes(x_bytes, 'little')
            y = int.from_bytes(y_bytes, 'little')

            if DEBUG == 1 or (DEBUG > 1 and control_byte != 0x00):
                print(f"Read record {i}:", end=" ")
                if c0 == 0x00:
                    print(f"c0={c0:3}", end=" ")
                else:
                    print(f"\033[91mc0={c0:3}\033[0m", end=" ")
                print(f"x={x:3}", end=" ")
                if c1 == 0x00:
                    print(f"c1={c1:3}", end=" ")
                else:
                    print(f"\033[91mc1={c1:3}\033[0m", end=" ")
                print(f"y={y:3}", end=" ")
                if control_byte == 0x00:
                    print(f"control_byte={control_byte:#04x}")
                else:
                    print(f"\033[91mcontrol_byte={control_byte:#04x}\033[0m")

            if control_byte == 0x00:
                # Normal stitch point; c0/c1 are fractional parts of x/y
                all_elems.append((ELEM_STITCH, x + c0 / 256, y + c1 / 256))
                # all_elems.append((ELEM_STITCH, round(x + c0 / 256), round(y + c1 / 256))) # align to grid, machine can't do fractions anyway (WYSIWYG)
                stitch_records += 1
            elif control_byte == 0x01 or control_byte == 0x02:
                # Automatic stitch point; treated as normal stitch for hoop patterns
                if pattern.stitch_type in ("small hoop", "large hoop"):
                    all_elems.append((ELEM_STITCH, x + c0 / 256, y + c1 / 256)) # include fractional parts; embroidery adapter has finer resolution (ca. 1/10 mm)
                else:
                    all_elems.append((ELEM_AUTO, x + c0 / 256, y + c1 / 256)) # include fractional parts; user can control this later
                stitch_records += 1
            elif control_byte == 0x03:
                # Color change: palette index in c0
                all_elems.append((ELEM_COLOR, c0))
                extra_records += 1 # color change records don't correspond to actual stitches, so we need to read extra records until we get stitch_count stitch points
            elif control_byte == 0x04:
                # Treat as normal stitch point; section markers are not transmitted to the machine, but we want to preserve them in the pattern (e.g. for selection of a single section)
                all_elems.append((ELEM_SECTION, x + c0 / 256, y + c1 / 256)) # include fractional parts
                stitch_records += 1
                sections += 1
            # Any other control byte is silently ignored.
            i += 1

        # Optional trailing blocks are only trustworthy when the record stream
        # was complete; in a truncated file they would be read from cut-off data.
        if not truncated:
            trailer = f.read()
            consumed = 0
            if len(trailer) >= BG_BLOCK_SIZE:
                background_image = _parse_text_block(trailer[:BG_BLOCK_SIZE])
                if background_image is not None:
                    info.background_image = background_image
                    consumed = BG_BLOCK_SIZE
                    # The comment block starts right after the background name block.
                    comment_block = trailer[BG_BLOCK_SIZE:]
                    info.comment = _parse_text_block(comment_block)
                    if info.comment is not None:
                        consumed += comment_block.index(b"\x00") + 1
            if DEBUG:
                if info.background_image is not None:
                    print(f"Background image: {info.background_image!r}")
                if info.comment is not None:
                    print(f"Comment: {info.comment!r}")
                remaining = trailer[consumed:]
                if remaining:
                    print(f"Unparsed trailing bytes ({len(remaining)}): "
                          f"{remaining.hex(' ')}")

    info.stitch_count = stitch_records
    info.has_color_palette = bool(pattern.colors)
    info.color_count = len(pattern.colors)
    info.color_changes = extra_records
    info.sections_count = sections

    pattern._load_elements(all_elems)
    return pattern, info
