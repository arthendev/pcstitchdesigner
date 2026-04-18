"""Binary file I/O for stitch patterns.

File format (PCD/PCQ compatible):
  Header:  byte 0x32
           byte 0x00 (9mm) or 0x01 (MAXI)
           uint16 unknown
           uint16 point count
  Body:    N × (byte unk, uint16 x, byte unk, byte unk, uint16 y, uint16 unk)

All values are little-endian.
"""

import struct
from model import StitchPattern

HEADER_FMT = '<BBHH'  # header_byte(1) + stitch_type(1) + unk1(2) + count(2) = 6 bytes
POINT_FMT = '<BHBBHH'  # unk3(1) + x(2) + unk4(1) + unk5(1) + y(2) + unk6(2) = 9 bytes


def save_pattern(path, pattern):
    """Save a StitchPattern to a binary file."""
    stitch_type_byte = 0x01 if pattern.stitch_type == "MAXI" else 0x00
    
    with open(path, 'wb') as f:
        # Write header: 0x32, stitch_type, unk1=0, count
        f.write(struct.pack(HEADER_FMT, 0x32, stitch_type_byte, 0, len(pattern.points)))
        # Write points: unk3=0, x, unk4=0, unk5=0, y, unk6=0
        for x, y in pattern.points:
            f.write(struct.pack(POINT_FMT, 0, x, 0, 0, y, 0))
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
        
        magic_number, stitch_type_byte, unk1, count = struct.unpack(HEADER_FMT, header_data)
        
        if magic_number != 0x32:
            raise ValueError("Invalid file format")
        if stitch_type_byte not in (0x00, 0x01):
            raise ValueError("Invalid/unsupported stitch type")
        
        # Set stitch type
        if stitch_type_byte == 0x00:
            pattern.stitch_type = "9mm"
        elif stitch_type_byte == 0x01:
            pattern.stitch_type = "MAXI"
        else:
            raise ValueError("Invalid/unsupported stitch type")
        
        # Read points
        point_size = struct.calcsize(POINT_FMT)
        for _ in range(count):
            point_data = f.read(point_size)
            if len(point_data) < point_size:
                raise ValueError("Unexpected end of file")
            unk3, x, unk4, unk5, y, unk6 = struct.unpack(POINT_FMT, point_data)
            pattern.points.append((x, y))
    
    pattern.modified = False
    return pattern
