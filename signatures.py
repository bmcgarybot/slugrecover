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
    name: str
    extension: str
    category: str               # "image", "video", "audio", "document", "archive"
    header: bytes
    header_offset: int = 0
    extra_check: Optional[Callable] = None
    footer: Optional[bytes] = None
    max_size: int = 50 * 1024 * 1024
    min_size: int = 1024
    parse_size: Optional[Callable] = None
    # Optional seek-based size parser: (file_handle, abs_offset, max_size)
    # -> Optional[int]. Preferred over parse_size when set; lets the
    # parser hop box-to-box with tiny reads instead of one big window.
    parse_size_fh: Optional[Callable] = None
    color: str = "#888888"
    icon: str = "📄"


# ─── Size Parsers ───────────────────────────────────────────────────────────

def parse_tiff_size(data: bytes, max_read: int = 100 * 1024 * 1024) -> Optional[int]:
    """Parse TIFF/RAW file size from IFD chain."""
    try:
        if len(data) < 16:
            return None
        byte_order = data[0:2]
        fmt = '<' if byte_order == b'II' else ('>' if byte_order == b'MM' else None)
        if not fmt:
            return None

        ifd0_offset = struct.unpack(fmt + 'I', data[4:8])[0]
        max_offset = 0
        offset = ifd0_offset
        visited = set()

        for _ in range(10):
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
                count = struct.unpack(fmt + 'I', data[pos + 4:pos + 8])[0]
                value = struct.unpack(fmt + 'I', data[pos + 8:pos + 12])[0]
                if tag in (273, 279, 324, 325) and count == 1:
                    max_offset = max(max_offset, value)
                pos += 12
            next_ifd_pos = pos
            if next_ifd_pos + 4 > len(data):
                break
            offset = struct.unpack(fmt + 'I', data[next_ifd_pos:next_ifd_pos + 4])[0]
            if offset == 0:
                break

        if max_offset > 0:
            return min(max_offset + 5 * 1024 * 1024, max_read)
        return None
    except Exception:
        return None


def parse_bmff_size(data: bytes, max_read: int = 4 * 1024 * 1024 * 1024) -> Optional[int]:
    """Parse ISO BMFF (MP4/MOV/HEIF/CR3/3GP/M4A) size from box headers.

    Walks ALL top-level boxes instead of stopping at the first mdat.
    Camcorders and most cameras write the moov index AFTER mdat — the
    old early-return truncated those files right before moov, producing
    recovered videos that would not play.
    """
    _KNOWN = {b'ftyp', b'moov', b'mdat', b'free', b'skip', b'wide',
              b'pnot', b'uuid', b'moof', b'mfra', b'meta', b'udta',
              b'styp', b'sidx', b'ssix', b'prft', b'mdta'}
    try:
        offset = 0
        total_size = 0
        saw_mdat = False
        while offset < max_read:
            if offset + 8 > len(data):
                # Box header lies beyond what we read; the arithmetic so
                # far (header-only walk) is still valid — stop here.
                break
            box_size = struct.unpack('>I', data[offset:offset + 4])[0]
            box_type = data[offset + 4:offset + 8]
            if box_type not in _KNOWN:
                break  # walked into garbage — trust what we have
            if box_size == 0:
                # "extends to end of file" — unknowable when carving
                break
            elif box_size == 1:
                if offset + 16 > len(data):
                    break
                box_size = struct.unpack('>Q', data[offset + 8:offset + 16])[0]
            if box_size < 8:
                break
            total_size = offset + box_size
            offset += box_size
            if box_type == b'mdat':
                saw_mdat = True
        if total_size > 0 and saw_mdat:
            return min(total_size, max_read)
        # No mdat seen (e.g. HEIF stills) — extent of known boxes
        return min(total_size, max_read) if total_size > 0 else None
    except Exception:
        return None


def parse_bmff_size_fh(fh, abs_offset: int,
                       max_size: int = 4 * 1024 * 1024 * 1024) -> Optional[int]:
    """Seek-based BMFF size parser. Hops from box to box reading only
    16-byte headers, so it finds a trailing moov even on a 4GB video
    without reading the data in between."""
    _KNOWN = {b'ftyp', b'moov', b'mdat', b'free', b'skip', b'wide',
              b'pnot', b'uuid', b'moof', b'mfra', b'meta', b'udta',
              b'styp', b'sidx', b'ssix', b'prft', b'mdta'}
    try:
        rel = 0
        total = 0
        saw_mdat = False
        for _ in range(512):  # sane box-count bound
            if rel >= max_size:
                break
            fh.seek(abs_offset + rel)
            hdr = fh.read(16)
            if len(hdr) < 8:
                break
            box_size = struct.unpack('>I', hdr[0:4])[0]
            box_type = hdr[4:8]
            if box_type not in _KNOWN:
                break
            if box_size == 0:
                break  # "to end of file" — unknowable when carving
            elif box_size == 1:
                if len(hdr) < 16:
                    break
                box_size = struct.unpack('>Q', hdr[8:16])[0]
            if box_size < 8:
                break
            total = rel + box_size
            rel += box_size
            if box_type == b'mdat':
                saw_mdat = True
        if total > 0 and saw_mdat:
            return min(total, max_size)
        return min(total, max_size) if total > 0 else None
    except Exception:
        return None


def parse_jpeg_size(data: bytes, max_read: int = 30 * 1024 * 1024) -> Optional[int]:
    search_limit = min(len(data), max_read)
    pos = data.rfind(b'\xff\xd9', 0, search_limit)
    return pos + 2 if pos > 0 else None


def parse_png_size(data: bytes, max_read: int = 30 * 1024 * 1024) -> Optional[int]:
    pos = data.find(b'IEND', 8, min(len(data), max_read))
    return pos + 8 if pos > 0 else None


def parse_gif_size(data: bytes, max_read: int = 30 * 1024 * 1024) -> Optional[int]:
    pos = data.rfind(b'\x3b', 0, min(len(data), max_read))
    return pos + 1 if pos > 0 else None


def parse_pdf_size(data: bytes, max_read: int = 100 * 1024 * 1024) -> Optional[int]:
    pos = data.rfind(b'%%EOF', 0, min(len(data), max_read))
    return pos + 5 if pos > 0 else None


def parse_zip_size(data: bytes, max_read: int = 500 * 1024 * 1024) -> Optional[int]:
    pos = data.rfind(b'\x50\x4b\x05\x06', 0, min(len(data), max_read))
    if pos > 0:
        if pos + 22 <= len(data):
            comment_len = struct.unpack('<H', data[pos + 20:pos + 22])[0]
            return pos + 22 + comment_len
        return pos + 22
    return None


def parse_riff_size(data: bytes, max_read: int = 2 * 1024 * 1024 * 1024) -> Optional[int]:
    try:
        if len(data) < 12:
            return None
        size = struct.unpack('<I', data[4:8])[0]
        return min(size + 8, max_read)
    except Exception:
        return None


def parse_bmp_size(data: bytes, max_read: int = 50 * 1024 * 1024) -> Optional[int]:
    try:
        if len(data) < 6:
            return None
        size = struct.unpack('<I', data[2:6])[0]
        return min(size, max_read) if size >= 54 else None
    except Exception:
        return None


def parse_mp3_size(data: bytes, max_read: int = 50 * 1024 * 1024) -> Optional[int]:
    try:
        offset = 0
        if data[:3] == b'ID3' and len(data) > 10:
            tag_size = ((data[6] & 0x7f) << 21 | (data[7] & 0x7f) << 14 |
                       (data[8] & 0x7f) << 7 | (data[9] & 0x7f))
            offset = tag_size + 10
        frame_count = 0
        pos = offset
        limit = min(len(data), 256 * 1024)
        while pos < limit - 2:
            if data[pos] == 0xff and (data[pos + 1] & 0xe0) == 0xe0:
                frame_count += 1
                pos += 417
            else:
                pos += 1
        if frame_count > 10:
            return min(len(data), max_read)
        return None
    except Exception:
        return None


def parse_flac_size(data: bytes, max_read: int = 200 * 1024 * 1024) -> Optional[int]:
    try:
        if len(data) < 8:
            return None
        offset = 4
        while offset < min(len(data), 1024 * 1024):
            if offset + 4 > len(data):
                break
            block_header = data[offset]
            is_last = (block_header & 0x80) != 0
            block_size = struct.unpack('>I', b'\x00' + data[offset + 1:offset + 4])[0]
            offset += 4 + block_size
            if is_last:
                break
        return min(len(data), max_read)
    except Exception:
        return None


# ─── Extra Validation Checks ───────────────────────────────────────────────

def check_cr2(data: bytes) -> bool:
    if len(data) < 16 or data[0:2] not in (b'II', b'MM'):
        return False
    fmt = '<' if data[0:2] == b'II' else '>'
    return struct.unpack(fmt + 'H', data[2:4])[0] == 42 and data[8:10] == b'CR'


def check_cr3(data: bytes) -> bool:
    return len(data) >= 12 and data[4:8] == b'ftyp' and data[8:12] == b'crx '


def check_nef(data: bytes) -> bool:
    if len(data) < 16 or data[0:2] not in (b'II', b'MM') or data[8:10] == b'CR':
        return False
    fmt = '<' if data[0:2] == b'II' else '>'
    return struct.unpack(fmt + 'H', data[2:4])[0] == 42


def check_arw(data: bytes) -> bool:
    if len(data) < 16 or data[0:2] != b'II' or data[8:10] == b'CR':
        return False
    return struct.unpack('<H', data[2:4])[0] == 42


def check_dng(data: bytes) -> bool:
    if len(data) < 16 or data[0:2] not in (b'II', b'MM') or data[8:10] == b'CR':
        return False
    return True


def check_mov(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data[4:8] == b'ftyp':
        return data[8:12] in (b'qt  ', b'mqt ')
    return data[4:8] in (b'moov', b'wide', b'free', b'mdat', b'pnot')


def check_mp4(data: bytes) -> bool:
    if len(data) < 12 or data[4:8] != b'ftyp':
        return False
    return data[8:12] in [b'isom', b'mp41', b'mp42', b'M4V ', b'M4A ', b'f4v ',
                          b'dash', b'avc1', b'iso2', b'iso5', b'iso6', b'mp71']


def check_heif(data: bytes) -> bool:
    if len(data) < 12 or data[4:8] != b'ftyp':
        return False
    return data[8:12] in [b'heic', b'heix', b'mif1', b'heim', b'heis',
                          b'avci', b'hevc', b'hevx']


def check_tiff_generic(data: bytes) -> bool:
    if len(data) < 16 or data[0:2] not in (b'II', b'MM'):
        return False
    return data[8:10] != b'CR'


def check_webp(data: bytes) -> bool:
    return len(data) >= 12 and data[0:4] == b'RIFF' and data[8:12] == b'WEBP'


def check_avi(data: bytes) -> bool:
    return len(data) >= 12 and data[0:4] == b'RIFF' and data[8:12] == b'AVI '


def check_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[0:4] == b'RIFF' and data[8:12] == b'WAVE'


def check_mp3_id3(data: bytes) -> bool:
    return len(data) >= 3 and data[0:3] == b'ID3'


def check_mp3_sync(data: bytes) -> bool:
    if len(data) < 4 or data[0] != 0xff or (data[1] & 0xe0) != 0xe0:
        return False
    version = (data[1] >> 3) & 0x03
    layer = (data[1] >> 1) & 0x03
    bitrate_idx = (data[2] >> 4) & 0x0f
    return version != 1 and layer != 0 and bitrate_idx not in (0, 15)


def check_mkv(data: bytes) -> bool:
    return len(data) >= 4 and data[0:4] == b'\x1a\x45\xdf\xa3'


def check_psd(data: bytes) -> bool:
    return len(data) >= 4 and data[0:4] == b'8BPS'


# ─── Signature Database ────────────────────────────────────────────────────

SIGNATURES: List[FileSignature] = [
    # ══════════════════════ IMAGES ══════════════════════
    FileSignature(
        name="JPEG", extension="jpg", category="image",
        header=b'\xff\xd8\xff', footer=b'\xff\xd9',
        max_size=30 * 1024 * 1024, min_size=2 * 1024,
        parse_size=parse_jpeg_size, color="#f39c12", icon="🖼️",
    ),
    FileSignature(
        name="PNG", extension="png", category="image",
        header=b'\x89\x50\x4e\x47\x0d\x0a\x1a\x0a',
        max_size=30 * 1024 * 1024, min_size=100,
        parse_size=parse_png_size, color="#27ae60", icon="🖼️",
    ),
    FileSignature(
        name="HEIF / HEIC", extension="heif", category="image",
        header=b'\x00\x00\x00', extra_check=check_heif,
        max_size=80 * 1024 * 1024, min_size=10 * 1024,
        parse_size=parse_bmff_size, parse_size_fh=parse_bmff_size_fh, color="#e74c3c", icon="🖼️",
    ),
    FileSignature(
        name="WEBP", extension="webp", category="image",
        header=b'\x52\x49\x46\x46', extra_check=check_webp,
        max_size=30 * 1024 * 1024, min_size=100,
        parse_size=parse_riff_size, color="#2ecc71", icon="🖼️",
    ),
    FileSignature(
        name="GIF", extension="gif", category="image",
        header=b'\x47\x49\x46\x38',
        max_size=30 * 1024 * 1024, min_size=100,
        parse_size=parse_gif_size, color="#1abc9c", icon="🖼️",
    ),
    FileSignature(
        name="BMP", extension="bmp", category="image",
        header=b'\x42\x4d',
        max_size=50 * 1024 * 1024, min_size=54,
        parse_size=parse_bmp_size, color="#d35400", icon="🖼️",
    ),
    FileSignature(
        name="TIFF", extension="tiff", category="image",
        header=b'\x49\x49\x2a\x00', extra_check=check_tiff_generic,
        max_size=100 * 1024 * 1024, min_size=1024,
        parse_size=parse_tiff_size, color="#16a085", icon="🖼️",
    ),
    FileSignature(
        name="TIFF", extension="tiff", category="image",
        header=b'\x4d\x4d\x00\x2a',
        max_size=100 * 1024 * 1024, min_size=1024,
        color="#16a085", icon="🖼️",
    ),
    FileSignature(
        name="PSD", extension="psd", category="image",
        header=b'8BPS', extra_check=check_psd,
        max_size=500 * 1024 * 1024, min_size=1024,
        color="#31a8ff", icon="🎨",
    ),
    FileSignature(
        name="ICO", extension="ico", category="image",
        header=b'\x00\x00\x01\x00',
        max_size=1 * 1024 * 1024, min_size=100,
        color="#95a5a6", icon="🔷",
    ),

    # ══════════════════ CAMERA RAW ══════════════════════
    FileSignature(
        name="CR2 Raw", extension="cr2", category="image",
        header=b'\x49\x49\x2a\x00', extra_check=check_cr2,
        max_size=80 * 1024 * 1024, min_size=5 * 1024 * 1024,
        parse_size=parse_tiff_size, color="#e94560", icon="📷",
    ),
    FileSignature(
        name="CR3 Raw", extension="cr3", category="image",
        header=b'\x00\x00\x00', extra_check=check_cr3,
        max_size=120 * 1024 * 1024, min_size=5 * 1024 * 1024,
        parse_size=parse_bmff_size, parse_size_fh=parse_bmff_size_fh, color="#ff6b6b", icon="📷",
    ),
    FileSignature(
        name="NEF Raw", extension="nef", category="image",
        header=b'\x4d\x4d\x00\x2a', extra_check=check_nef,
        max_size=80 * 1024 * 1024, min_size=5 * 1024 * 1024,
        parse_size=parse_tiff_size, color="#ffd700", icon="📷",
    ),
    FileSignature(
        name="ARW Raw", extension="arw", category="image",
        header=b'\x49\x49\x2a\x00', extra_check=check_arw,
        max_size=80 * 1024 * 1024, min_size=5 * 1024 * 1024,
        parse_size=parse_tiff_size, color="#ff8c00", icon="📷",
    ),
    FileSignature(
        name="DNG Raw", extension="dng", category="image",
        header=b'\x49\x49\x2a\x00', extra_check=check_dng,
        max_size=100 * 1024 * 1024, min_size=1 * 1024 * 1024,
        parse_size=parse_tiff_size, color="#ff4500", icon="📷",
    ),
    FileSignature(
        name="ORF Raw (Olympus)", extension="orf", category="image",
        header=b'IIRO', max_size=80 * 1024 * 1024, min_size=100 * 1024,
        color="#16a085", icon="📷",
    ),
    FileSignature(
        name="ORF Raw (Olympus)", extension="orf", category="image",
        header=b'IIRS', max_size=80 * 1024 * 1024, min_size=100 * 1024,
        color="#16a085", icon="📷",
    ),
    FileSignature(
        name="RW2 Raw (Panasonic)", extension="rw2", category="image",
        header=b'II\x55\x00\x18\x00\x00\x00', max_size=80 * 1024 * 1024,
        min_size=100 * 1024, color="#8e44ad", icon="📷",
    ),
    FileSignature(
        name="RAF Raw (Fujifilm)", extension="raf", category="image",
        header=b'FUJIFILMCCD-RAW', max_size=120 * 1024 * 1024,
        min_size=100 * 1024, color="#27ae60", icon="📷",
    ),

    # ══════════════════════ VIDEO ══════════════════════
    FileSignature(
        name="MP4", extension="mp4", category="video",
        header=b'\x00\x00\x00', extra_check=check_mp4,
        max_size=4 * 1024 * 1024 * 1024, min_size=10 * 1024,
        parse_size=parse_bmff_size, parse_size_fh=parse_bmff_size_fh, color="#8e44ad", icon="🎥",
    ),
    FileSignature(
        name="MOV", extension="mov", category="video",
        header=b'\x00\x00\x00', extra_check=check_mov,
        max_size=4 * 1024 * 1024 * 1024, min_size=10 * 1024,
        parse_size=parse_bmff_size, parse_size_fh=parse_bmff_size_fh, color="#9b59b6", icon="🎬",
    ),
    FileSignature(
        name="AVI", extension="avi", category="video",
        header=b'\x52\x49\x46\x46', extra_check=check_avi,
        max_size=4 * 1024 * 1024 * 1024, min_size=1024,
        parse_size=parse_riff_size, color="#3498db", icon="🎬",
    ),
    FileSignature(
        name="MKV / WebM", extension="mkv", category="video",
        header=b'\x1a\x45\xdf\xa3', extra_check=check_mkv,
        max_size=4 * 1024 * 1024 * 1024, min_size=1024,
        color="#2c3e50", icon="🎬",
    ),
    FileSignature(
        name="FLV", extension="flv", category="video",
        header=b'\x46\x4c\x56\x01',
        max_size=2 * 1024 * 1024 * 1024, min_size=1024,
        color="#c0392b", icon="🎬",
    ),
    FileSignature(
        name="3GP", extension="3gp", category="video",
        header=b'\x00\x00\x00',
        extra_check=lambda d: len(d) >= 12 and d[4:8] == b'ftyp' and d[8:12] in (b'3gp4', b'3gp5', b'3gp6', b'3ge6', b'3gg6'),
        max_size=2 * 1024 * 1024 * 1024, min_size=1024,
        parse_size=parse_bmff_size, parse_size_fh=parse_bmff_size_fh, color="#7f8c8d", icon="📱",
    ),
    FileSignature(
        name="WMV / ASF", extension="wmv", category="video",
        header=b'\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c',
        max_size=2 * 1024 * 1024 * 1024, min_size=64 * 1024,
        color="#2980b9", icon="🎬",
    ),
    FileSignature(
        name="MPEG Video", extension="mpg", category="video",
        header=b'\x00\x00\x01\xba', max_size=2 * 1024 * 1024 * 1024,
        min_size=64 * 1024, color="#c0392b", icon="🎬",
    ),

    # ══════════════════════ AUDIO ══════════════════════
    FileSignature(
        name="MP3", extension="mp3", category="audio",
        header=b'\x49\x44\x33', extra_check=check_mp3_id3,
        max_size=50 * 1024 * 1024, min_size=10 * 1024,
        parse_size=parse_mp3_size, color="#1db954", icon="🎵",
    ),
    FileSignature(
        name="MP3", extension="mp3", category="audio",
        header=b'\xff\xfb', extra_check=check_mp3_sync,
        max_size=50 * 1024 * 1024, min_size=10 * 1024,
        parse_size=parse_mp3_size, color="#1db954", icon="🎵",
    ),
    FileSignature(
        name="WAV", extension="wav", category="audio",
        header=b'\x52\x49\x46\x46', extra_check=check_wav,
        max_size=2 * 1024 * 1024 * 1024, min_size=1024,
        parse_size=parse_riff_size, color="#e67e22", icon="🎵",
    ),
    FileSignature(
        name="FLAC", extension="flac", category="audio",
        header=b'\x66\x4c\x61\x43',
        max_size=200 * 1024 * 1024, min_size=1024,
        parse_size=parse_flac_size, color="#ff6347", icon="🎵",
    ),
    FileSignature(
        name="AAC", extension="aac", category="audio",
        header=b'\xff\xf1',
        max_size=50 * 1024 * 1024, min_size=1024,
        color="#9b59b6", icon="🎵",
    ),
    FileSignature(
        name="OGG / Vorbis", extension="ogg", category="audio",
        header=b'\x4f\x67\x67\x53',
        max_size=200 * 1024 * 1024, min_size=1024,
        color="#f1c40f", icon="🎵",
    ),
    FileSignature(
        name="M4A", extension="m4a", category="audio",
        header=b'\x00\x00\x00',
        extra_check=lambda d: len(d) >= 12 and d[4:8] == b'ftyp' and d[8:12] in (b'M4A ', b'M4B ', b'mp42'),
        max_size=200 * 1024 * 1024, min_size=1024,
        parse_size=parse_bmff_size, parse_size_fh=parse_bmff_size_fh, color="#e91e63", icon="🎵",
    ),
    FileSignature(
        name="AIFF", extension="aiff", category="audio",
        header=b'\x46\x4f\x52\x4d',
        extra_check=lambda d: len(d) >= 12 and d[8:12] in (b'AIFF', b'AIFC'),
        max_size=2 * 1024 * 1024 * 1024, min_size=1024,
        color="#00bcd4", icon="🎵",
    ),

    # ═══════════════════ DOCUMENTS ═════════════════════
    FileSignature(
        name="PDF", extension="pdf", category="document",
        header=b'\x25\x50\x44\x46',
        max_size=200 * 1024 * 1024, min_size=100,
        parse_size=parse_pdf_size, color="#c0392b", icon="📑",
    ),
    FileSignature(
        name="ZIP / DOCX / XLSX", extension="zip", category="document",
        header=b'\x50\x4b\x03\x04',
        max_size=500 * 1024 * 1024, min_size=100,
        parse_size=parse_zip_size, color="#2980b9", icon="📦",
    ),
    FileSignature(
        name="RTF", extension="rtf", category="document",
        header=b'\\{\\rtf',
        max_size=50 * 1024 * 1024, min_size=100,
        color="#6c5ce7", icon="📝",
    ),
    FileSignature(
        name="Word / Excel (older .doc/.xls)", extension="doc",
        category="document",
        header=b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',
        max_size=100 * 1024 * 1024, min_size=4 * 1024,
        color="#2b579a", icon="📄",
    ),

    # ═══════════════════ ARCHIVES ══════════════════════
    FileSignature(
        name="RAR", extension="rar", category="archive",
        header=b'\x52\x61\x72\x21\x1a\x07\x00',
        max_size=2 * 1024 * 1024 * 1024, min_size=1024,
        color="#e17055", icon="📦",
    ),
    FileSignature(
        name="RAR5", extension="rar", category="archive",
        header=b'\x52\x61\x72\x21\x1a\x07\x01\x00',
        max_size=2 * 1024 * 1024 * 1024, min_size=1024,
        color="#e17055", icon="📦",
    ),
    FileSignature(
        name="7-Zip", extension="7z", category="archive",
        header=b'\x37\x7a\xbc\xaf\x27\x1c',
        max_size=2 * 1024 * 1024 * 1024, min_size=1024,
        color="#fdcb6e", icon="📦",
    ),
    FileSignature(
        name="GZIP", extension="gz", category="archive",
        header=b'\x1f\x8b\x08',
        max_size=500 * 1024 * 1024, min_size=100,
        color="#00b894", icon="📦",
    ),
]


# ─── Lookup Helpers ─────────────────────────────────────────────────────────

def get_signature_by_extension(ext: str) -> Optional[FileSignature]:
    ext = ext.lower().lstrip('.')
    for sig in SIGNATURES:
        if sig.extension == ext:
            return sig
    return None


def get_all_extensions() -> list:
    seen = set()
    result = []
    for sig in SIGNATURES:
        if sig.extension not in seen:
            seen.add(sig.extension)
            result.append(sig.extension)
    return result


def get_signatures_for_types(types: list) -> List[FileSignature]:
    if not types:
        return SIGNATURES
    type_set = {t.lower().lstrip('.') for t in types}
    return [s for s in SIGNATURES if s.extension in type_set]


def get_signature_info() -> list:
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
