"""
SlugRecover — Pre-Recovery Preview Engine

Generates image previews directly from the scan source (drive, card, or
disk image) BEFORE any file is recovered, so you can check a photo is
the right one — and intact — before saving it.

How it works:
- Reads a bounded window of bytes at the carved file's offset
  (read-only; the source is never written to).
- Standard images are decoded directly (Pillow, tolerant of truncation).
- Camera RAW formats (CR2/CR3/NEF/ARW/DNG/ORF/RW2/RAF/...) almost always
  embed a full JPEG preview; we locate the largest embedded JPEG stream
  and decode that. This works generically across RAW brands.
- Results are cached on disk per (source, offset) and served instantly
  on subsequent requests. Cache writes are atomic.
"""

import io
import os
import hashlib
import tempfile
import threading
from typing import Optional

# How much of the source we read to build a preview.
# 24MB covers full embedded previews in modern RAW files and most JPEGs.
_PREVIEW_READ_BYTES = 24 * 1024 * 1024

# Thumbnail (grid) and preview (modal) long-edge sizes
THUMB_SIZE = 320
PREVIEW_SIZE = 1600

# Formats Pillow can open directly
_DIRECT_DECODE = {'jpg', 'png', 'gif', 'bmp', 'webp', 'tiff', 'ico', 'psd'}
# RAW formats where we hunt for the embedded JPEG preview
_EMBEDDED_JPEG = {'cr2', 'cr3', 'nef', 'arw', 'dng', 'orf', 'rw2', 'raf',
                  'srw', 'pef', 'x3f'}

_cache_dir: Optional[str] = None
_cache_lock = threading.Lock()


def get_cache_dir() -> str:
    """Temp directory for preview cache (survives for the app's lifetime)."""
    global _cache_dir
    with _cache_lock:
        if _cache_dir is None or not os.path.isdir(_cache_dir):
            _cache_dir = tempfile.mkdtemp(prefix='slugrecover_previews_')
        return _cache_dir


def clear_cache():
    """Drop all cached previews (called when a new scan starts)."""
    global _cache_dir
    with _cache_lock:
        d = _cache_dir
        _cache_dir = None
    if d and os.path.isdir(d):
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def _cache_path(source_path: str, offset: int, size: int) -> str:
    key = hashlib.sha1(
        f"{source_path}|{offset}|{size}".encode()).hexdigest()[:24]
    return os.path.join(get_cache_dir(), f"{key}_{size}.jpg")


def _read_window(source_path: str, offset: int, length: int) -> bytes:
    """Read a bounded window from the source. Read-only, own fd."""
    with open(source_path, 'rb') as f:
        f.seek(offset)
        return f.read(length)


def _find_embedded_jpeg(data: bytes) -> Optional[bytes]:
    """
    Locate the largest embedded JPEG stream in a RAW file's bytes.
    Virtually every camera RAW embeds at least one JPEG preview; the
    largest one is usually the full-size preview.
    """
    best: Optional[bytes] = None
    pos = 0
    n = len(data)
    while pos < n:
        start = data.find(b'\xff\xd8\xff', pos)
        if start < 0:
            break
        end = data.find(b'\xff\xd9', start + 3)
        if end < 0:
            break
        candidate = data[start:end + 2]
        # Skip garbage fragments; "largest wins" below already prefers
        # the full-size preview over tiny EXIF thumbnails
        if len(candidate) >= 1024 and (best is None or len(candidate) > len(best)):
            best = candidate
        pos = end + 2
    return best


def _decode_image(data: bytes, extension: str):
    """Decode carved bytes into a PIL Image, or None."""
    from PIL import Image, ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True  # carved data is often truncated

    if extension in _DIRECT_DECODE:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            return img
        except Exception:
            # A "direct" format can still contain an embedded JPEG
            # (e.g. TIFF-based files); fall through and hunt for it.
            pass

    if extension in _EMBEDDED_JPEG or extension in _DIRECT_DECODE:
        jpeg = _find_embedded_jpeg(data)
        if jpeg:
            try:
                img = Image.open(io.BytesIO(jpeg))
                img.load()
                return img
            except Exception:
                return None

    if extension in ('heif', 'heic'):
        try:
            import pillow_heif
            heif_file = pillow_heif.read_heif(data)
            return Image.frombytes(heif_file.mode, heif_file.size,
                                   heif_file.data)
        except Exception:
            return None

    return None


def generate_preview(source_path: str, offset: int, file_size: int,
                     extension: str, size: int = THUMB_SIZE) -> Optional[str]:
    """
    Build (or fetch from cache) a JPEG preview for a carved file, reading
    directly from the scan source. Returns the preview path, or None if
    the file can't be decoded (which usually means it's damaged).
    """
    cache = _cache_path(source_path, offset, size)
    if os.path.isfile(cache):
        return cache
    # Negative cache: remember undecodable files so the UI doesn't
    # hammer the drive retrying them
    neg = cache + '.none'
    if os.path.isfile(neg):
        return None

    try:
        window = _read_window(source_path, offset,
                              min(file_size, _PREVIEW_READ_BYTES))
    except (OSError, PermissionError):
        return None

    img = _decode_image(window, extension)
    if img is None:
        try:
            open(neg, 'wb').close()
        except OSError:
            pass
        return None

    try:
        from PIL import Image
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        # Atomic cache write
        tmp = cache + '.tmp'
        img.save(tmp, 'JPEG', quality=85)
        os.replace(tmp, cache)
        return cache
    except Exception:
        return None
