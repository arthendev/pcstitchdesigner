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
  
  File might include optional 13-bytes block for the name of BMP template image. Name is null-terminated.
  It occurs directly after the data block.

  File might include optional ASCII comment field of any length, terminated by 0x00. It occurs after the 13-bytes BMP name block (if present).

  There is another (older?) file format for PCD, PCQ. Magic number = 0x31. It omits the color count and color block.

VCSM format (.9mm and .max files):
  Header:  "VCSM" (4 bytes)
           byte 4: 0xE8 = no optional header block, 0xE9 = optional block present
           byte 8: stitch type (0x00 = 9mm, 0x01 = MAXI)
  Optional block (0xE9 only, starting at offset 0x14):
           foot-type length + ASCII, needle-type length + ASCII,
           uint16 tension (value / 100), 2 unknown bytes,
           notes length (0xFF means the next uint16 holds the length) + ASCII notes,
           uint32 template-image size + embedded image bytes
  Body:    uint32 stitch count, then 8-byte records:
           int24 x LE, c0, int24 y LE, c1
           Coordinates use 1/10 mm units with c0/c1 as fractional parts (/256)
           and are stored as 24-bit signed values.

"""

import os
import struct
from dataclasses import dataclass, field
from typing import List, Optional

from PyQt5.QtCore import QCoreApplication
from model import (StitchPattern, elem_has_coords,
                   ELEM_STITCH, ELEM_AUTO, ELEM_COLOR, ELEM_SECTION)


def _tr(s):
    return QCoreApplication.translate("FileIO", s)

HEADER_FMT = '<BBH'  # header_byte(1) + stitch_type(1) + color_count(2) = 4 bytes
POINT_FMT = '<B3sB3sB'  # c0(1) + x(3, LE) + c1(1) + y(3, LE) + control_byte(1) = 9 bytes

BG_BLOCK_SIZE = 13  # fixed size of the optional BMP template-image name block

VCSM_MAGIC = b"VCSM"
VCSM_UNIT_MM = 0.1  # VCSM stitch coordinates are stored in 1/10 mm units

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
    template_image: Optional[str] = None  # optional BMP file name, None if absent/invalid
    comment: Optional[str] = None  # optional comment, None if absent/invalid
    warnings: List[str] = field(default_factory=list)


def _parse_text_block(block, allow_empty=False):
    """Decode a NUL-terminated text block, or return None if it is invalid.

    Valid blocks hold only bytes 32..255 before the terminator. Everything
    after the first NUL is ignored. A missing terminator or a control byte
    before the terminator makes the block invalid.

    An empty block (NUL as its first byte) normally counts as invalid, but the
    optional template-image name block is still valid when empty — it just holds no
    file name — so *allow_empty* makes it return an empty string instead.
    """
    terminator = block.find(b"\x00")
    if terminator < 0:
        return None
    text = block[:terminator]
    if not text:
        return "" if allow_empty else None
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

        # Element record count; the format excludes colour-change records
        f.write(struct.pack('<H', sum(1 for e in pattern.display_elements if e[0] != ELEM_COLOR)))

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
                x_int, y_int = int(x), int(y)
                c0 = round((x - x_int) * 256) % 256
                c1 = round((y - y_int) * 256) % 256
                f.write(struct.pack(POINT_FMT, c0, x_int.to_bytes(3, 'little'), c1, y_int.to_bytes(3, 'little'), 0x04))
        # Optional trailer: an empty template-image name block (no template image).
        f.write(b"\x00" * BG_BLOCK_SIZE)
    pattern.modified = False


def load_pattern(path):
    """Load a stitch pattern from a PCD, PCQ or PCS file.

    The parser is chosen from the header, not from the file extension: the
    magic byte separates the old 0x31 variant, and the stitch-type byte picks
    between the standard parser and the third-party PCS parser. Hoop stitch
    types (0x02/0x03) come from third-party embroidery tools, so those files are
    parsed with both parsers and the more plausible result is returned.

    Returns ``(pattern, info)``: the parsed pattern and a PatternFileInfo
    describing the file. Structurally broken headers raise ValueError; a file
    that ends before the header's record count is loaded partially and reported
    through ``info.warnings``.
    """
    with open(path, 'rb') as f:
        header = f.read(5)
    if len(header) < 2:
        raise ValueError(_tr("File too short"))

    if header[:4] == VCSM_MAGIC:
        return load_pattern_vcsm(path)

    magic_number, stitch_type_byte = header[0], header[1]
    if magic_number == 0x31:
        # Older PCD/PCQ/PCS variant that omits the stitch type, color count and color table.
        return load_pattern_v31(path)
    if magic_number != 0x32:
        raise ValueError(_tr("Invalid file format"))
    if stitch_type_byte in (0x00, 0x01):
         # 9mm or MAXI
        return _load_pcdqs_standard(path)
    if stitch_type_byte in (0x02, 0x03):
        # small hoop or large hoop
        return _load_pcs_best(path)
    raise ValueError(_tr("Invalid/unsupported stitch type"))


def load_pattern_v31(path):
    """Load the older PCD/PCQ/PCS variant identified by magic byte 0x31.

    That variant omits the stitch type, colour count and colour table.
    """

    HEADER_FMT_V31 = '<BH'  # magic_number(1) + stitch_count(2) = 3 bytes
    pattern = StitchPattern()
    file_format = os.path.splitext(path)[1].lstrip(".").upper()
    info = PatternFileInfo(input_file_format=file_format or "UNKNOWN")

    with open(path, 'rb') as f:
        # Read and validate header
        header_size = struct.calcsize(HEADER_FMT_V31)
        header_data = f.read(header_size)
        if len(header_data) < header_size:
            raise ValueError(_tr("File too short"))

        magic_number, stitch_count = struct.unpack(HEADER_FMT_V31, header_data)

        if magic_number != 0x31:
            raise ValueError(_tr("Invalid file format"))

        point_size = struct.calcsize(POINT_FMT)

        if DEBUG:
            print("\n\nReading pattern with " + str(stitch_count) + " stitch records:")

        all_elems = []
        stitch_records = 0  # parsed records that count towards the header's stitch count
        sections = 0
        truncated = False

        # for i in range(stitch_count):
        file_size = os.fstat(f.fileno()).st_size
        i = 0
        extra_records = 0
        while i < stitch_count + extra_records:
            if file_size - f.tell() == BG_BLOCK_SIZE:
                # Exactly the size of the optional template-image name block is left:
                # the record stream ends here and a trailer follows, even though
                # the header count implies more records. Leave it for the trailer
                # parser instead of consuming it as a stitch record.
                break
            point_data = f.read(point_size)
            if len(point_data) < point_size:
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
                # Automatic stitch point
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
                # An all-NUL name block is still a valid block, just without a name.
                template_name = _parse_text_block(trailer[:BG_BLOCK_SIZE], allow_empty=True)
                if template_name is not None:
                    info.template_image = template_name or None
                    consumed = BG_BLOCK_SIZE
                    # The comment block starts right after the template name block.
                    comment_block = trailer[BG_BLOCK_SIZE:]
                    comment = _parse_text_block(comment_block, allow_empty=True)
                    if comment is not None:
                        info.comment = comment or None
                        consumed += comment_block.index(b"\x00") + 1
            if DEBUG:
                print(f"Trailing bytes: {len(trailer)}")
                if info.template_image is not None:
                    print(f"Template image: {info.template_image!r}")
                if info.comment is not None:
                    print(f"Comment: {info.comment!r}")
                remaining = trailer[consumed:]
                if remaining:
                    print(f"Unparsed trailing bytes ({len(remaining)}): "
                          f"{remaining.hex(' ')}")

        if DEBUG and info.warnings:
            print(f"Warnings: {info.warnings}")

    # This format has no stitch-type byte, so infer the preset from the pattern
    # extents. Colour changes only occur on embroidery (hoop) patterns, so the
    # two sewing presets require a pattern without them.
    max_x = max_y = 0
    for elem in all_elems:
        if elem_has_coords(elem):
            max_x = max(max_x, elem[1])
            max_y = max(max_y, elem[2])

    def fits(stitch_type):
        width, height = StitchPattern.CANVAS_SIZES[stitch_type]
        return max_x <= width and max_y <= height

    if not extra_records and fits("9mm"):
        pattern.stitch_type = "9mm"
    elif not extra_records and fits("MAXI"):
        pattern.stitch_type = "MAXI"
    elif fits("small hoop"):
        pattern.stitch_type = "small hoop"
    elif fits("large hoop"):
        pattern.stitch_type = "large hoop"
    else:
        # Nothing fits; large hoop keeps the most of an oversized pattern visible.
        pattern.stitch_type = "large hoop"
        info.warnings.append(_tr(
            "Pattern size exceeds all supported stitch types; using large hoop"
        ))


    info.stitch_type = pattern.stitch_type
    info.stitch_count = stitch_records
    info.has_color_palette = False # this format doesn't have a color palette
    info.color_count = len(pattern.colors)
    info.color_changes = extra_records
    info.sections_count = sections

    pattern._load_elements(all_elems)
    return pattern, info


def _is_more_plausible(candidate_info, reference_info):
    """Return True when *candidate_info* looks like the better parse.

    Fewer warnings wins; on equal warnings the parse that recovered more
    stitch records wins.
    """
    if len(candidate_info.warnings) != len(reference_info.warnings):
        return len(candidate_info.warnings) < len(reference_info.warnings)
    return candidate_info.stitch_count > reference_info.stitch_count


def _describe_parse_result(result):
    """Return 'N stitches, M warnings' for a parse result, or 'failed'."""
    if result is None:
        return "failed"
    _pattern, info = result
    return f"{info.stitch_count} stitches, {len(info.warnings)} warnings"


def _plausibility_reason(candidate_info, reference_info):
    """Explain why *candidate_info* is a better parse than *reference_info*."""
    if len(candidate_info.warnings) != len(reference_info.warnings):
        return (f"fewer warnings ({len(candidate_info.warnings)} vs "
                f"{len(reference_info.warnings)})")
    return (f"more stitch records ({candidate_info.stitch_count} vs "
            f"{reference_info.stitch_count})")


def _load_pcs_best(path):
    """Parse a PCS file with both parsers and return the better result.

    A parser can reject a file outright, so a failure of one is not fatal as
    long as the other succeeds.
    """
    standard_error = None
    try:
        standard_result = _load_pcdqs_standard(path)
    except ValueError as exc:
        standard_result = None
        standard_error = exc
    try:
        nonstandard_result = load_pcdqs_nonstandard(path)
    except ValueError:
        nonstandard_result = None

    if DEBUG:
        print("PCS parser comparison:")
        print(f"  standard:    {_describe_parse_result(standard_result)}")
        print(f"  nonstandard: {_describe_parse_result(nonstandard_result)}")

    if standard_result is None and nonstandard_result is None:
        # Both parsers rejected the file; the standard one is the more
        # authoritative source for the error message.
        if DEBUG:
            print("Winner: none - both parsers failed")
        raise standard_error
    if standard_result is None:
        if DEBUG:
            print("Winner: nonstandard - the standard parser failed")
        return nonstandard_result
    if nonstandard_result is None:
        if DEBUG:
            print("Winner: standard - the nonstandard parser failed")
        return standard_result

    standard_info = standard_result[1]
    nonstandard_info = nonstandard_result[1]
    nonstandard_wins = _is_more_plausible(nonstandard_info, standard_info)
    if nonstandard_wins:
        winner = "nonstandard"
        reason = _plausibility_reason(nonstandard_info, standard_info)
    else:
        winner = "standard"
        if _is_more_plausible(standard_info, nonstandard_info):
            reason = _plausibility_reason(standard_info, nonstandard_info)
        else:
            reason = "equal warnings and stitch counts; standard preferred"

    if DEBUG:
        print(f"Winner: {winner} - {reason}")

    return nonstandard_result if nonstandard_wins else standard_result


def _load_pcdqs_standard(path):
    """Parse a PCD/PCQ/PCS file whose header uses magic number 0x32.

    Returns ``(pattern, info)``; see load_pattern() for the shared contract.
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

        # Read element records
        point_size = struct.calcsize(POINT_FMT)

        if DEBUG:
            print("\n\nReading " + pattern.stitch_type + " pattern with "
                  + str(color_count) + " colors and "
                  + str(stitch_count) + " stitch records:")

        all_elems = []
        stitch_records = 0  # parsed records that count towards the header's stitch count
        sections = 0
        truncated = False

        # for i in range(stitch_count):
        file_size = os.fstat(f.fileno()).st_size
        i = 0
        extra_records = 0
        while i < stitch_count + extra_records:
            remaining = file_size - f.tell()
            if remaining < point_size:
                truncated = True
            if remaining == BG_BLOCK_SIZE or remaining < point_size:
                # Exactly the size of the optional template-image name block is left:
                # assume the record stream ends here and a trailer follows, even though
                # the header count implies more records. Leave it for the trailer
                # parser instead of consuming it as a stitch record.
                # The header promises more records than the file holds, so the
                # file was cut short.
                info.warnings.append(_tr(
                    "File appears to be truncated: only {0} of {1} records could be read"
                ).format(i, stitch_count + extra_records))
                truncated = True
                break
            point_data = f.read(point_size)
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
                # An all-NUL name block is still a valid block, just without a name.
                template_name = _parse_text_block(trailer[:BG_BLOCK_SIZE], allow_empty=True)
                if template_name is not None:
                    info.template_image = template_name or None
                    consumed = BG_BLOCK_SIZE
                    # The comment block starts right after the template name block.
                    comment_block = trailer[BG_BLOCK_SIZE:]
                    comment = _parse_text_block(comment_block, allow_empty=True)
                    if comment is not None:
                        info.comment = comment or None
                        consumed += comment_block.index(b"\x00") + 1
            if DEBUG:
                print(f"Trailing bytes: {len(trailer)}")
                if info.template_image is not None:
                    print(f"Template image: {info.template_image!r}")
                if info.comment is not None:
                    print(f"Comment: {info.comment!r}")
                remaining = trailer[consumed:]
                if remaining:
                    print(f"Unparsed trailing bytes ({len(remaining)}): "
                          f"{remaining.hex(' ')}")

        if DEBUG and info.warnings:
            print(f"Warnings: {info.warnings}")

    info.stitch_count = stitch_records
    info.has_color_palette = bool(pattern.colors)
    info.color_count = len(pattern.colors)
    info.color_changes = extra_records
    info.sections_count = sections

    pattern._load_elements(all_elems)
    return pattern, info



def load_pcdqs_nonstandard(path):
    """Load a PCS file written by a third-party application.

    Unlike the standard parser this ignores the header's record count: records
    are read until the stream itself signals its end — an unknown control byte,
    a colour index outside the 16-colour palette, coordinates outside the canvas
    of the file's stitch type, an all-zero record (null trailer), or only the
    template-image name block remaining. The record that triggers the stop is
    discarded. The optional trailer is not parsed here.
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

        # The header's record count is unreliable in third-party files, so only
        # skip it; the record stream is delimited by its content instead.
        stitch_count_data = f.read(2)
        if len(stitch_count_data) < 2:
            raise ValueError(_tr("Unexpected end of file while reading stitch count"))

        point_size = struct.calcsize(POINT_FMT)
        canvas_w, canvas_h = StitchPattern.CANVAS_SIZES[pattern.stitch_type]

        if DEBUG:
            print("\n\nReading " + pattern.stitch_type + " pattern with "
                  + str(color_count) + " colors (record count ignored):")

        all_elems = []
        stitch_records = 0  # parsed records that count towards the stitch count
        sections = 0
        extra_records = 0  # parsed colour-change records

        file_size = os.fstat(f.fileno()).st_size
        i = 0  # record index, used by the debug output only
        while True:
            remaining = file_size - f.tell()
            if remaining == BG_BLOCK_SIZE or remaining < point_size:
                # Only the optional template-image name block is left, or not even a
                # whole record; the record stream has ended.
                break
            point_data = f.read(point_size)
            c0, x_bytes, c1, y_bytes, control_byte = struct.unpack(POINT_FMT, point_data)
            x = int.from_bytes(x_bytes, 'little')
            y = int.from_bytes(y_bytes, 'little')

            if control_byte > 0x04:
                # Unknown record type; not part of the stitch data.
                break
            if control_byte == 0x03 and c0 > 15:
                # Colour indices above the 16-colour palette cannot be real
                # colour changes, so this record is not stitch data either.
                break
            # if control_byte == 0x00 and c0 == 0 and x == 0 and c1 == 0 and y == 0:
            #     # A completely zero record means probably trailer is reached.
            #     break
            if control_byte in (0x00, 0x01, 0x02, 0x04) and (x > canvas_w or y > canvas_h):
                # Coordinates outside the canvas for this stitch type: not a
                # stitch record.
                break

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
                extra_records += 1
            elif control_byte == 0x04:
                # Section marker; preserved so single sections can be selected
                all_elems.append((ELEM_SECTION, x + c0 / 256, y + c1 / 256))
                stitch_records += 1
                sections += 1
            i += 1

    info.stitch_count = stitch_records
    info.has_color_palette = bool(pattern.colors)
    info.color_count = len(pattern.colors)
    info.color_changes = extra_records
    info.sections_count = sections

    pattern._load_elements(all_elems)
    return pattern, info


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


def load_pattern_vcsm(path):
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

    if len(data) < 9:
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
        pos += image_size
        if DEBUG:
            print(f"VCSM optional block:\nfoot={foot!r}\nneedle={needle!r}\n"
                  f"tension={tension / 100:.2f}\nnotes={notes!r}\n"
                  f"template image={image_size} bytes")
    else:
        pos = 0x18

    if pos + 4 > len(data):
        raise ValueError(_tr("Unexpected end of file while reading stitch count"))
    stitch_count, = struct.unpack_from("<I", data, pos)
    pos += 4

    if DEBUG:
        print("Reading VCSM " + pattern.stitch_type + " pattern with "
              + str(stitch_count) + " stitch records:")

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

        if DEBUG == 1:
            print(f"Read record {i}: x={x:8.3f} y={y:8.3f} c0={c0} c1={c1}")

    info.comment = notes or None
    info.stitch_count = len(all_elems)
    info.has_color_palette = False
    info.color_count = 0
    info.color_changes = 0
    info.sections_count = 0

    pattern._load_elements(all_elems)
    return pattern, info