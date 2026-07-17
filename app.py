"""
SlugRecover — Flask Web Application
Main entry point. Serves the web UI and handles API endpoints.
"""

import os
import sys
import json
import time
import platform
from flask import (Flask, render_template, request, jsonify, Response,
                   send_file, send_from_directory, stream_with_context)

from scanner import FileCarver
from signatures import get_signature_info, get_all_extensions, SIGNATURES
from recovery import recover_files, recover_all, get_recovery_stats

app = Flask(__name__)
app.config['SECRET_KEY'] = 'slugrecover-secret-key'

# Global carver instance
carver = FileCarver()

# Default settings
settings = {
    'output_dir': os.path.expanduser('~/SlugRecover_Output'),
    'chunk_size': 512,
    'read_buffer_mb': 4,
}


# ─── Pages ──────────────────────────────────────────────────────────────────

@app.route('/')
def dashboard():
    """Main dashboard page."""
    drives = []
    is_admin = False

    try:
        drives = carver.list_drives()
    except Exception:
        pass

    # Check if running with elevated privileges
    if platform.system() != 'Windows':
        is_admin = os.geteuid() == 0
    else:
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            pass

    return render_template('dashboard.html',
                           drives=drives,
                           signatures=get_signature_info(),
                           is_admin=is_admin,
                           settings=settings,
                           platform=platform.system())


@app.route('/results')
def results_page():
    """Scan results page."""
    return render_template('results.html',
                           settings=settings)


@app.route('/settings')
def settings_page():
    """Settings page."""
    return render_template('settings.html',
                           settings=settings,
                           signatures=get_signature_info())


# ─── API Endpoints ──────────────────────────────────────────────────────────

@app.route('/api/scan/start', methods=['POST'])
def api_start_scan():
    """Start a new scan."""
    data = request.get_json()
    source = data.get('source', '')
    file_types = data.get('file_types', [])
    chunk_size = data.get('chunk_size', settings['chunk_size'])
    output_dir = data.get('output_dir', settings['output_dir'])

    if not source:
        return jsonify({'error': 'No source specified'}), 400

    # Validate source exists
    if not os.path.exists(source):
        return jsonify({'error': f'Source not found: {source}'}), 404

    # Update output dir if provided
    settings['output_dir'] = output_dir

    success = carver.start_scan(source, file_types or None, chunk_size)
    if success:
        return jsonify({'status': 'started', 'source': source})
    else:
        return jsonify({'error': 'Scan already in progress or failed to start',
                        'details': carver.progress.error}), 409


@app.route('/api/scan/pause', methods=['POST'])
def api_pause_scan():
    """Pause the current scan."""
    carver.pause_scan()
    return jsonify({'status': 'paused'})


@app.route('/api/scan/resume', methods=['POST'])
def api_resume_scan():
    """Resume a paused scan."""
    carver.resume_scan()
    return jsonify({'status': 'resumed'})


@app.route('/api/scan/cancel', methods=['POST'])
def api_cancel_scan():
    """Cancel the current scan."""
    carver.cancel_scan()
    return jsonify({'status': 'cancelled'})


@app.route('/api/scan/progress')
def api_scan_progress():
    """Get current scan progress."""
    return jsonify(carver.get_progress())


@app.route('/api/scan/progress/stream')
def api_scan_progress_stream():
    """Server-Sent Events stream for real-time progress updates."""
    def generate():
        last_status = None
        while True:
            progress = carver.get_progress()

            # Always send update
            yield f"data: {json.dumps(progress)}\n\n"

            # Stop streaming if scan is done
            if progress['status'] in ('complete', 'cancelled', 'error', 'idle'):
                if last_status == progress['status']:
                    break
                last_status = progress['status']
                time.sleep(0.5)
                # Send final update
                progress = carver.get_progress()
                progress['results'] = carver.get_results()
                yield f"data: {json.dumps(progress)}\n\n"
                break

            last_status = progress['status']
            time.sleep(0.5)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@app.route('/api/scan/results')
def api_scan_results():
    """Get scan results."""
    results = carver.get_results()
    progress = carver.get_progress()
    return jsonify({
        'results': results,
        'progress': progress,
        'total': len(results),
    })


@app.route('/api/recover', methods=['POST'])
def api_recover():
    """Recover selected files."""
    data = request.get_json()
    file_ids = data.get('file_ids', [])
    output_dir = data.get('output_dir', settings['output_dir'])

    if not file_ids:
        return jsonify({'error': 'No files selected'}), 400

    results = recover_files(carver, file_ids, output_dir)

    succeeded = sum(1 for r in results if r['success'])
    return jsonify({
        'results': results,
        'total': len(results),
        'succeeded': succeeded,
        'failed': len(results) - succeeded,
        'output_dir': output_dir,
    })


@app.route('/api/recover/all', methods=['POST'])
def api_recover_all():
    """Recover all carved files."""
    data = request.get_json() or {}
    output_dir = data.get('output_dir', settings['output_dir'])

    results = recover_all(carver, output_dir)

    succeeded = sum(1 for r in results if r['success'])
    return jsonify({
        'results': results,
        'total': len(results),
        'succeeded': succeeded,
        'failed': len(results) - succeeded,
        'output_dir': output_dir,
    })


@app.route('/api/thumbnail/<path:file_id>')
def api_thumbnail(file_id):
    """Serve a thumbnail image."""
    carved = carver.get_carved_file(file_id)
    if carved and carved.thumbnail_path and os.path.exists(carved.thumbnail_path):
        return send_file(carved.thumbnail_path, mimetype='image/jpeg')
    # Return a placeholder
    return '', 204


@app.route('/api/preview/<path:file_id>')
def api_preview(file_id):
    """Serve a recovered file for preview/download."""
    carved = carver.get_carved_file(file_id)
    if not carved or not carved.recovery_path or not os.path.exists(carved.recovery_path):
        return jsonify({'error': 'File not recovered yet'}), 404

    return send_file(carved.recovery_path,
                     as_attachment=False,
                     download_name=os.path.basename(carved.recovery_path))


@app.route('/api/drives')
def api_drives():
    """List available drives."""
    try:
        drives = carver.list_drives()
        return jsonify({'drives': drives})
    except Exception as e:
        return jsonify({'error': str(e), 'drives': []}), 500


@app.route('/api/browse')
def api_browse():
    """Browse filesystem for files/directories. Used by the file browser UI."""
    path = request.args.get('path', '')

    # Default starting locations by platform
    if not path:
        if platform.system() == 'Darwin':
            path = '/Volumes'
        elif platform.system() == 'Windows':
            path = 'C:\\'
        else:
            path = '/'

    path = os.path.expanduser(path)

    if not os.path.exists(path):
        return jsonify({'error': f'Path not found: {path}', 'items': [], 'current': path})

    # If it's a file, return its info
    if os.path.isfile(path):
        return jsonify({
            'current': path,
            'is_file': True,
            'size': os.path.getsize(path),
            'items': [],
        })

    # List directory contents
    items = []
    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return jsonify({'error': 'Permission denied', 'items': [], 'current': path})

    # Add parent directory
    parent = os.path.dirname(path.rstrip('/'))
    if parent and parent != path:
        items.append({
            'name': '..',
            'path': parent,
            'is_dir': True,
            'size': 0,
            'size_human': '',
        })

    for entry in entries:
        if entry.startswith('.'):
            continue  # Skip hidden files
        full_path = os.path.join(path, entry)
        try:
            is_dir = os.path.isdir(full_path)
            size = 0 if is_dir else os.path.getsize(full_path)
            items.append({
                'name': entry,
                'path': full_path,
                'is_dir': is_dir,
                'size': size,
                'size_human': FileCarver._human_size(size) if not is_dir else '',
            })
        except (PermissionError, OSError):
            continue

    return jsonify({
        'current': path,
        'is_file': False,
        'items': items,
    })


@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    """Get or update settings."""
    if request.method == 'POST':
        data = request.get_json()
        if 'output_dir' in data:
            settings['output_dir'] = data['output_dir']
        if 'chunk_size' in data:
            settings['chunk_size'] = int(data['chunk_size'])
        if 'read_buffer_mb' in data:
            settings['read_buffer_mb'] = int(data['read_buffer_mb'])
            carver.read_buffer_size = settings['read_buffer_mb'] * 1024 * 1024
        return jsonify({'status': 'updated', 'settings': settings})

    return jsonify(settings)


@app.route('/api/signatures')
def api_signatures():
    """Get supported file signatures."""
    return jsonify(get_signature_info())


@app.route('/api/stats')
def api_stats():
    """Get recovery statistics."""
    stats = get_recovery_stats(settings['output_dir'])
    return jsonify(stats)


# ─── Static file serving ──────────────────────────────────────────────────

@app.route('/favicon.ico')
def favicon():
    return '', 204


# ─── Main ───────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5678))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'

    print(f"""
╔══════════════════════════════════════════════════╗
║            🐌 SlugRecover v1.0                   ║
║         File Recovery & Carving Tool             ║
╠══════════════════════════════════════════════════╣
║  Web UI: http://localhost:{port:<5}                  ║
║  Platform: {platform.system():<20}              ║
║  Admin: {'Yes' if os.geteuid() == 0 else 'No':<5}                                ║
╚══════════════════════════════════════════════════╝
""")

    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
