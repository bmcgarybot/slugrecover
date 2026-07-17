#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  🐌 SlugRecover — macOS Launcher
#  Just double-click this file. It sets everything up for you.
# ─────────────────────────────────────────────────────────────
cd "$(dirname "$0")"
APP_DIR="$(pwd)"

echo ""
echo "  🐌 Starting SlugRecover..."
echo ""

# ── 1. Find a REAL Python ────────────────────────────────────
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
    xcode-select --install 2>/dev/null

    echo "  ⏳ Waiting for the install to finish..."
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
        echo "     Please try again, or install Python from python.org"
        read -n 1 -s -r -p "  Press any key to close..."
        exit 1
    fi
fi

# ── 2. One-time setup (creates a private Python environment) ─
if [ ! -d ".venv" ]; then
    echo "  ⚙️  First-time setup — this takes about a minute"
    echo "     and only happens once..."
    "$PYTHON" -m venv .venv || {
        echo "  ❌ Setup failed."
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

# ── 4. Launch with admin access automatically ────────────────
#    Uses the standard macOS password dialog (like installing an app).

if [ "$EUID" -ne 0 ]; then
    echo ""
    echo "  🔑 Your Mac will ask for your password so SlugRecover"
    echo "     can access drives and memory cards."
    echo ""

    # Write a temp launch script to avoid quoting hell with osascript
    LAUNCH_SCRIPT="$APP_DIR/.slugrecover_launch.sh"
    cat > "$LAUNCH_SCRIPT" << INNERSCRIPT
#!/bin/bash
cd '$APP_DIR'
exec '$APP_DIR/.venv/bin/python' '$APP_DIR/app.py'
INNERSCRIPT
    chmod +x "$LAUNCH_SCRIPT"

    ( sleep 3 && open "http://localhost:5678" ) &

    # Native macOS password popup
    osascript -e "do shell script quoted form of \"$LAUNCH_SCRIPT\" with administrator privileges"
    EXIT_CODE=$?

    # Clean up temp script
    rm -f "$LAUNCH_SCRIPT" 2>/dev/null

    if [ $EXIT_CODE -ne 0 ]; then
        echo ""
        echo "  ℹ️  Running without admin access."
        echo "     You can scan disk image files, but not drives directly."
        echo ""
        ( sleep 2 && open "http://localhost:5678" ) &
        exec ./.venv/bin/python app.py
    fi
else
    echo ""
    echo "  ✅ SlugRecover is starting — your browser will open."
    echo "     Keep this window open while you use it."
    echo ""
    ( sleep 3 && open "http://localhost:5678" ) &
    exec ./.venv/bin/python app.py
fi
