# 🐌 SlugRecover

**File Recovery & Carving Tool** — A clean, web-based alternative to PhotoRec/TestDisk.

Scans drives, partitions, or disk images for deleted files using byte signature scanning (file carving). Recovers photos, videos, and documents with a modern dark-themed web UI.

## Priority File Types (Canon Camera First)

| # | Type | Extension | Use Case |
|---|------|-----------|----------|
| 1 | Canon CR2 | .cr2 | Canon DSLR raw photos |
| 2 | Canon CR3 | .cr3 | Canon mirrorless raw photos |
| 3 | JPEG | .jpg | Standard photos |
| 4 | MOV | .mov | Canon video files |
| 5 | MP4 | .mp4 | Video files |
| 6 | HEIF | .heif | Modern Canon photo format |
| 7 | PNG | .png | Screenshots, graphics |
| 8 | TIFF | .tiff | High-quality images |
| 9 | PDF | .pdf | Documents |
| 10 | ZIP/DOCX | .zip | Archives, Word docs |
| 11 | GIF | .gif | Animated images |
| 12 | BMP | .bmp | Bitmap images |
| 13 | WEBP | .webp | Web images |
| 14 | AVI | .avi | Video files |

## Quick Start

### Install

```bash
cd slugrecover
pip install -r requirements.txt
```

### Run

**Mac/Linux** (requires sudo for raw disk access):
```bash
sudo python3 app.py
```

**Windows** (run as Administrator):
```bash
python app.py
```

**Scanning disk images** (no admin required):
```bash
python3 app.py
# Then point it at your .img / .dd / .raw file
```

### Open
Navigate to **http://localhost:5678**

## How It Works

1. **Select Source** — Choose a drive, partition, or disk image file
2. **Pick File Types** — Select which formats to scan for (all selected by default)
3. **Start Scan** — Watch live progress with real-time file discovery
4. **Review Results** — Browse recovered files with thumbnails
5. **Recover** — Save selected files or recover all

## Features

- **File Carving Engine** — Scans raw bytes for known file signatures (magic bytes)
- **Smart Size Detection** — Parses file headers to determine actual boundaries (TIFF IFDs, BMFF boxes, JPEG end markers)
- **Live Progress** — Server-Sent Events for real-time scan updates
- **Thumbnail Previews** — Auto-generates thumbnails for recovered images (including CR2 embedded previews)
- **Pause/Resume** — Pause long scans and resume later
- **Cross-Platform** — Works on Mac, Windows, and Linux
- **Dark Theme UI** — Clean, modern interface matching SlugTube aesthetic

## Architecture

```
slugrecover/
├── app.py           # Flask web server (port 5678)
├── scanner.py       # Core file carving engine
├── signatures.py    # File signature database (magic bytes)
├── recovery.py      # File extraction + thumbnail generation
├── requirements.txt
├── templates/
│   ├── base.html        # Layout + navbar
│   ├── dashboard.html   # Main scan page
│   ├── results.html     # Recovery results
│   └── settings.html    # Configuration
└── static/
    ├── css/style.css    # Dark theme styles
    └── js/app.js        # Frontend logic
```

## Notes

- Raw disk/partition scanning requires admin/root privileges
- Disk image files (.img, .dd, .raw) can be scanned without admin
- Recovered files are organized in subfolders by type: `CR2/`, `JPG/`, `MOV/`, etc.
- CR2 thumbnails are extracted from the embedded JPEG preview in the raw file
- Large drives can take a while — use the pause button and come back later
