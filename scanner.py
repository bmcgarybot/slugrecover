"""
SlugRecover — Core File Carving Engine
Scans raw disks/drives/disk images for deleted files using byte signature scanning.
"""

import os
import time
import threading
import platform
import struct
import subprocess
from typing import Optional, List, Dict
from dataclasses import dataclass, field

from signatures import FileSignature, SIGNATURES, get_signatures_for_types


@dataclass
class CarvedFile:
    """Represents a file found during scanning."""
    signature: FileSignature
    offset: int
    size: int
    data: Optional[bytes] = None
    recovered: bool = False
    recovery_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    valid: bool = True

    @property
    def size_human(self) -> str:
        if self.size < 1024:
            return f"{self.size} B"
        elif self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        elif self.size < 1024 * 1024 * 1024:
            return f"{self.size / (1024 * 1024):.1f} MB"
        else:
            return f"{self.size / (1024 * 1024 * 1024):.2f} GB"

    def to_dict(self) -> dict:
        return {
            'id': f"{self.signature.extension}_{self.offset}",
            'type': self.signature.name,
            'extension': self.signature.extension,
            'category': self.signature.category,
            'offset': self.offset,
            'offset_hex': f"0x{self.offset:X}",
            'size': self.size,
            'size_human': self.size_human,
            'color': self.signature.color,
            'icon': self.signature.icon,
            'recovered': self.recovered,
            'recovery_path': self.recovery_path,
            'thumbnail': self.thumbnail_path,
            'valid': self.valid,
        }


@dataclass
class ScanProgress:
    """Tracks scanning progress."""
    total_bytes: int = 0
    scanned_bytes: int = 0
    files_found: Dict[str, int] = field(default_factory=dict)
    total_files: int = 0
    status: str = "idle"  # idle, scanning, paused, complete, cancelled, error
    start_time: float = 0.0
    elapsed: float = 0.0
    error: Optional[str] = None
    current_offset: int = 0

    @property
    def percent(self) -> float:
        if self.total_bytes == 0:
            return 0
        return min(100.0, (self.scanned_bytes / self.total_bytes) * 100)

    @property
    def eta_seconds(self) -> Optional[float]:
        if self.elapsed <= 0 or self.scanned_bytes <= 0:
            return None
        rate = self.scanned_bytes / self.elapsed
        remaining = self.total_bytes - self.scanned_bytes
        return remaining / rate if rate > 0 else None

    @property
    def speed_human(self) -> str:
        if self.elapsed <= 0:
            return "—"
        rate = self.scanned_bytes / self.elapsed
        if rate < 1024:
            return f"{rate:.0f} B/s"
        elif rate < 1024 * 1024:
            return f"{rate / 1024:.1f} KB/s"
        else:
            return f"{rate / (1024 * 1024):.1f} MB/s"

    def to_dict(self) -> dict:
        eta = self.eta_seconds
        return {
            'total_bytes': self.total_bytes,
            'scanned_bytes': self.scanned_bytes,
            'percent': round(self.percent, 2),
            'files_found': dict(self.files_found),
            'total_files': self.total_files,
            'status': self.status,
            'elapsed': round(self.elapsed, 1),
            'eta': round(eta, 1) if eta else None,
            'speed': self.speed_human,
            'error': self.error,
            'current_offset': self.current_offset,
        }


class FileCarver:
    """Core file carving engine — scans raw data sources for file signatures."""

    def __init__(self, chunk_size: int = 512):
        self.chunk_size = chunk_size
        self.read_buffer_size = 4 * 1024 * 1024  # 4MB
        self.progress = ScanProgress()
        self.carved_files: List[CarvedFile] = []
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._scan_thread: Optional[threading.Thread] = None
        self._source_path: Optional[str] = None
        self._signatures: List[FileSignature] = []

    # ─── Device Size Detection ───────────────────────────────────────────

    @staticmethod
    def _get_device_size(path: str) -> Optional[int]:
        """
        Get the size of a block device, handling platform quirks.

        - Linux:  seek(0, 2) works, or use ioctl BLKGETSIZE64
        - macOS:  seek(0, 2) returns 0 on raw devices — use diskutil
        - Windows: seek(0, 2) works on \\\\.\\PhysicalDriveN
        """
        system = platform.system()

        # Try seek first (works on Linux, Windows, and disk images)
        try:
            with open(path, 'rb') as f:
                f.seek(0, 2)
                size = f.tell()
                if size > 0:
                    return size
        except Exception:
            pass

        # macOS: seek returns 0 for raw devices — use diskutil or ioctl
        if system == 'Darwin':
            size = FileCarver._get_macos_device_size(path)
            if size and size > 0:
                return size

        # Linux: try ioctl BLKGETSIZE64
        if system == 'Linux':
            size = FileCarver._get_linux_device_size(path)
            if size and size > 0:
                return size

        return None

    @staticmethod
    def _get_macos_device_size(path: str) -> Optional[int]:
        """Get device size on macOS using diskutil or ioctl."""
        import re

        # Normalize path: /dev/rdiskN -> diskN for diskutil
        dev_name = os.path.basename(path)
        if dev_name.startswith('r'):
            dev_name = dev_name[1:]  # rdisk2 -> disk2

        # Try diskutil info
        try:
            result = subprocess.run(
                ['diskutil', 'info', dev_name],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    # Look for "Disk Size:" or "Total Size:"
                    if 'Disk Size:' in line or 'Total Size:' in line:
                        # Extract byte count from parenthesized value
                        # e.g. "Disk Size:  31.9 GB (31914983424 Bytes)"
                        match = re.search(r'\((\d+)\s*[Bb]ytes?\)', line)
                        if match:
                            return int(match.group(1))
                    # Also check "Partition Size:"
                    if 'Partition Size:' in line:
                        match = re.search(r'\((\d+)\s*[Bb]ytes?\)', line)
                        if match:
                            return int(match.group(1))
        except Exception:
            pass

        # Try ioctl DKIOCGETBLOCKCOUNT + DKIOCGETBLOCKSIZE
        try:
            import fcntl
            DKIOCGETBLOCKSIZE = 0x40046418
            DKIOCGETBLOCKCOUNT = 0x40086419

            with open(path, 'rb') as f:
                fd = f.fileno()
                # Get block size (usually 512)
                buf = bytearray(4)
                fcntl.ioctl(fd, DKIOCGETBLOCKSIZE, buf)
                block_size = struct.unpack('I', buf)[0]

                # Get block count
                buf = bytearray(8)
                fcntl.ioctl(fd, DKIOCGETBLOCKCOUNT, buf)
                block_count = struct.unpack('Q', buf)[0]

                size = block_size * block_count
                if size > 0:
                    return size
        except Exception:
            pass

        return None

    @staticmethod
    def _get_linux_device_size(path: str) -> Optional[int]:
        """Get device size on Linux using ioctl BLKGETSIZE64."""
        try:
            import fcntl
            BLKGETSIZE64 = 0x80081272

            with open(path, 'rb') as f:
                buf = bytearray(8)
                fcntl.ioctl(f.fileno(), BLKGETSIZE64, buf)
                return struct.unpack('Q', buf)[0]
        except Exception:
            pass
        return None

    # ─── Drive Listing ───────────────────────────────────────────────────

    def list_drives(self) -> List[dict]:
        """List available drives/partitions on the system."""
        drives = []
        system = platform.system()

        if system == 'Linux':
            try:
                if os.path.exists('/proc/partitions'):
                    with open('/proc/partitions', 'r') as f:
                        lines = f.readlines()[2:]
                    for line in lines:
                        parts = line.strip().split()
                        if len(parts) >= 4:
                            name = parts[3]
                            size_blocks = int(parts[2]) * 1024
                            path = f"/dev/{name}"
                            drives.append({
                                'path': path,
                                'name': name,
                                'size': size_blocks,
                                'size_human': self._human_size(size_blocks),
                                'type': 'partition' if any(c.isdigit() for c in name) else 'disk',
                            })
            except Exception:
                pass

        elif system == 'Darwin':
            # Use diskutil list to get real disk info
            try:
                result = subprocess.run(
                    ['diskutil', 'list', '-plist'],
                    capture_output=True, timeout=10
                )
                # Fallback: just list /dev/disk* with sizes
                for name in sorted(os.listdir('/dev')):
                    if not name.startswith('disk'):
                        continue
                    # Skip partition-less character devices (rdisk*)
                    if name.startswith('rdisk'):
                        continue
                    path = f"/dev/{name}"
                    size = self._get_device_size(path)
                    # Skip tiny/zero entries (they're usually control nodes)
                    if size and size > 0:
                        drives.append({
                            'path': path,
                            'name': name,
                            'size': size,
                            'size_human': self._human_size(size),
                            'type': 'partition' if 's' in name[4:] else 'disk',
                        })
            except Exception:
                # Bare fallback
                for name in sorted(os.listdir('/dev')):
                    if name.startswith('disk') and not name.startswith('rdisk'):
                        drives.append({
                            'path': f"/dev/{name}",
                            'name': name,
                            'size': 0,
                            'size_human': 'Unknown',
                            'type': 'disk',
                        })

        elif system == 'Windows':
            for i in range(16):
                path = f"\\\\.\\PhysicalDrive{i}"
                try:
                    size = self._get_device_size(path)
                    if size and size > 0:
                        drives.append({
                            'path': path,
                            'name': f"PhysicalDrive{i}",
                            'size': size,
                            'size_human': self._human_size(size),
                            'type': 'disk',
                        })
                except Exception:
                    continue

        return drives

    # ─── Scan Control ────────────────────────────────────────────────────

    def start_scan(self, source_path: str, file_types: Optional[List[str]] = None,
                   chunk_size: Optional[int] = None) -> bool:
        """Start scanning a source in a background thread."""
        if self.progress.status == 'scanning':
            return False

        if chunk_size:
            self.chunk_size = chunk_size

        self._source_path = source_path
        self._signatures = get_signatures_for_types(file_types) if file_types else SIGNATURES
        self.carved_files = []
        self._cancel_event.clear()
        self._pause_event.set()

        # Get source size — platform-aware
        try:
            if os.path.isfile(source_path):
                total_size = os.path.getsize(source_path)
            else:
                total_size = self._get_device_size(source_path)
        except Exception as e:
            self.progress = ScanProgress(status='error', error=str(e))
            return False

        # Catch the 0-byte lie: if size detection failed, don't fake a scan
        if not total_size or total_size <= 0:
            self.progress = ScanProgress(
                status='error',
                error=(
                    f'Could not determine size of "{source_path}". '
                    f'Make sure the path is correct and you have permission to read it. '
                    f'On macOS, try the raw device (e.g. /dev/rdisk2) or run with sudo.'
                )
            )
            return False

        self.progress = ScanProgress(
            total_bytes=total_size,
            status='scanning',
            start_time=time.time(),
        )

        self._scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self._scan_thread.start()
        return True

    def pause_scan(self):
        if self.progress.status == 'scanning':
            self._pause_event.clear()
            self.progress.status = 'paused'

    def resume_scan(self):
        if self.progress.status == 'paused':
            self._pause_event.set()
            self.progress.status = 'scanning'

    def cancel_scan(self):
        self._cancel_event.set()
        self._pause_event.set()
        self.progress.status = 'cancelled'

    def get_progress(self) -> dict:
        return self.progress.to_dict()

    def get_results(self) -> List[dict]:
        with self._lock:
            return [f.to_dict() for f in self.carved_files]

    def get_carved_file(self, file_id: str) -> Optional[CarvedFile]:
        with self._lock:
            for f in self.carved_files:
                if f"{f.signature.extension}_{f.offset}" == file_id:
                    return f
        return None

    # ─── Scan Worker ─────────────────────────────────────────────────────

    def _scan_worker(self):
        """Background scanning worker thread."""
        try:
            with open(self._source_path, 'rb') as source:
                offset = 0
                max_header_len = max(len(s.header) for s in self._signatures)
                peek_size = max(max_header_len, 16)

                while offset < self.progress.total_bytes:
                    if self._cancel_event.is_set():
                        return

                    self._pause_event.wait()

                    source.seek(offset)
                    buffer = source.read(self.read_buffer_size)
                    if not buffer:
                        break

                    buf_len = len(buffer)
                    pos = 0

                    while pos < buf_len - peek_size:
                        if self._cancel_event.is_set():
                            return

                        matched = False
                        for sig in self._signatures:
                            header_len = len(sig.header)

                            if buffer[pos:pos + header_len] != sig.header:
                                continue

                            chunk = buffer[pos:pos + min(peek_size, buf_len - pos)]

                            if sig.extra_check and not sig.extra_check(chunk):
                                continue

                            file_size = sig.max_size
                            abs_offset = offset + pos

                            if sig.parse_size:
                                source.seek(abs_offset)
                                size_data = source.read(min(sig.max_size, 10 * 1024 * 1024))
                                parsed_size = sig.parse_size(size_data, sig.max_size)
                                if parsed_size and parsed_size >= sig.min_size:
                                    file_size = parsed_size
                                source.seek(offset + buf_len)

                            if file_size < sig.min_size:
                                continue

                            file_size = min(file_size, self.progress.total_bytes - abs_offset)

                            carved = CarvedFile(
                                signature=sig,
                                offset=abs_offset,
                                size=file_size,
                            )

                            with self._lock:
                                self.carved_files.append(carved)
                                ext = sig.extension
                                self.progress.files_found[ext] = self.progress.files_found.get(ext, 0) + 1
                                self.progress.total_files += 1

                            matched = True
                            skip = max(self.chunk_size, min(file_size, 1024 * 1024))
                            pos += skip
                            break

                        if not matched:
                            pos += self.chunk_size

                    offset += buf_len
                    self.progress.scanned_bytes = min(offset, self.progress.total_bytes)
                    self.progress.current_offset = offset
                    self.progress.elapsed = time.time() - self.progress.start_time

            # Final state — only "complete" if we actually scanned something
            self.progress.elapsed = time.time() - self.progress.start_time

            if self.progress.scanned_bytes == 0:
                self.progress.status = 'error'
                self.progress.error = (
                    'Scan finished but read 0 bytes. '
                    'The device may be empty, unreadable, or requires different permissions.'
                )
            else:
                self.progress.status = 'complete'
                self.progress.scanned_bytes = self.progress.total_bytes

        except PermissionError:
            self.progress.status = 'error'
            self.progress.error = 'Permission denied. Run with sudo/admin privileges for raw disk access.'
        except FileNotFoundError:
            self.progress.status = 'error'
            self.progress.error = f'Source not found: {self._source_path}'
        except OSError as e:
            self.progress.status = 'error'
            self.progress.error = f'Read error: {e}'
        except Exception as e:
            self.progress.status = 'error'
            self.progress.error = f'Unexpected error: {e}'

    # ─── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _human_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        elif size < 1024 * 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024 * 1024):.2f} GB"
        else:
            return f"{size / (1024 * 1024 * 1024 * 1024):.2f} TB"
