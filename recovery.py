"""
SlugRecover — File Extraction and Saving Logic
Handles reading carved data from source, saving to disk, and thumbnail generation.
"""

import os
import io
import hashlib
from typing import Optional, List, Tuple
from scanner import FileCarver, CarvedFile


_COPY_CHUNK = 4 * 1024 * 1024  # 4MB streaming copy chunks


def recover_file(carver: FileCarver, carved_file: CarvedFile,
                 output_dir: str, index: int = 0) -> Optional[str]:
    """
    Extract a carved file from the source and save it to disk.
    Returns the output file path on success, None on failure.

    Data safety:
    - Streams in 4MB chunks (never loads a whole file into RAM — the old
      code read entire multi-GB videos into memory at once).
    - Writes to a temp file and os.replace()s into place, so a crash
      can never leave a partial file that looks recovered.
    - Filenames are keyed on the source byte offset, so they are stable
      and unique: recovering in several batches can never overwrite
      files from an earlier batch (the old per-call index counter did).
    """
    source_path = carver._source_path
    if not source_path:
        return None

    # Create type-specific subdirectory
    type_dir = os.path.join(output_dir, carved_file.signature.extension.upper())
    os.makedirs(type_dir, exist_ok=True)

    # Offset-keyed filename: stable, unique, cross-batch safe
    filename = (f"recovered_0x{carved_file.offset:012X}"
                f".{carved_file.signature.extension}")
    output_path = os.path.join(type_dir, filename)

    tmp_path = output_path + '.slugpart'
    try:
        head = b''
        with open(source_path, 'rb') as src, open(tmp_path, 'wb') as out:
            src.seek(carved_file.offset)
            remaining = carved_file.size
            total_written = 0
            while remaining > 0:
                chunk = src.read(min(_COPY_CHUNK, remaining))
                if not chunk:
                    break
                if total_written == 0:
                    head = chunk[:65536]
                out.write(chunk)
                total_written += len(chunk)
                remaining -= len(chunk)

        if total_written < carved_file.signature.min_size:
            carved_file.valid = False
            os.unlink(tmp_path)
            return None

        # Validate header/footer before finalizing
        tail = b''
        if carved_file.signature.footer:
            with open(tmp_path, 'rb') as f:
                f.seek(max(0, total_written - 4096))
                tail = f.read()
        if not _validate_carved_data(head, carved_file, tail=tail):
            carved_file.valid = False
            # Still keep it — partially damaged files are often worth
            # having in recovery scenarios — but mark as questionable.

        os.replace(tmp_path, output_path)

        carved_file.recovered = True
        carved_file.recovery_path = output_path

        # Generate thumbnail for images (from the recovered file)
        if carved_file.signature.category == 'image':
            try:
                with open(output_path, 'rb') as f:
                    data = f.read(32 * 1024 * 1024)
                thumb_path = _generate_thumbnail(
                    data, output_path, type_dir, carved_file.offset,
                    carved_file.signature.extension)
                if thumb_path:
                    carved_file.thumbnail_path = thumb_path
            except Exception:
                pass

        return output_path

    except Exception:
        carved_file.valid = False
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        return None


def recover_files(carver: FileCarver, file_ids: List[str],
                  output_dir: str) -> List[dict]:
    """
    Recover multiple files. Returns list of results.
    """
    os.makedirs(output_dir, exist_ok=True)
    results = []

    for file_id in file_ids:
        carved = carver.get_carved_file(file_id)
        if not carved:
            results.append({
                'id': file_id,
                'success': False,
                'error': 'File not found in scan results',
            })
            continue

        path = recover_file(carver, carved, output_dir)
        results.append({
            'id': file_id,
            'success': path is not None,
            'path': path,
            'type': carved.signature.name,
            'size': carved.size,
            'valid': carved.valid,
        })

    return results


def recover_all(carver: FileCarver, output_dir: str) -> List[dict]:
    """Recover all carved files."""
    file_ids = [f"{c.signature.extension}_{c.offset}" for c in carver.carved_files]
    return recover_files(carver, file_ids, output_dir)


def _validate_carved_data(head: bytes, carved_file: CarvedFile,
                          tail: bytes = b'') -> bool:
    """Basic validation of carved file data (header + optional footer)."""
    sig = carved_file.signature

    # Check header is still present
    if head[:len(sig.header)] != sig.header:
        return False

    # Run extra check if available
    if sig.extra_check:
        if not sig.extra_check(head[:min(len(head), 64)]):
            return False

    # Check footer if defined
    if sig.footer and tail:
        if sig.footer not in tail:
            return False

    return True


def _generate_thumbnail(data: bytes, file_path: str, type_dir: str,
                         index: int, extension: str) -> Optional[str]:
    """Generate a thumbnail for an image file. Returns thumbnail path."""
    try:
        from PIL import Image

        thumb_dir = os.path.join(type_dir, 'thumbnails')
        os.makedirs(thumb_dir, exist_ok=True)
        thumb_path = os.path.join(thumb_dir, f"thumb_0x{index:012X}.jpg")

        # Try to open the image
        if extension in ('jpg', 'png', 'bmp', 'gif', 'webp', 'tiff'):
            img = Image.open(io.BytesIO(data))
        elif extension == 'cr2':
            # Try to extract embedded JPEG preview from CR2
            # CR2 files typically have a JPEG preview in IFD1
            jpeg_start = data.find(b'\xff\xd8\xff', 1)  # Skip if at pos 0
            if jpeg_start > 0:
                jpeg_end = data.find(b'\xff\xd9', jpeg_start)
                if jpeg_end > 0:
                    img = Image.open(io.BytesIO(data[jpeg_start:jpeg_end + 2]))
                else:
                    return None
            else:
                return None
        elif extension == 'heif':
            try:
                import pillow_heif
                heif_file = pillow_heif.read_heif(data)
                img = Image.frombytes(heif_file.mode, heif_file.size, heif_file.data)
            except ImportError:
                return None
        else:
            return None

        # Create thumbnail
        img.thumbnail((300, 300), Image.Resampling.LANCZOS)
        img = img.convert('RGB')
        img.save(thumb_path, 'JPEG', quality=80)
        return thumb_path

    except Exception:
        return None


def get_recovery_stats(output_dir: str) -> dict:
    """Get statistics about recovered files in output directory."""
    stats = {
        'total_files': 0,
        'total_size': 0,
        'by_type': {},
    }

    if not os.path.exists(output_dir):
        return stats

    for type_dir in os.listdir(output_dir):
        type_path = os.path.join(output_dir, type_dir)
        if not os.path.isdir(type_path) or type_dir == 'thumbnails':
            continue

        count = 0
        size = 0
        for f in os.listdir(type_path):
            if f == 'thumbnails' or os.path.isdir(os.path.join(type_path, f)):
                continue
            count += 1
            size += os.path.getsize(os.path.join(type_path, f))

        if count > 0:
            stats['by_type'][type_dir] = {'count': count, 'size': size}
            stats['total_files'] += count
            stats['total_size'] += size

    return stats
