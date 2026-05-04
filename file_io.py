"""Binary file I/O for stitch patterns.

File format (PCD/PCQ compatible):
  Header:  byte 0x32
           byte 0x00 (9mm) or 0x01 (MAXI) or 0x02 (small hoop) or 0x03 (large hoop)
           uint16 color count
           uint16 stitch count
  Body:    N × (byte unk, uint16 x, byte unk, byte unk, uint16 y, uint16 unk)

All values are little-endian.
"""

import struct
from model import StitchPattern

HEADER_FMT = '<BBH'  # header_byte(1) + stitch_type(1) + color_count(2) = 4 bytes
POINT_FMT = '<B3sB3sB'  # c0(1) + x(3, LE) + c1(1) + y(3, LE) + control_byte(1) = 9 bytes


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
        # Write header: 0x32, stitch_type, color_count
        f.write(struct.pack(HEADER_FMT, 0x32, stitch_type_byte, len(pattern.colors)))
        # Write colors: RGB + padding byte
        for r, g, b in pattern.colors:
            f.write(struct.pack('BBBx', r, g, b))
        # Stitch count includes color-change marker records
        stitch_count = len(pattern.points) + len(pattern.color_segments)
        f.write(struct.pack('<H', stitch_count))
        # Write points, inserting a color-change record before each new color segment
        change_indices = set(pattern.color_segments)
        for i, (x, y) in enumerate(pattern.points):
            if i in change_indices:
                # Color-change marker record: same coordinates as the upcoming stitch
                x_bytes_m = x.to_bytes(3, 'little')
                y_bytes_m = y.to_bytes(3, 'little')
                f.write(struct.pack(POINT_FMT, 0, x_bytes_m, 0, y_bytes_m, 0x01))
            x_bytes = x.to_bytes(3, 'little')
            y_bytes = y.to_bytes(3, 'little')
            f.write(struct.pack(POINT_FMT, 0, x_bytes, 0, y_bytes, 0))
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
            color_data = f.read(3)  # RGB (big-endian)
            if len(color_data) < 3:
                raise ValueError("Unexpected end of file while reading colors")
            r, g, b = struct.unpack('BBB', color_data)
            pattern.colors.append((r, g, b))
            f.read(1)  # skip padding byte

        stitch_count_data = f.read(2)
        if len(stitch_count_data) < 2:
            raise ValueError("Unexpected end of file while reading stitch count")
        stitch_count, = struct.unpack('<H', stitch_count_data)

        # Read points — color_change records are stored as-is into color_segments:
        # color_segments[j] = index of the first stitch that uses palette color j.
        # Files always open with a color_change record before any stitches (j=0).
        point_size = struct.calcsize(POINT_FMT)
        for _ in range(stitch_count):
            point_data = f.read(point_size)
            if len(point_data) < point_size:
                raise ValueError("Unexpected end of file while reading stitch points")
            c0, x_bytes, c1, y_bytes, control_byte = struct.unpack(POINT_FMT, point_data)
            if control_byte == 0x00:
                # Normal stitch point
                x = int.from_bytes(x_bytes, 'little')
                y = int.from_bytes(y_bytes, 'little')
                pattern.points.append((x, y))
            elif control_byte & 0x01:
                # Color change: record the first point index for this palette color
                if pattern.colors:
                    pattern.color_segments.append(len(pattern.points))
            elif control_byte & 0x04:
                # jump stitch — ignored
                pass

    
    pattern.modified = False
    return pattern
