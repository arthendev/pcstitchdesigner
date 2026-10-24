"""Binary file I/O for stitch patterns.

Public entry points:
  load_pattern(path) -> (StitchPattern, PatternFileInfo)
  save_pattern(path, pattern, info=None, image_data=None)

The concrete formats live in their own modules. Loading picks the module from
the file content, saving from the file extension:

  file_io_pcd_pcq_pcs.py   .pcd, .pcq, .pcs   magic 0x31 / 0x32
  file_io_vcsm_9mm_max.py  .9mm, .max         magic "VCSM"

Conventions shared by both formats:
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

DEBUG = 1  # 1: print details of each element record; 2: print only for non-zero control bytes


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
    with open(path, 'rb') as f:
        header = f.read(5)
    if len(header) < 2:
        raise ValueError(_tr("File too short"))

    # Imported here rather than at module level: the format modules import this
    # module for PatternFileInfo and _tr, which would otherwise be a cycle.
    import file_io_pcd_pcq_pcs
    import file_io_vcsm_9mm_max

    if file_io_vcsm_9mm_max.is_format(header):
        return file_io_vcsm_9mm_max.load(path)
    if file_io_pcd_pcq_pcs.is_format(header):
        return file_io_pcd_pcq_pcs.load(path)
    raise ValueError(_tr("Invalid file format"))


def save_pattern(path, pattern, info=None, image_data=None):
    """Save a StitchPattern, choosing the format from the file extension.

    ``.9mm`` and ``.max`` paths are written by file_io_vcsm_9mm_max.save();
    every other extension is written by file_io_pcd_pcq_pcs.save(). *info* is
    read by both writers.

    *image_data* is the template preview as a raster image (QPixmap), not
    encoded bytes: the writer that can store an image converts it to whatever
    its format needs. Only the VCSM format embeds an image.
    """
    import file_io_pcd_pcq_pcs
    import file_io_vcsm_9mm_max

    if os.path.splitext(path)[1].lower() in file_io_vcsm_9mm_max.EXTENSIONS:
        file_io_vcsm_9mm_max.save(path, pattern, info=info, image_data=image_data)
    else:
        file_io_pcd_pcq_pcs.save(path, pattern, info=info)


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
