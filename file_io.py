"""Binary file I/O for stitch patterns.

File format (PCD/PCQ compatible):
  Header:  byte 0x32
           byte 0x00 (9mm) or 0x01 (MAXI) or 0x02 (small hoop) or 0x03 (large hoop)
           uint16 color count
           uint16 element record count
  Colors:  color_count × (R G B padding)
  Body:    record_count × (byte c0, 3-byte x LE, byte c1, 3-byte y LE, byte control)

Element encoding:
  control == 0: ELEM_STITCH  — normal stitch point, coords in x/y
  control == 2: ELEM_AUTO    — automatic stitch point, coords in x/y
  control == 3: ELEM_COLOR   — color change; new palette index stored in c0
  control == 4: ELEM_TRIM    — trim point; line suppressed only between two consecutive trims

Legacy format (read-only, produced by old save_pattern):
  control == 1: old-style color-change marker; treated as ELEM_COLOR with
                inferred sequential color index.

All integer values are little-endian.
"""

import struct
from model import StitchPattern, ELEM_STITCH, ELEM_AUTO, ELEM_COLOR, ELEM_TRIM

HEADER_FMT = '<BBH'  # header_byte(1) + stitch_type(1) + color_count(2) = 4 bytes
POINT_FMT = '<B3sB3sB'  # c0(1) + x(3, LE) + c1(1) + y(3, LE) + control_byte(1) = 9 bytes

DEBUG = 0  # 1: print details of each element record; 2: print only for non-zero control bytes


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
        raise ValueError("Invalid/unsupported stitch type")

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
            elif kind == ELEM_TRIM:
                x, y = elem[1], elem[2]
                f.write(struct.pack(POINT_FMT, 0, x.to_bytes(3, 'little'), 0, y.to_bytes(3, 'little'), 0x04))
    pattern.modified = False


def load_pattern(path):
    """Load a StitchPattern from a binary file. Returns a new StitchPattern."""
    pattern = StitchPattern()
    with open(path, 'rb') as f:
        # Read and validate header
        header_size = struct.calcsize(HEADER_FMT)
        header_data = f.read(header_size)
        if len(header_data) < header_size:
            raise ValueError("File too short")

        magic_number, stitch_type_byte, color_count = struct.unpack(HEADER_FMT, header_data)

        if magic_number != 0x32:
            raise ValueError("Invalid file format")

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
            raise ValueError("Invalid/unsupported stitch type")

        # Read colors
        for _ in range(color_count):
            color_data = f.read(3)
            if len(color_data) < 3:
                raise ValueError("Unexpected end of file while reading colors")
            r, g, b = struct.unpack('BBB', color_data)
            pattern.colors.append((r, g, b))
            f.read(1)  # skip padding byte

        stitch_count_data = f.read(2)
        if len(stitch_count_data) < 2:
            raise ValueError("Unexpected end of file while reading stitch count")
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

        for i in range(stitch_count):
            point_data = f.read(point_size)
            if len(point_data) < point_size:
                raise ValueError("Unexpected end of file while reading stitch points")
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
                # all_elems.append((ELEM_STITCH, x + c0 / 256, y + c1 / 256))
                all_elems.append((ELEM_STITCH, round(x + c0 / 256), round(y + c1 / 256))) # align to grid, machine can't do fractions anyway (WYSIWYG)
            elif control_byte == 0x01:
                # Legacy color-change marker: infer sequential palette index
                if pattern.colors:
                    all_elems.append((ELEM_COLOR, legacy_color_idx))
                    legacy_color_idx += 1
            elif control_byte == 0x02:
                # Automatic stitch point; treated as normal stitch for hoop patterns
                if pattern.stitch_type in ("small hoop", "large hoop"):
                    # all_elems.append((ELEM_STITCH, x + c0 / 256, y + c1 / 256))
                    all_elems.append((ELEM_STITCH, round(x + c0 / 256), round(y + c1 / 256))) # align to grid, machine can't do fractions anyway (WYSIWYG)
                else:
                    all_elems.append((ELEM_AUTO, x + c0 / 256, y + c1 / 256)) # include fractional parts; user can control this later
            elif control_byte == 0x03:
                # Color change: palette index in c0
                all_elems.append((ELEM_COLOR, c0))
            elif control_byte == 0x04:
                # Trim: line is drawn to this point, line broken after it
                all_elems.append((ELEM_TRIM, round(x + c0 / 256), round(y + c1 / 256))) # align to grid, machine can't do fractions anyway (WYSIWYG)
            # Any other control byte is silently ignored.

    pattern._load_elements(all_elems)
    return pattern
