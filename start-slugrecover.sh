#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  🐌 SlugRecover — Linux Launcher
#  Run this file (double-click, or: bash start-slugrecover.sh)
# ─────────────────────────────────────────────────────────────
cd "$(dirname "$0")"

echo ""
echo "  🐌 Starting SlugRecover..."
echo ""

# ── 1. Find Python ───────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    echo "  ❌ Python isn't installed yet. Install it with:"
    echo "     sudo apt install python3 python3-venv"
    echo "  then run this file again."
    read -n 1 -s -r -p "  Press any key to close..."
    exit 1
fi

# ── 2. One-time setup ────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "  ⚙️  First-time setup — about a minute, only happens once..."
    python3 -m venv .venv || {
        echo "  ❌ Setup failed. You may need: sudo apt install python3-venv"
        read -n 1 -s -r -p "  Press any key to close..."
        exit 1
    }
fi

# ── 3. Install what SlugRecover needs ────────────────────────
./.venv/bin/pip install -q --upgrade pip 2>/dev/null
./.venv/bin/pip install -q -r requirements.txt || {
    echo "  ❌ Couldn't download what SlugRecover needs."
    echo "     Check your internet connection and try again."
    read -n 1 -s -r -p "  Press any key to close..."
    exit 1
}

# ── 4. Drive access ──────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo ""
    echo "  ❓ Recovering from a real drive or memory card?"
    echo "     (That needs your password.)"
    echo ""
    echo "     y = Yes, a drive or memory card"
    echo "     n = No, a disk image file (.img/.dd)"
    echo ""
    read -n 1 -r -p "  Your choice (y/n): " REPLY
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo ""
        echo "  🔑 You'll be asked for your password now."
        echo "     (Nothing shows while you type — that is normal.)"
        echo ""
        ( sleep 3 && xdg-open "http://localhost:5678" >/dev/null 2>&1 ) &
        exec sudo ./.venv/bin/python app.py
    fi
fi

# ── 5. Launch ────────────────────────────────────────────────
echo ""
echo "  ✅ SlugRecover is starting — your browser will open."
echo "     Keep this window open while you use it."
echo ""
( sleep 3 && xdg-open "http://localhost:5678" >/dev/null 2>&1 ) &
exec ./.venv/bin/python app.py
