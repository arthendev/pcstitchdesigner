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

DEBUG = 1

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
        # Stitch count includes color-change and jump-stitch marker records
        stitch_count = len(pattern.points) + len(pattern.color_segments) + len(pattern.jump_stitches)
        f.write(struct.pack('<H', stitch_count))
        # Write points, inserting marker records before each affected stitch
        change_indices = set(pattern.color_segments)
        jump_indices = set(pattern.jump_stitches)
        for i, (x, y) in enumerate(pattern.points):
            if i in change_indices:
                # Color-change marker record
                x_bytes_m = x.to_bytes(3, 'little')
                y_bytes_m = y.to_bytes(3, 'little')
                f.write(struct.pack(POINT_FMT, 0, x_bytes_m, 0, y_bytes_m, 0x01))
            if i in jump_indices:
                # Jump-stitch marker record
                x_bytes_m = x.to_bytes(3, 'little')
                y_bytes_m = y.to_bytes(3, 'little')
                f.write(struct.pack(POINT_FMT, 0, x_bytes_m, 0, y_bytes_m, 0x04))
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
        
        if DEBUG:
            print("\n\nReading " + pattern.stitch_type + " pattern with " + str(color_count) + " colors and " + str(stitch_count) + " stitch records:")
        for i in range(stitch_count):
            point_data = f.read(point_size)
            if len(point_data) < point_size:
                raise ValueError("Unexpected end of file while reading stitch points")
            c0, x_bytes, c1, y_bytes, control_byte = struct.unpack(POINT_FMT, point_data)
            x = int.from_bytes(x_bytes, 'little')
            y = int.from_bytes(y_bytes, 'little')
            
            if DEBUG:
                print(f"Read point {i}:", end=" ")
            
                if c0 == 0x00:
                    print(f"c0={c0:3}", end=" ")
                else:
                    print(f"\033[91mc0={c0:3}\033[0m", end=" ")  # Highlight non-zero c0 in red
                
                print(f"x={x:3}", end=" ")  # x coordinate
                
                if c1 == 0x00:
                    print(f"c1={c1:3}", end=" ")
                else:
                    print(f"\033[91mc1={c1:3}\033[0m", end=" ")  # Highlight non-zero c1 in red
                
                print(f"y={y:3}", end=" ")  # y coordinate
                
                if control_byte == 0x00:
                    print(f"control_byte={control_byte:#04x}")
                else:
                    print(f"\033[91mcontrol_byte={control_byte:#04x}\033[0m")  # Highlight non-zero control_byte in red

                # if control_byte == 0x00:
                #     print(f"Read point {i}: c0={c0:#04x}, x={x:3}, c1={c1:#04x}, y={y:3}, control_byte={control_byte:#04x}")
                # else:
                #     print(f"\033[91mRead point {i}: c0={c0:#04x}, x={x:3}, c1={c1:#04x}, y={y:3}, control_byte={control_byte:#04x}\033[0m")
            
            if control_byte == 0x00:
                # Normal stitch point
                pattern.points.append((x, y))
            elif control_byte & 0x01:
                # Color change: record the first point index for this palette color
                if pattern.colors:
                    pattern.color_segments.append(len(pattern.points))
            elif control_byte & 0x02:
                # Filler point
                pattern.points.append((x, y))
            elif control_byte & 0x04:
                # Jump stitch: the next point starts a new segment (no connecting line)
                pattern.points.append((x, y))
                #pattern.jump_stitches.add(len(pattern.points))

    
    pattern.modified = False
    return pattern
