"""
SlugRecover — File Extraction and Saving Logic
Handles reading carved data from source, saving to disk, and thumbnail generation.
"""

import os
import io
import hashlib
from typing import Optional, List, Tuple
from scanner import FileCarver, CarvedFile


def recover_file(carver: FileCarver, carved_file: CarvedFile,
                 output_dir: str, index: int) -> Optional[str]:
    """
    Extract a carved file from the source and save it to disk.
    Returns the output file path on success, None on failure.
    """
    source_path = carver._source_path
    if not source_path:
        return None

    # Create type-specific subdirectory
    type_dir = os.path.join(output_dir, carved_file.signature.extension.upper())
    os.makedirs(type_dir, exist_ok=True)

    # Generate filename
    filename = f"recovered_{index:04d}.{carved_file.signature.extension}"
    output_path = os.path.join(type_dir, filename)

    try:
        # Read data from source
        with open(source_path, 'rb') as f:
            f.seek(carved_file.offset)
            data = f.read(carved_file.size)

        if not data or len(data) < carved_file.signature.min_size:
            carved_file.valid = False
            return None

        # Validate the data
        if not _validate_carved_data(data, carved_file):
            carved_file.valid = False
            # Still save it but mark as potentially invalid

        # Write to output
        with open(output_path, 'wb') as f:
            f.write(data)

        carved_file.recovered = True
        carved_file.recovery_path = output_path

        # Generate thumbnail for images
        if carved_file.signature.category == 'image':
            thumb_path = _generate_thumbnail(data, output_path, type_dir, index,
                                              carved_file.signature.extension)
            if thumb_path:
                carved_file.thumbnail_path = thumb_path

        return output_path

    except Exception as e:
        carved_file.valid = False
        return None


def recover_files(carver: FileCarver, file_ids: List[str],
                  output_dir: str) -> List[dict]:
    """
    Recover multiple files. Returns list of results.
    """
    os.makedirs(output_dir, exist_ok=True)
    results = []

    # Count existing files per type for numbering
    type_counts = {}

    for file_id in file_ids:
        carved = carver.get_carved_file(file_id)
        if not carved:
            results.append({
                'id': file_id,
                'success': False,
                'error': 'File not found in scan results',
            })
            continue

        ext = carved.signature.extension
        type_counts[ext] = type_counts.get(ext, 0) + 1
        index = type_counts[ext]

        path = recover_file(carver, carved, output_dir, index)
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


def _validate_carved_data(data: bytes, carved_file: CarvedFile) -> bool:
    """Basic validation of carved file data."""
    sig = carved_file.signature

    # Check header is still present
    if data[:len(sig.header)] != sig.header:
        return False

    # Run extra check if available
    if sig.extra_check:
        if not sig.extra_check(data[:min(len(data), 64)]):
            return False

    # Check footer if defined
    if sig.footer:
        # Footer should be near the end
        search_end = data[-min(len(data), 4096):]
        if sig.footer not in search_end:
            return False

    return True


def _generate_thumbnail(data: bytes, file_path: str, type_dir: str,
                         index: int, extension: str) -> Optional[str]:
    """Generate a thumbnail for an image file. Returns thumbnail path."""
    try:
        from PIL import Image

        thumb_dir = os.path.join(type_dir, 'thumbnails')
        os.makedirs(thumb_dir, exist_ok=True)
        thumb_path = os.path.join(thumb_dir, f"thumb_{index:04d}.jpg")

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
