"""
SlugRecover — Core File Carving Engine
Scans raw disks/drives/disk images for deleted files using byte signature scanning.
"""

import os
import time
import threading
import platform
import struct
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass, field

from signatures import FileSignature, SIGNATURES, get_signatures_for_types


@dataclass
class CarvedFile:
    """Represents a file found during scanning."""
    signature: FileSignature
    offset: int              # Byte offset in source
    size: int                # Estimated file size
    data: Optional[bytes] = None  # Raw file data (loaded on recovery)
    recovered: bool = False
    recovery_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    valid: bool = True

    @property
    def size_human(self) -> str:
        """Human-readable file size."""
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
    status: str = "idle"        # idle, scanning, paused, complete, cancelled, error
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
        self.chunk_size = chunk_size  # Scan alignment (sector size)
        self.read_buffer_size = 4 * 1024 * 1024  # 4MB read buffer
        self.progress = ScanProgress()
        self.carved_files: List[CarvedFile] = []
        self._seen_offsets: set = set()
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Start unpaused
        self._log_file = None
        self._scan_thread: Optional[threading.Thread] = None
        self._source_path: Optional[str] = None
        self._signatures: List[FileSignature] = []

    def _log(self, msg: str):
        """Write to scan log file for troubleshooting."""
        try:
            if self._log_file:
                import datetime
                ts = datetime.datetime.now().strftime('%H:%M:%S')
                self._log_file.write(f"[{ts}] {msg}\n")
                self._log_file.flush()
        except Exception:
            pass

    # ─── Device Size Detection ───────────────────────────────────────

    @staticmethod
    def _get_device_size(path: str) -> Optional[int]:
        """
        Get the size of a block device, handling platform quirks.
        macOS seek(0,2) returns 0 on raw devices — needs diskutil/ioctl.
        """
        system = platform.system()

        # Try seek first (works on Linux, Windows, and disk images)
        try:
            with open(path, 'rb') as f:
                f.seek(0, 2)
                size = f.tell()
                if size > 0:
                    return size
        except PermissionError:
            raise PermissionError(
                "SlugRecover doesn't have permission to read this drive. "
                "Close SlugRecover and reopen it — your computer will ask "
                "for your password to allow access."
            )
        except Exception:
            pass

        if system == 'Darwin':
            size = FileCarver._get_macos_device_size(path)
            if size and size > 0:
                return size

        if system == 'Linux':
            size = FileCarver._get_linux_device_size(path)
            if size and size > 0:
                return size

        return None

    @staticmethod
    def _get_macos_device_size(path: str) -> Optional[int]:
        """Get device size on macOS using diskutil or ioctl."""
        import re

        dev_name = os.path.basename(path)
        if dev_name.startswith('r'):
            dev_name = dev_name[1:]

        # Try diskutil info
        try:
            result = subprocess.run(
                ['diskutil', 'info', dev_name],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if any(k in line for k in ('Disk Size:', 'Total Size:', 'Partition Size:')):
                        match = re.search(r'\((\d+)\s*[Bb]ytes?\)', line)
                        if match:
                            return int(match.group(1))
        except Exception:
            pass

        # Try ioctl
        try:
            import fcntl
            DKIOCGETBLOCKSIZE = 0x40046418
            DKIOCGETBLOCKCOUNT = 0x40086419
            with open(path, 'rb') as f:
                fd = f.fileno()
                buf = bytearray(4)
                fcntl.ioctl(fd, DKIOCGETBLOCKSIZE, buf)
                block_size = struct.unpack('I', buf)[0]
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

    def list_drives(self) -> List[dict]:
        """List available drives/partitions on the system."""
        drives = []
        system = platform.system()

        if system == 'Linux':
            # List block devices with friendly names from /sys/block
            try:
                if os.path.exists('/proc/partitions'):
                    with open('/proc/partitions', 'r') as f:
                        lines = f.readlines()[2:]  # Skip header
                    root_dev = self._linux_root_device()
                    for line in lines:
                        parts = line.strip().split()
                        if len(parts) >= 4:
                            name = parts[3]
                            size_blocks = int(parts[2]) * 1024
                            path = f"/dev/{name}"
                            base = name.rstrip('0123456789')
                            if base.startswith('nvme') and 'p' in name:
                                base = name.rsplit('p', 1)[0]
                            model = self._sys_read(f'/sys/block/{base}/device/model')
                            vendor = self._sys_read(f'/sys/block/{base}/device/vendor')
                            removable = self._sys_read(f'/sys/block/{base}/removable') == '1'
                            label = ' '.join(x for x in (vendor, model) if x) or name
                            drives.append({
                                'path': path,
                                'name': name,
                                'label': label,
                                'size': size_blocks,
                                'size_human': self._human_size(size_blocks),
                                'type': 'partition' if name != base else 'disk',
                                'removable': removable,
                                'system': root_dev is not None and name.startswith(root_dev),
                            })
            except Exception:
                pass

        elif system == 'Darwin':
            # macOS — real names and sizes via diskutil (the old listing
            # showed every raw node with size "Unknown", which meant
            # nothing to a non-technical person)
            try:
                import subprocess, plistlib
                out = subprocess.run(
                    ['diskutil', 'list', '-plist'],
                    capture_output=True, timeout=10)
                info = plistlib.loads(out.stdout) if out.returncode == 0 else {}
                for name in sorted(info.get('WholeDisks', [])):
                    try:
                        d = subprocess.run(
                            ['diskutil', 'info', '-plist', name],
                            capture_output=True, timeout=10)
                        di = plistlib.loads(d.stdout) if d.returncode == 0 else {}
                    except Exception:
                        di = {}
                    size = int(di.get('TotalSize', 0) or 0)
                    label = (di.get('MediaName') or di.get('IORegistryEntryName')
                             or name)
                    drives.append({
                        'path': f"/dev/{name}",
                        'name': name,
                        'label': label,
                        'size': size,
                        'size_human': self._human_size(size) if size else 'Unknown',
                        'type': 'disk',
                        'removable': bool(di.get('RemovableMedia')
                                          or di.get('Removable')
                                          or not di.get('Internal', True)),
                        'system': bool(di.get('Internal', False))
                                  and name == 'disk0',
                    })
            except Exception:
                # diskutil unavailable — fall back to raw listing
                try:
                    for name in sorted(os.listdir('/dev')):
                        if name.startswith('disk') and 's' not in name[4:]:
                            drives.append({
                                'path': f"/dev/{name}", 'name': name,
                                'label': name, 'size': 0,
                                'size_human': 'Unknown', 'type': 'disk',
                                'removable': False, 'system': False,
                            })
                except Exception:
                    pass

        elif system == 'Windows':
            # Windows — list physical drives
            for i in range(16):
                path = f"\\\\.\\PhysicalDrive{i}"
                try:
                    with open(path, 'rb') as f:
                        f.seek(0, 2)
                        size = f.tell()
                    drives.append({
                        'path': path,
                        'name': f"PhysicalDrive{i}",
                        'label': f"Drive {i}",
                        'size': size,
                        'size_human': self._human_size(size),
                        'type': 'disk',
                        'removable': False,
                        'system': i == 0,
                    })
                except Exception:
                    continue

        return drives

    def start_scan(self, source_path: str, file_types: Optional[List[str]] = None,
                   chunk_size: Optional[int] = None) -> bool:
        """Start scanning a source in a background thread."""
        with self._start_lock:
            if self.progress.status in ('scanning', 'paused'):
                return False
            # Reset state so retries work after errors
            self.progress = ScanProgress()
            # Claim immediately so a concurrent request can't also start
            self.progress.status = 'scanning'

        if chunk_size:
            self.chunk_size = chunk_size

        self._source_path = source_path
        self._signatures = get_signatures_for_types(file_types) if file_types else SIGNATURES
        self.carved_files = []
        self._seen_offsets = set()
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

        # Guard: if size detection failed, don't fake a scan
        if not total_size or total_size <= 0:
            self.progress = ScanProgress(
                status='error',
                error=(
                    f'Could not read the size of this drive. '
                    f'Close SlugRecover and reopen it — your computer will '
                    f'ask for your password to allow access.'
                )
            )
            return False

        self.progress = ScanProgress(
            total_bytes=total_size,
            status='scanning',
            start_time=time.time(),
        )

        # Open scan log for troubleshooting
        try:
            log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scan.log')
            self._log_file = open(log_path, 'w')
            self._log(f"Scan started: {source_path}")
            self._log(f"Size: {self._human_size(total_size)} ({total_size} bytes)")
            self._log(f"Signatures: {len(self._signatures)} active")
            self._log(f"Chunk size: {self.chunk_size} bytes")
        except Exception:
            self._log_file = None

        self._scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self._scan_thread.start()
        return True

    def pause_scan(self):
        """Pause the current scan."""
        if self.progress.status == 'scanning':
            self._pause_event.clear()
            self.progress.status = 'paused'

    def resume_scan(self):
        """Resume a paused scan."""
        if self.progress.status == 'paused':
            self._pause_event.set()
            self.progress.status = 'scanning'

    def cancel_scan(self):
        """Cancel the current scan."""
        if self.progress.status not in ('scanning', 'paused'):
            return
        self._cancel_event.set()
        self._pause_event.set()  # Unpause to allow thread to exit
        self.progress.status = 'cancelled'

    def get_progress(self) -> dict:
        """Get current scan progress."""
        return self.progress.to_dict()

    def get_results(self) -> List[dict]:
        """Get all carved files."""
        with self._lock:
            return [f.to_dict() for f in self.carved_files]

    def get_carved_file(self, file_id: str) -> Optional[CarvedFile]:
        """Get a specific carved file by ID."""
        with self._lock:
            for f in self.carved_files:
                if f"{f.signature.extension}_{f.offset}" == file_id:
                    return f
        return None

    def _scan_worker(self):
        """Background scanning worker thread."""
        try:
            with open(self._source_path, 'rb') as source:
                offset = 0
                # Build a quick lookup of header lengths needed
                max_header_len = max(len(s.header) for s in self._signatures)
                # For BMFF types, we need more bytes for validation
                peek_size = max(max_header_len, 16)

                while offset < self.progress.total_bytes:
                    # Check cancel
                    if self._cancel_event.is_set():
                        return

                    # Check pause
                    self._pause_event.wait()

                    # Read a buffer
                    source.seek(offset)
                    buffer = source.read(self.read_buffer_size)
                    if not buffer:
                        break

                    buf_len = len(buffer)
                    pos = 0

                    # Scan the whole buffer. Positions in the final
                    # peek_size bytes are validated against a chunk that
                    # may be short; extra_check functions handle short
                    # input, and the overlapped advance below rescans the
                    # tail with full context anyway.
                    scan_end = buf_len if buf_len < self.read_buffer_size \
                        else buf_len - peek_size

                    while pos < scan_end:
                        if self._cancel_event.is_set():
                            return

                        # Try each signature at this position
                        matched = False
                        for sig in self._signatures:
                            header_len = len(sig.header)

                            # Quick header check
                            if buffer[pos:pos + header_len] != sig.header:
                                continue

                            # Get enough data for validation
                            chunk = buffer[pos:pos + min(peek_size, buf_len - pos)]

                            # Extra validation check
                            if sig.extra_check and not sig.extra_check(chunk):
                                continue

                            # Found a match — determine file size
                            file_size = sig.max_size
                            abs_offset = offset + pos

                            if sig.parse_size_fh:
                                # Seek-based parser: tiny targeted reads,
                                # finds trailing moov on huge videos
                                parsed_size = sig.parse_size_fh(source, abs_offset, sig.max_size)
                                if parsed_size and parsed_size >= sig.min_size:
                                    file_size = parsed_size
                                source.seek(offset + buf_len)
                            elif sig.parse_size:
                                # Read more data for size parsing
                                source.seek(abs_offset)
                                size_data = source.read(min(sig.max_size, 10 * 1024 * 1024))
                                parsed_size = sig.parse_size(size_data, sig.max_size)
                                if parsed_size and parsed_size >= sig.min_size:
                                    file_size = parsed_size
                                # Reset source position
                                source.seek(offset + buf_len)

                            # Validate minimum size
                            if file_size < sig.min_size:
                                continue

                            # Cap at remaining source
                            file_size = min(file_size, self.progress.total_bytes - abs_offset)

                            carved = CarvedFile(
                                signature=sig,
                                offset=abs_offset,
                                size=file_size,
                            )

                            with self._lock:
                                # Overlapped buffer reads can revisit the
                                # same position — never record twice
                                if abs_offset in self._seen_offsets:
                                    matched = True
                                    pos += self.chunk_size
                                    break
                                self._seen_offsets.add(abs_offset)
                                self.carved_files.append(carved)
                                ext = sig.extension
                                self.progress.files_found[ext] = self.progress.files_found.get(ext, 0) + 1
                                self.progress.total_files += 1
                                # Log to scan log
                                self._log(f"FOUND {sig.name} ({sig.extension}) at 0x{abs_offset:X}, "
                                         f"size={self._human_size(file_size)}, "
                                         f"parsed={'yes' if file_size != sig.max_size else 'MAX_SIZE'}")

                            matched = True
                            # Skip past this file to avoid finding embedded files
                            skip = max(self.chunk_size, min(file_size, 1024 * 1024))
                            pos += skip
                            break

                        if not matched:
                            pos += self.chunk_size

                    # Update progress. Advance with an overlap of peek_size
                    # so a header straddling the buffer boundary is seen by
                    # the next read (the old code stepped by the full buffer
                    # and permanently missed any file starting in the last
                    # peek_size bytes of each 4MB read).
                    if buf_len == self.read_buffer_size:
                        # Align the overlap down to chunk_size so scan
                        # positions stay sector-aligned
                        overlap = peek_size + (-peek_size % self.chunk_size)
                        offset += buf_len - overlap
                    else:
                        offset += buf_len  # short read = end of source
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
            self.progress.error = (
                "SlugRecover doesn't have permission to read this drive. "
                "Close SlugRecover and reopen it — your computer will ask for "
                "your password to allow access."
            )
        except FileNotFoundError:
            self.progress.status = 'error'
            self.progress.error = f'Source not found: {self._source_path}'
        except Exception as e:
            self.progress.status = 'error'
            self.progress.error = str(e)

    @staticmethod
    def _sys_read(path: str) -> str:
        """Read a small /sys file, stripped; '' on any failure."""
        try:
            with open(path, 'r') as f:
                return f.read().strip()
        except OSError:
            return ''

    @staticmethod
    def _linux_root_device():
        """Base device name backing / (e.g. 'nvme0n1'), or None."""
        try:
            import re as _re
            with open('/proc/mounts', 'r') as f:
                for line in f:
                    dev, mnt = line.split()[:2]
                    if mnt == '/' and dev.startswith('/dev/'):
                        name = os.path.basename(os.path.realpath(dev))
                        return _re.sub(r'p?\d+$', '', name)
        except Exception:
            pass
        return None

    @staticmethod
    def _human_size(size: int) -> str:
        """Convert bytes to human-readable size."""
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
