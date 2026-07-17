"""
SlugRecover — File Signature Database
Magic bytes, header patterns, max sizes, and end markers for file carving.
"""

import struct
from dataclasses import dataclass, field
from typing import Optional, List, Callable


@dataclass
class FileSignature:
    """Describes a file type's binary signature for carving."""
    name: str                   # Display name (e.g. "Canon CR2")
    extension: str              # File extension (e.g. "cr2")
    category: str               # Category: "image", "video", "document"
    header: bytes               # Magic bytes at file start
    header_offset: int = 0      # Offset where header appears
    extra_check: Optional[Callable] = None  # Additional validation function
    footer: Optional[bytes] = None          # End-of-file marker
    max_size: int = 50 * 1024 * 1024        # Max file size (default 50MB)
    min_size: int = 1024                     # Min file size (default 1KB)
    parse_size: Optional[Callable] = None   # Function to parse actual size from header
    color: str = "#888888"                  # Badge color in UI
    icon: str = "📄"                        # Emoji icon


# ─── Size Parsers ───────────────────────────────────────────────────────────

def parse_cr2_size(data: bytes, max_read: int = 50 * 1024 * 1024) -> Optional[int]:
    """Parse CR2 file size from TIFF IFD chain. Returns estimated size."""
    try:
        if len(data) < 16:
            return None
        # CR2 is little-endian TIFF
        byte_order = data[0:2]
        if byte_order == b'II':
            fmt = '<'
        elif byte_order == b'MM':
            fmt = '>'
        else:
            return None

        # Read IFD0 offset
        ifd0_offset = struct.unpack(fmt + 'I', data[4:8])[0]

        # Walk IFD chain to find the last strip/tile offset + size
        max_offset = 0
        offset = ifd0_offset
        visited = set()

        for _ in range(10):  # Max 10 IFDs
            if offset in visited or offset < 8 or offset + 2 > len(data):
                break
            visited.add(offset)

            num_entries = struct.unpack(fmt + 'H', data[offset:offset + 2])[0]
            if num_entries > 1000:
                break

            pos = offset + 2
            for _ in range(num_entries):
                if pos + 12 > len(data):
                    break
                tag = struct.unpack(fmt + 'H', data[pos:pos + 2])[0]
                typ = struct.unpack(fmt + 'H', data[pos + 2:pos + 4])[0]
                count = struct.unpack(fmt + 'I', data[pos + 4:pos + 8])[0]
                value = struct.unpack(fmt + 'I', data[pos + 8:pos + 12])[0]

                # StripOffsets (273), StripByteCounts (279), TileOffsets (324), TileByteCounts (325)
                if tag in (273, 324) and count == 1:
                    end = value
                    # Look for corresponding byte count
                elif tag in (279, 325) and count == 1:
                    pass
                # Track max offset seen in any value field
                if tag in (273, 324, 279, 325):
                    if count == 1:
                        max_offset = max(max_offset, value)

                pos += 12

            # Next IFD offset
            next_ifd_pos = pos
            if next_ifd_pos + 4 > len(data):
                break
            offset = struct.unpack(fmt + 'I', data[next_ifd_pos:next_ifd_pos + 4])[0]
            if offset == 0:
                break

        if max_offset > 0:
            # Add some padding for the actual strip data
            return min(max_offset + 5 * 1024 * 1024, max_read)
        return None
    except Exception:
        return None


def parse_bmff_size(data: bytes, max_read: int = 500 * 1024 * 1024) -> Optional[int]:
    """Parse ISO Base Media File Format (MP4/MOV/CR3/HEIF) size from box headers."""
    try:
        offset = 0
        total_size = 0
        while offset < len(data) and offset < max_read:
            if offset + 8 > len(data):
                break
            box_size = struct.unpack('>I', data[offset:offset + 4])[0]
            box_type = data[offset + 4:offset + 8]

            if box_size == 0:
                # Box extends to end of file — can't determine
                return None
            elif box_size == 1:
                # 64-bit extended size
                if offset + 16 > len(data):
                    break
                box_size = struct.unpack('>Q', data[offset + 8:offset + 16])[0]

            if box_size < 8:
                break

            total_size = offset + box_size
            offset += box_size

            # If we've found mdat (media data), that's typically the last big box
            if box_type == b'mdat':
                return min(total_size, max_read)

        return min(total_size, max_read) if total_size > 0 else None
    except Exception:
        return None


def parse_jpeg_size(data: bytes, max_read: int = 30 * 1024 * 1024) -> Optional[int]:
    """Find JPEG end marker FF D9."""
    # Search for FFD9 — but skip embedded thumbnails by searching from the end
    search_limit = min(len(data), max_read)
    # Search backwards from the end for the last FFD9
    pos = data.rfind(b'\xff\xd9', 0, search_limit)
    if pos > 0:
        return pos + 2
    return None


def parse_png_size(data: bytes, max_read: int = 30 * 1024 * 1024) -> Optional[int]:
    """Find PNG IEND chunk."""
    # IEND chunk: length(4) + 'IEND' + CRC(4)
    pos = data.find(b'IEND', 8, min(len(data), max_read))
    if pos > 0:
        return pos + 8  # 4 bytes type + 4 bytes CRC
    return None


def parse_gif_size(data: bytes, max_read: int = 30 * 1024 * 1024) -> Optional[int]:
    """Find GIF trailer byte 0x3B."""
    pos = data.rfind(b'\x3b', 0, min(len(data), max_read))
    if pos > 0:
        return pos + 1
    return None


def parse_pdf_size(data: bytes, max_read: int = 100 * 1024 * 1024) -> Optional[int]:
    """Find PDF %%EOF marker."""
    search_limit = min(len(data), max_read)
    pos = data.rfind(b'%%EOF', 0, search_limit)
    if pos > 0:
        return pos + 5
    return None


def parse_zip_size(data: bytes, max_read: int = 200 * 1024 * 1024) -> Optional[int]:
    """Find ZIP end of central directory record."""
    search_limit = min(len(data), max_read)
    # End of central directory signature: 50 4B 05 06
    pos = data.rfind(b'\x50\x4b\x05\x06', 0, search_limit)
    if pos > 0:
        if pos + 22 <= len(data):
            comment_len = struct.unpack('<H', data[pos + 20:pos + 22])[0]
            return pos + 22 + comment_len
        return pos + 22
    return None


def parse_riff_size(data: bytes, max_read: int = 500 * 1024 * 1024) -> Optional[int]:
    """Parse RIFF container size (WEBP, AVI)."""
    try:
        if len(data) < 12:
            return None
        size = struct.unpack('<I', data[4:8])[0]
        return min(size + 8, max_read)
    except Exception:
        return None


def parse_bmp_size(data: bytes, max_read: int = 50 * 1024 * 1024) -> Optional[int]:
    """Parse BMP file size from header."""
    try:
        if len(data) < 6:
            return None
        size = struct.unpack('<I', data[2:6])[0]
        if size < 54:  # BMP header minimum
            return None
        return min(size, max_read)
    except Exception:
        return None


# ─── Extra Validation Checks ───────────────────────────────────────────────

def check_cr2(data: bytes) -> bool:
    """Validate CR2: TIFF header + CR2 marker at offset 8-9."""
    if len(data) < 16:
        return False
    # Check byte order
    if data[0:2] not in (b'II', b'MM'):
        return False
    # Check TIFF magic
    if data[0:2] == b'II':
        magic = struct.unpack('<H', data[2:4])[0]
    else:
        magic = struct.unpack('>H', data[2:4])[0]
    if magic != 42:
        return False
    # CR2 marker at offset 8: "CR" followed by version
    return data[8:10] == b'CR'


def check_cr3(data: bytes) -> bool:
    """Validate CR3: ftyp box with 'crx ' brand."""
    if len(data) < 12:
        return False
    # ftyp box
    if data[4:8] != b'ftyp':
        return False
    return data[8:12] == b'crx '


def check_mov(data: bytes) -> bool:
    """Validate MOV: ftyp with qt brand or moov/wide/free atoms."""
    if len(data) < 12:
        return False
    if data[4:8] == b'ftyp':
        brand = data[8:12]
        return brand in (b'qt  ', b'mqt ')
    # Some MOV files start with moov, wide, or free atoms
    return data[4:8] in (b'moov', b'wide', b'free', b'mdat', b'pnot')


def check_mp4(data: bytes) -> bool:
    """Validate MP4: ftyp with common MP4 brands."""
    if len(data) < 12:
        return False
    if data[4:8] != b'ftyp':
        return False
    brand = data[8:12]
    mp4_brands = [b'isom', b'mp41', b'mp42', b'M4V ', b'M4A ', b'f4v ',
                  b'dash', b'avc1', b'iso2', b'iso5', b'iso6', b'mp71']
    return brand in mp4_brands


def check_heif(data: bytes) -> bool:
    """Validate HEIF/HIF: ftyp with HEIC brands."""
    if len(data) < 12:
        return False
    if data[4:8] != b'ftyp':
        return False
    brand = data[8:12]
    heif_brands = [b'heic', b'heix', b'mif1', b'heim', b'heis',
                   b'avci', b'hevc', b'hevx']
    return brand in heif_brands


def check_tiff_not_cr2(data: bytes) -> bool:
    """Validate TIFF but NOT CR2 (CR2 has its own signature)."""
    if len(data) < 16:
        return False
    if data[0:2] not in (b'II', b'MM'):
        return False
    # Exclude CR2
    if data[8:10] == b'CR':
        return False
    return True


def check_webp(data: bytes) -> bool:
    """Validate WEBP: RIFF + WEBP."""
    return len(data) >= 12 and data[0:4] == b'RIFF' and data[8:12] == b'WEBP'


def check_avi(data: bytes) -> bool:
    """Validate AVI: RIFF + AVI."""
    return len(data) >= 12 and data[0:4] == b'RIFF' and data[8:12] == b'AVI '


# ─── Signature Database ────────────────────────────────────────────────────

SIGNATURES: List[FileSignature] = [
    # 1. Canon CR2
    FileSignature(
        name="Canon CR2",
        extension="cr2",
        category="image",
        header=b'\x49\x49\x2a\x00',  # Little-endian TIFF
        extra_check=check_cr2,
        max_size=80 * 1024 * 1024,   # CR2 can be up to ~80MB
        min_size=5 * 1024 * 1024,    # Usually at least 5MB
        parse_size=parse_cr2_size,
        color="#e94560",
        icon="📷",
    ),

    # 2. Canon CR3
    FileSignature(
        name="Canon CR3",
        extension="cr3",
        category="image",
        header=b'\x00\x00\x00',     # ftyp box (variable first 4 bytes = size)
        extra_check=check_cr3,
        max_size=120 * 1024 * 1024,  # CR3 can be larger
        min_size=5 * 1024 * 1024,
        parse_size=parse_bmff_size,
        color="#ff6b6b",
        icon="📷",
    ),

    # 3. JPEG
    FileSignature(
        name="JPEG",
        extension="jpg",
        category="image",
        header=b'\xff\xd8\xff',
        footer=b'\xff\xd9',
        max_size=30 * 1024 * 1024,
        min_size=2 * 1024,
        parse_size=parse_jpeg_size,
        color="#f39c12",
        icon="🖼️",
    ),

    # 4. MOV
    FileSignature(
        name="MOV",
        extension="mov",
        category="video",
        header=b'\x00\x00\x00',     # ftyp/moov box
        extra_check=check_mov,
        max_size=4 * 1024 * 1024 * 1024,  # 4GB
        min_size=10 * 1024,
        parse_size=parse_bmff_size,
        color="#9b59b6",
        icon="🎬",
    ),

    # 5. MP4
    FileSignature(
        name="MP4",
        extension="mp4",
        category="video",
        header=b'\x00\x00\x00',     # ftyp box
        extra_check=check_mp4,
        max_size=4 * 1024 * 1024 * 1024,  # 4GB
        min_size=10 * 1024,
        parse_size=parse_bmff_size,
        color="#8e44ad",
        icon="🎥",
    ),

    # 6. HEIF/HIF
    FileSignature(
        name="HEIF",
        extension="heif",
        category="image",
        header=b'\x00\x00\x00',     # ftyp box
        extra_check=check_heif,
        max_size=80 * 1024 * 1024,
        min_size=10 * 1024,
        parse_size=parse_bmff_size,
        color="#e74c3c",
        icon="🖼️",
    ),

    # 7. PNG
    FileSignature(
        name="PNG",
        extension="png",
        category="image",
        header=b'\x89\x50\x4e\x47\x0d\x0a\x1a\x0a',
        max_size=30 * 1024 * 1024,
        min_size=100,
        parse_size=parse_png_size,
        color="#27ae60",
        icon="🖼️",
    ),

    # 8. TIFF (non-CR2)
    FileSignature(
        name="TIFF",
        extension="tiff",
        category="image",
        header=b'\x49\x49\x2a\x00',  # Little-endian
        extra_check=check_tiff_not_cr2,
        max_size=100 * 1024 * 1024,
        min_size=1024,
        parse_size=parse_cr2_size,  # Same IFD parsing
        color="#16a085",
        icon="🖼️",
    ),
    FileSignature(
        name="TIFF",
        extension="tiff",
        category="image",
        header=b'\x4d\x4d\x00\x2a',  # Big-endian
        max_size=100 * 1024 * 1024,
        min_size=1024,
        color="#16a085",
        icon="🖼️",
    ),

    # 9. PDF
    FileSignature(
        name="PDF",
        extension="pdf",
        category="document",
        header=b'\x25\x50\x44\x46',  # %PDF
        max_size=200 * 1024 * 1024,
        min_size=100,
        parse_size=parse_pdf_size,
        color="#c0392b",
        icon="📑",
    ),

    # 10. DOCX/ZIP
    FileSignature(
        name="ZIP/DOCX",
        extension="zip",
        category="document",
        header=b'\x50\x4b\x03\x04',
        max_size=200 * 1024 * 1024,
        min_size=100,
        parse_size=parse_zip_size,
        color="#2980b9",
        icon="📦",
    ),

    # 11. GIF
    FileSignature(
        name="GIF",
        extension="gif",
        category="image",
        header=b'\x47\x49\x46\x38',  # GIF8
        max_size=30 * 1024 * 1024,
        min_size=100,
        parse_size=parse_gif_size,
        color="#1abc9c",
        icon="🖼️",
    ),

    # 12. BMP
    FileSignature(
        name="BMP",
        extension="bmp",
        category="image",
        header=b'\x42\x4d',  # BM
        max_size=50 * 1024 * 1024,
        min_size=54,
        parse_size=parse_bmp_size,
        color="#d35400",
        icon="🖼️",
    ),

    # 13. WEBP
    FileSignature(
        name="WEBP",
        extension="webp",
        category="image",
        header=b'\x52\x49\x46\x46',  # RIFF
        extra_check=check_webp,
        max_size=30 * 1024 * 1024,
        min_size=100,
        parse_size=parse_riff_size,
        color="#2ecc71",
        icon="🖼️",
    ),

    # 14. AVI
    FileSignature(
        name="AVI",
        extension="avi",
        category="video",
        header=b'\x52\x49\x46\x46',  # RIFF
        extra_check=check_avi,
        max_size=4 * 1024 * 1024 * 1024,  # 4GB
        min_size=1024,
        parse_size=parse_riff_size,
        color="#3498db",
        icon="🎬",
    ),
]


def get_signature_by_extension(ext: str) -> Optional[FileSignature]:
    """Get first matching signature by extension."""
    ext = ext.lower().lstrip('.')
    for sig in SIGNATURES:
        if sig.extension == ext:
            return sig
    return None


def get_all_extensions() -> list:
    """Get unique list of all supported extensions."""
    seen = set()
    result = []
    for sig in SIGNATURES:
        if sig.extension not in seen:
            seen.add(sig.extension)
            result.append(sig.extension)
    return result


def get_signatures_for_types(types: list) -> List[FileSignature]:
    """Filter signatures to only include specified types (by extension)."""
    if not types:
        return SIGNATURES
    type_set = {t.lower().lstrip('.') for t in types}
    return [s for s in SIGNATURES if s.extension in type_set]


def get_signature_info() -> list:
    """Get signature info for UI display."""
    seen = set()
    result = []
    for sig in SIGNATURES:
        if sig.extension not in seen:
            seen.add(sig.extension)
            result.append({
                'name': sig.name,
                'extension': sig.extension,
                'category': sig.category,
                'color': sig.color,
                'icon': sig.icon,
                'max_size_mb': sig.max_size / (1024 * 1024),
            })
    return result
