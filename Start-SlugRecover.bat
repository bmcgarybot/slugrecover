@echo off
REM ────────────────────────────────────────────────────────────
REM  🐌 SlugRecover — Windows Launcher
REM  Just double-click this file. It handles everything.
REM ────────────────────────────────────────────────────────────
title SlugRecover

REM ── Auto-elevate to admin (standard Windows UAC popup) ─────
net session >nul 2>nul
if %errorlevel% neq 0 (
    echo Requesting admin access...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

echo.
echo   Starting SlugRecover...
echo.

REM ── 1. Find Python ─────────────────────────────────────────
where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=py -3"
    goto :havepython
)
where python >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=python"
    goto :havepython
)
echo   Python isn't installed yet.
echo.
echo   Opening the Python download page for you.
echo   IMPORTANT: on the installer's first screen, tick the box
echo   that says "Add Python to PATH", then click Install Now.
echo   When it finishes, double-click this file again.
echo.
start https://www.python.org/downloads/
pause
exit /b 1

:havepython

REM ── 2. One-time setup ──────────────────────────────────────
if not exist ".venv" (
    echo   First-time setup — this takes about a minute
    echo   and only happens once...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo   Setup failed. Please try again.
        pause
        exit /b 1
    )
)

REM ── 3. Install what SlugRecover needs ──────────────────────
".venv\Scripts\python.exe" -m pip install -q --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo   Couldn't download what SlugRecover needs.
    echo   Check your internet connection and try again.
    pause
    exit /b 1
)

REM ── 4. Launch ──────────────────────────────────────────────
echo.
echo   SlugRecover is starting — your browser will open.
echo   Keep this window open while you use it.
echo.
start "" http://localhost:5678
".venv\Scripts\python.exe" app.py
pause
