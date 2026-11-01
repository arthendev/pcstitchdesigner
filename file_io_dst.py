"""Tajima DST embroidery reader (.dst files) — import only.

File format:
  Header: fixed 512 bytes of ``\\r``-separated ASCII records, e.g. ``LA:``
          (design label), ``ST:`` (record count), ``CO:`` (colour changes),
          ``+X:``/``-X:``/``+Y:``/``-Y:`` (extents in 0.1 mm).
  Body:   three-byte records until the END command or EOF.

Record encoding (bit 7..0 of each byte):
  byte 0:  y+1  y-1  y+9  y-9   —   x-9  x+9  x-1  x+1
  byte 1:  y+3  y-3  y+27 y-27  —   x-27 x+27 x-3  x+3
  byte 2:  c0   c1   y+81 y-81  —   x-81 x+81 set  set

  c0/c1 select the command: 00 = stitch, 10 = jump, 11 = colour change/stop,
  01 = sequin. The END record is the colour/stop pair together with the
  otherwise unused y+81/y-81 bits (0xF3 with a zero delta).

Coordinates are deltas in 0.1 mm steps and are cumulative from the design
origin; +Y points up, which matches the canvas. The model measures stitches in
1/6 mm, so each DST step is 0.6 canvas units. Jumps move the needle without
adding a stitch; the model has no jump record, so the travel becomes the line
between the two surrounding stitches (the embroidery card format cannot store
jumps either).

Information source: https://edutechwiki.unige.ch/en/Embroidery_format_DST
"""

import file_io
from file_io import PatternFileInfo, _tr
from model import StitchPattern, ELEM_STITCH, ELEM_COLOR, DEFAULT_PALETTE

HEADER_SIZE = 512
RECORD_SIZE = 3

# DST moves in 0.1 mm; one canvas unit is 1/6 mm, so a DST step is 0.6 units.
DST_UNIT_MM = 0.1
DST_TO_CANVAS = DST_UNIT_MM / StitchPattern.STITCH_RES_MM

EXTENSIONS = (".dst",)

# Header signatures: "LA:" is the label record; some writers emit the raw
# label-record marker 0x01 0x00 0x00 instead.
_HEADER_SIGNATURES = (b"LA:", b"\x01\x00\x00")


def is_format(header):
    """Return True when *header* starts like a Tajima DST file."""
    return len(header) >= 3 and header[:3] in _HEADER_SIGNATURES


def _parse_header(raw):
    """Return the header's two-letter fields as a dict of strings."""
    fields = {}
    text = raw.decode("latin-1", errors="replace")
    for entry in text.split("\r"):
        if len(entry) >= 3 and entry[2] == ":":
            fields[entry[:2]] = entry[3:].strip()
    return fields


def _decode_delta(b0, b1, b2):
    """Return the (dx, dy) movement in DST units stored in one record."""
    dx = 0
    dy = 0
    if b0 & 0x01: dx += 1
    if b0 & 0x02: dx -= 1
    if b0 & 0x04: dx += 9
    if b0 & 0x08: dx -= 9
    if b0 & 0x10: dy -= 9
    if b0 & 0x20: dy += 9
    if b0 & 0x40: dy -= 1
    if b0 & 0x80: dy += 1

    if b1 & 0x01: dx += 3
    if b1 & 0x02: dx -= 3
    if b1 & 0x04: dx += 27
    if b1 & 0x08: dx -= 27
    if b1 & 0x10: dy -= 27
    if b1 & 0x20: dy += 27
    if b1 & 0x40: dy -= 3
    if b1 & 0x80: dy += 3

    if b2 & 0x04: dx += 81
    if b2 & 0x08: dx -= 81
    if b2 & 0x10: dy -= 81
    if b2 & 0x20: dy += 81
    return dx, dy


def _command(b2):
    """Return 'stitch', 'jump', 'color', 'end' or 'sequin' for record byte 2."""
    c0 = bool(b2 & 0x80)
    c1 = bool(b2 & 0x40)
    if c0 and c1:
        # END carries the unused y+81/y-81 bits; a stop or colour change is the
        # same c0/c1 pair without them.
        return "end" if (b2 & 0x30) == 0x30 else "color"
    if c0:
        return "jump"
    if c1:
        return "sequin"
    return "stitch"


def _drop_trailing_color_changes(elements):
    """Remove colour changes that no stitch follows; return how many."""
    dropped = 0
    while elements and elements[-1][0] == ELEM_COLOR:
        elements.pop()
        dropped += 1
    return dropped


def load(path):
    """Load a Tajima DST file as a hoop pattern.

    DST carries no thread table, so the pattern is left without a palette; the
    application supplies the default machine palette for embroidery designs.
    The design is scaled to canvas units and centred in the smallest hoop that
    holds it.

    Returns ``(pattern, info)``; see file_io.load_pattern() for the contract.
    """
    pattern = StitchPattern()
    info = PatternFileInfo(input_file_format="DST")

    with open(path, "rb") as f:
        header = f.read(HEADER_SIZE)
        if len(header) < HEADER_SIZE:
            raise ValueError(_tr("File too short"))
        fields = _parse_header(header)
        records = f.read()

    if file_io.DEBUG:
        print(f"\n\nReading DST pattern: {fields}")

    all_elems = []
    x = 0
    y = 0
    stitches = 0
    jumps = 0
    color_changes = 0
    ended = False
    record_count = len(records) // RECORD_SIZE

    for index in range(record_count):
        offset = index * RECORD_SIZE
        b0, b1, b2 = records[offset], records[offset + 1], records[offset + 2]
        command = _command(b2)
        if command == "end":
            ended = True
            break

        dx, dy = _decode_delta(b0, b1, b2)
        x += dx
        y += dy

        if command == "stitch":
            all_elems.append((ELEM_STITCH, x, y))
            stitches += 1
        elif command == "color":
            # DST only records that the machine stops, not which thread comes
            # next, so threads advance through the default palette in order.
            all_elems.append((ELEM_COLOR, (color_changes + 1) % len(DEFAULT_PALETTE)))
            color_changes += 1
        elif command == "jump":
            jumps += 1
        # 'sequin' only moves the needle; the model has no sequin record.

        if file_io.DEBUG == 1 or (file_io.DEBUG > 1 and command != "stitch"):
            print(f"Record {index}: {command} at ({x}, {y})")

    if not stitches:
        raise ValueError(_tr("The DST file contains no stitches"))

    trailing = len(records) % RECORD_SIZE
    if trailing:
        tail = records[record_count * RECORD_SIZE:]
        # Wilcom DST files end with a single 0x1A; anything else is garbage.
        if tail != b"\x1a":
            info.warnings.append(_tr(
                "Unexpected trailing bytes at the end of the DST file"))

    try:
        expected = int(fields.get("ST", ""))
    except ValueError:
        expected = 0
    parsed_records = stitches + jumps + color_changes + (1 if ended else 0)
    if not ended and expected and parsed_records < expected:
        info.warnings.append(_tr(
            "File appears to be truncated: only {0} of {1} records could be read"
        ).format(parsed_records, expected))

    color_changes -= _drop_trailing_color_changes(all_elems)

    # DST coordinates are relative to the design origin, so the design is
    # centred in the smallest hoop that holds it.
    pattern.stitch_type, converted, warning = file_io.place_in_hoop(
        all_elems, DST_TO_CANVAS)
    if warning:
        info.warnings.append(warning)

    info.stitch_type = pattern.stitch_type
    info.stitch_count = stitches
    info.has_color_palette = False
    info.color_count = 0
    info.color_changes = color_changes

    pattern._load_elements(converted)
    if file_io.DEBUG:
        print(f"DST design placed in {pattern.stitch_type}; {stitches} stitches, "
              f"{jumps} jumps, {color_changes} colour changes")
    return pattern, info
