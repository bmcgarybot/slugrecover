# 🐌 SlugRecover

A simple, clean file recovery tool with a web interface. Scan any drive, memory card, or disk image to find and recover deleted files.

Built with Python + Flask. Runs on Mac, Windows, and Linux.

## What It Does

SlugRecover scans raw storage for deleted files by searching for known file signatures — a technique called **file carving**. No special drivers or complex setup needed.

1. Pick a drive, partition, or disk image
2. Choose what file types to look for
3. Hit scan and watch it find your files
4. Preview and recover what you need

## Supported File Types

### 📷 Photos & Images
JPEG · PNG · HEIF/HEIC · WEBP · GIF · BMP · TIFF · PSD · ICO

### 📷 Camera RAW
CR2 · CR3 · NEF (Nikon) · ARW (Sony) · DNG (Adobe)

### 🎬 Video
MP4 · MOV · AVI · MKV/WebM · FLV · 3GP

### 🎵 Audio
MP3 · WAV · FLAC · AAC · OGG/Vorbis · M4A · AIFF

### 📄 Documents
PDF · ZIP/DOCX/XLSX · RTF

### 📦 Archives
RAR · RAR5 · 7-Zip · GZIP

**40+ file signatures** across 30 unique formats.

## Quick Start

```bash
# Clone
git clone https://github.com/bmcgarybot/slugrecover.git
cd slugrecover

# Install
pip install -r requirements.txt

# Run (use sudo for raw disk access)
sudo python3 app.py        # Mac / Linux
python app.py               # Windows (Run as Administrator)
```

Open **http://localhost:5678** in your browser.

> **Note:** Admin/root is only needed for scanning physical drives. You can scan disk image files (.img, .dd, .raw) without elevated privileges.

## Features

- **Web UI** — Clean dark-themed dashboard, no terminal needed
- **Step-by-step flow** — Pick source → pick file types → scan → recover
- **Live progress** — Real-time speed, ETA, and file discovery counters
- **Smart detection** — Parses file headers for accurate size boundaries
- **Thumbnail previews** — See recovered images before saving
- **Pause / Resume** — Take a break during long scans
- **Quick filters** — One-click buttons for Photos, Videos, or Audio only
- **Cross-platform** — Mac, Windows, Linux
- **Organized output** — Recovered files sorted into folders by type

## How It Works

Every file type starts with specific bytes (a "magic number"). JPEG files start with `FF D8 FF`, PNG starts with `89 50 4E 47`, and so on. SlugRecover reads through raw storage sector by sector, matching these patterns against its signature database.

When it finds a match, it parses the file structure to determine where the file ends, extracts the data, and makes it available for recovery.

## Project Structure

```
slugrecover/
├── app.py             Flask web server
├── scanner.py         File carving engine
├── signatures.py      Signature database (40+ signatures)
├── recovery.py        File extraction + thumbnails
├── requirements.txt
├── templates/         HTML (dashboard, results, settings)
└── static/            CSS + JS
```

## Requirements

- Python 3.8+
- Flask
- Pillow (for image thumbnails)

## Tips

- **Save to a different drive** than the one you're scanning
- **512-byte alignment** (default) gives the most thorough results
- **Larger buffer** = faster scanning but uses more RAM
- Camera RAW files (CR2, NEF, ARW) are typically 20-60MB each — scans with lots of RAW photos take longer
- For SD cards and USB drives on Mac, the path is usually `/dev/diskN`
- On Windows, physical drives are `\\.\PhysicalDrive0`, `\\.\PhysicalDrive1`, etc.

## License

MIT
