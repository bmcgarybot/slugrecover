#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  🐌 SlugRecover — macOS Launcher
#  Just double-click this file. It sets everything up for you.
# ─────────────────────────────────────────────────────────────
cd "$(dirname "$0")"

echo ""
echo "  🐌 Starting SlugRecover..."
echo ""

# ── 0. Self-heal (silent) ────────────────────────────────────
# Remove the download-quarantine flag from this whole folder so
# macOS stops running our app from a hidden translocated copy,
# and bake this folder's real location into the snail app so it
# can always find its way home, even if translocated.
HERE="$(pwd)"
xattr -cr "$HERE" 2>/dev/null
if [ -d "$HERE/SlugRecover.app/Contents/Resources" ]; then
    printf '%s' "$HERE/Start SlugRecover.command" \
        > "$HERE/SlugRecover.app/Contents/Resources/launcher_path" 2>/dev/null
fi

# ── 1. Find a REAL Python ────────────────────────────────────
# On a fresh Mac, `python3` exists only as a stub that pops the
# developer-tools installer and otherwise does nothing. So we don't
# just check that python3 exists — we check that it actually runs.
find_python() {
    for py in python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 \
              /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
        if "$py" -c 'import sys, venv' >/dev/null 2>&1; then
            echo "$py"
            return 0
        fi
    done
    return 1
}

PYTHON="$(find_python)"

if [ -z "$PYTHON" ]; then
    echo "  ⚙️  One-time step: macOS needs to install a small set of"
    echo "     tools (this includes Python) before we can continue."
    echo ""
    echo "  👉 A window will pop up in a moment. Click INSTALL and"
    echo "     wait for it to finish (it can take a few minutes)."
    echo ""
    # Kick off Apple's Command Line Tools installer
    xcode-select --install 2>/dev/null

    echo "  ⏳ Waiting for the install to finish..."
    echo "     (You can leave this window sitting here. It will"
    echo "      continue on its own once the tools are ready.)"
    echo ""
    # Poll for up to ~20 minutes for a working Python to appear
    for i in $(seq 1 240); do
        PYTHON="$(find_python)"
        if [ -n "$PYTHON" ]; then
            echo "  ✅ Tools installed. Continuing..."
            echo ""
            break
        fi
        sleep 5
    done

    if [ -z "$PYTHON" ]; then
        echo "  ❌ The tools didn't finish installing."
        echo ""
        echo "     Please try this: open the App Store, search for"
        echo "     'Xcode', install it, then double-click SlugRecover"
        echo "     again. Or send a photo of this window to whoever"
        echo "     gave you SlugRecover."
        echo ""
        read -n 1 -s -r -p "  Press any key to close..."
        exit 1
    fi
fi

# ── 2. One-time setup (creates a private Python environment) ─
if [ ! -d ".venv" ]; then
    echo "  ⚙️  First-time setup — this takes about a minute"
    echo "     and only happens once..."
    "$PYTHON" -m venv .venv || {
        echo "  ❌ Setup failed while creating the app's workspace."
        echo "     Please send a photo of this window to whoever"
        echo "     gave you SlugRecover."
        read -n 1 -s -r -p "  Press any key to close..."
        exit 1
    }
fi

# ── 3. Install/update what SlugRecover needs ─────────────────
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
    echo "  ❓ Do you want to recover files from a real drive or"
    echo "     memory card? (This needs your Mac password.)"
    echo ""
    echo "     y = Yes, I'm recovering from a drive or card"
    echo "     n = No, I'm scanning a disk image file (.img/.dd)"
    echo ""
    read -n 1 -r -p "  Your choice (y/n): " REPLY
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo ""
        echo "  🔑 macOS will now ask for your password."
        echo "     (Nothing shows while you type — that's normal.)"
        echo ""
        ( sleep 3 && open "http://localhost:5678" ) &
        exec sudo ./.venv/bin/python app.py
    fi
fi

# ── 5. Launch ────────────────────────────────────────────────
echo ""
echo "  ✅ SlugRecover is starting — your browser will open."
echo "     Keep this window open while you use it."
echo "     (Close this window when you're finished.)"
echo ""
( sleep 3 && open "http://localhost:5678" ) &
exec ./.venv/bin/python app.py
