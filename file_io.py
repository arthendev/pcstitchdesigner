"""Binary file I/O for stitch patterns.

Public entry points:
  load_pattern(path) -> (StitchPattern, PatternFileInfo)
  save_pattern(path, pattern, info=None, image_data=None)

The concrete formats live in their own modules. Loading picks the module from
the file content, saving from the file extension:

  file_io_pcd_pcq_pcs.py   .pcd, .pcq, .pcs   magic 0x31 / 0x32
  file_io_vcsm_9mm_max.py  .9mm, .max         magic "VCSM"
  file_io_dst.py           .dst               header record "LA:" (import only)
  file_io_jef.py           .jef               header layout (import only)
  file_io_pec_pes.py       .pec, .pes         "#PEC0001" / "#PES" (import only)

Conventions shared by all formats:
  * Every loader returns ``(pattern, info)``. PatternFileInfo reports what was
    actually read, so callers can compare it with what the header claims.
  * A file that ends before its declared record count is still loaded: the
    partial result is returned and info.warnings explains the truncation.
    Structurally broken headers raise ValueError.
  * StitchPattern coordinates are in 1/6 mm units (StitchPattern.STITCH_RES_MM);
    each format module converts its own physical resolution.
  * DEBUG is the single tracing switch. The format modules read it as
    ``file_io.DEBUG``, so assigning here affects every parser.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PyQt5.QtCore import QCoreApplication

from model import ELEM_COLOR, ELEM_STITCH, StitchPattern, elem_has_coords

# DEBUG print
# 1: print details of each element record
# 2: print only selected info (PCD/PCQ/PCS: non-zero control bytes)
DEBUG = 0

def _tr(s):
    return QCoreApplication.translate("FileIO", s)


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
    template_image_filename: Optional[str] = None  # optional BMP file name, None if absent/invalid
    note: Optional[str] = None  # optional note, None if absent/invalid
    # VCSM-only metadata (.9mm / .max files)
    sewing_foot: Optional[str] = None  # sewing foot type, e.g. "2A"
    needle_system: Optional[str] = None  # needle system, e.g. "130/705"
    tension: Optional[float] = None  # thread tension, e.g. 2.5
    thread_color: Optional[Tuple[int, int, int]] = None  # (R, G, B) thread colour
    template_image_data: Optional[bytes] = None  # embedded template image bytes
    warnings: List[str] = field(default_factory=list)


def load_pattern(path):
    """Load a stitch pattern, choosing the parser from the file content.

    Returns ``(pattern, info)``: the parsed pattern and a PatternFileInfo
    describing the file. Structurally broken headers raise ValueError; a file
    that ends before the header's record count is loaded partially and reported
    through ``info.warnings``.
    """
    # 0x20 bytes: JEF has no magic number and is recognised from its header
    # layout, which names the thread table length at 0x18.
    with open(path, 'rb') as f:
        header = f.read(0x20)
    if len(header) < 2:
        raise ValueError(_tr("File too short"))

    # Imported here rather than at module level: the format modules import this
    # module for PatternFileInfo and _tr, which would otherwise be a cycle.
    import file_io_pcd_pcq_pcs
    import file_io_vcsm_9mm_max
    import file_io_dst
    import file_io_jef
    import file_io_pec_pes

    if file_io_vcsm_9mm_max.is_format(header):
        return file_io_vcsm_9mm_max.load(path)
    if file_io_pcd_pcq_pcs.is_format(header):
        return file_io_pcd_pcq_pcs.load(path)
    if file_io_dst.is_format(header):
        return file_io_dst.load(path)
    if file_io_jef.is_format(header):
        return file_io_jef.load(path)
    if file_io_pec_pes.is_format(header):
        return file_io_pec_pes.load(path)
    # JEF writers that pad the header do not match the layout signature, so a
    # file with the format's own extension is handed to it anyway; the reader
    # validates the header again.
    if os.path.splitext(path)[1].lower() in file_io_jef.EXTENSIONS:
        return file_io_jef.load(path)
    raise ValueError(_tr("Invalid file format"))


def save_pattern(path, pattern, info=None, image_data=None):
    """Save a StitchPattern, choosing the format from the file extension.

    ``.9mm`` and ``.max`` paths are written by file_io_vcsm_9mm_max.save();
    every other writable extension is written by file_io_pcd_pcq_pcs.save().
    Import-only formats (DST, JEF, PEC, PES) raise ValueError — see
    :func:`can_save`.

    *image_data* is the template preview as a raster image (QPixmap), not
    encoded bytes: the writer that can store an image converts it to whatever
    its format needs. Only the VCSM format embeds an image.
    """
    import file_io_pcd_pcq_pcs
    import file_io_vcsm_9mm_max

    if not can_save(path):
        raise ValueError(_tr(
            "This file format can only be opened, not saved. "
            "Choose another file name."))

    if os.path.splitext(path)[1].lower() in file_io_vcsm_9mm_max.EXTENSIONS:
        file_io_vcsm_9mm_max.save(path, pattern, info=info, image_data=image_data)
    else:
        file_io_pcd_pcq_pcs.save(path, pattern, info=info)


def place_in_hoop(elements, scale):
    """Centre a design stored relative to its own origin in a supported hoop.

    Third-party embroidery files store stitch positions as deltas from the
    design origin, while the model positions them inside a hoop of fixed size.
    The smallest hoop that holds the design is chosen and the coordinates are
    scaled and shifted so the design's bounding box is centred in it.

    Args:
        elements: ``(type, x, y)`` / ``(ELEM_COLOR, index)`` records in the
            source format's units, with the design starting at its origin.
        scale: canvas units (1/6 mm) per source unit.

    Returns:
        tuple: ``(stitch_type, converted_elements, warning)``. *warning* is a
        message when the design is larger than every hoop — the large hoop is
        used so most of it stays visible — and None otherwise.
    """
    xs = [e[1] for e in elements if e[0] == ELEM_STITCH]
    ys = [e[2] for e in elements if e[0] == ELEM_STITCH]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = (max_x - min_x) * scale
    height = (max_y - min_y) * scale

    warning = None
    for candidate in ("small hoop", "large hoop"):
        canvas_w, canvas_h = StitchPattern.CANVAS_SIZES[candidate]
        if width <= canvas_w and height <= canvas_h:
            stitch_type = candidate
            break
    else:
        # Keep the most of an oversized design visible; the window refuses it
        # after the hoop-size check so the user sees why.
        stitch_type = "large hoop"
        warning = _tr("Pattern size exceeds all supported stitch types; "
                      "using large hoop")
    canvas_w, canvas_h = StitchPattern.CANVAS_SIZES[stitch_type]

    offset_x = round((canvas_w - width) / 2 - min_x * scale)
    offset_y = round((canvas_h - height) / 2 - min_y * scale)
    converted = [
        elem if not elem_has_coords(elem)
        else (elem[0], elem[1] * scale + offset_x, elem[2] * scale + offset_y)
        for elem in elements
    ]
    return stitch_type, converted, warning


def drop_trailing_color_changes(elements):
    """Remove colour changes that no stitch follows; return how many.

    A trailing change cannot be applied to anything, and the file and card
    formats cannot represent one, so the readers drop it and report it as
    not part of the parsed colour-change count.
    """
    dropped = 0
    while elements and elements[-1][0] == ELEM_COLOR:
        elements.pop()
        dropped += 1
    return dropped


def note_max_length(path):
    """Return how many characters a note may have when saved to *path*.

    Each format caps the note differently (see NOTE_MAX_LENGTH in the
    format modules); callers use this to warn the user before a note is
    truncated on save.
    """
    import file_io_pcd_pcq_pcs
    import file_io_vcsm_9mm_max

    if os.path.splitext(path)[1].lower() in file_io_vcsm_9mm_max.EXTENSIONS:
        return file_io_vcsm_9mm_max.NOTE_MAX_LENGTH
    return file_io_pcd_pcq_pcs.NOTE_MAX_LENGTH


def palette_size(path):
    """Return the exact number of palette entries *path*'s format stores.

    Embroidery files hold a fixed thread palette, so a pattern with a
    different number of colors cannot be written before the palette is
    adjusted. The VCSM formats (.9mm/.max) store one thread color and no
    palette, so they impose no size; this returns None for them. DST carries
    no colour table, and JEF, PEC and PES files hold only the threads they use,
    so those return None as well.
    """
    import file_io_pcd_pcq_pcs
    import file_io_vcsm_9mm_max
    import file_io_dst
    import file_io_jef
    import file_io_pec_pes

    if os.path.splitext(path)[1].lower() in file_io_vcsm_9mm_max.EXTENSIONS:
        return None
    if os.path.splitext(path)[1].lower() in file_io_dst.EXTENSIONS:
        return None
    if os.path.splitext(path)[1].lower() in file_io_jef.EXTENSIONS:
        return None
    if os.path.splitext(path)[1].lower() in file_io_pec_pes.EXTENSIONS:
        return None
    return file_io_pcd_pcq_pcs.PALETTE_SIZE


def can_save(path):
    """Return False for import-only formats that save_pattern cannot write.

    Only DST, JEF, PEC and PES are import-only today; every other supported
    extension has a writer (or is treated as a PCD file by convention).
    """
    import file_io_dst
    import file_io_jef
    import file_io_pec_pes

    extension = os.path.splitext(path)[1].lower()
    import_only = (file_io_dst.EXTENSIONS + file_io_jef.EXTENSIONS
                   + file_io_pec_pes.EXTENSIONS)
    return extension not in import_only
