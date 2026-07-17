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
import preview as preview_engine

app = Flask(__name__)
app.config['SECRET_KEY'] = 'slugrecover-secret-key'

# Global carver instance
carver = FileCarver()

# Default settings
settings = {
    'output_dir': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Recovered'),
    'chunk_size': 512,
    'read_buffer_mb': 4,
}


# ─── Pages ──────────────────────────────────────────────────────────────────

@app.route('/')
def dashboard():
    """Main dashboard page."""
    drives = []

    try:
        drives = carver.list_drives()
    except Exception:
        pass

    return render_template('dashboard.html',
                           drives=drives,
                           signatures=get_signature_info(),
                           settings=settings)


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

    preview_engine.clear_cache()  # previews belong to the previous scan
    success = carver.start_scan(source, file_types or None, chunk_size)
    if success:
        return jsonify({'status': 'started', 'source': source})
    else:
        # Return the actual error so the user knows what happened
        err = carver.progress.error or 'Unknown error'
        return jsonify({'error': err}), 409


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
    """Serve a thumbnail — generated straight from the drive, BEFORE
    recovery, so you can see what a file is before saving it."""
    carved = carver.get_carved_file(file_id)
    if not carved:
        return '', 404

    # Post-recovery thumbnail already on disk? Use it.
    if carved.thumbnail_path and os.path.exists(carved.thumbnail_path):
        return send_file(carved.thumbnail_path, mimetype='image/jpeg')

    # Pre-recovery: build one directly from the source
    if carved.signature.category == 'image' and carver._source_path:
        thumb = preview_engine.generate_preview(
            carver._source_path, carved.offset, carved.size,
            carved.signature.extension, size=preview_engine.THUMB_SIZE)
        if thumb:
            return send_file(thumb, mimetype='image/jpeg')

    return '', 204


@app.route('/api/preview/<path:file_id>')
def api_preview(file_id):
    """Serve a large preview of a file. Works BEFORE recovery (generated
    from the drive) and after (served from the recovered file)."""
    carved = carver.get_carved_file(file_id)
    if not carved:
        return jsonify({'error': 'File not found in scan results'}), 404

    # Recovered already? Serve the real file.
    if carved.recovery_path and os.path.exists(carved.recovery_path):
        return send_file(carved.recovery_path,
                         as_attachment=False,
                         download_name=os.path.basename(carved.recovery_path))

    # Pre-recovery large preview from the source
    if carved.signature.category == 'image' and carver._source_path:
        big = preview_engine.generate_preview(
            carver._source_path, carved.offset, carved.size,
            carved.signature.extension, size=preview_engine.PREVIEW_SIZE)
        if big:
            return send_file(big, mimetype='image/jpeg')
        return jsonify({'error': "This file couldn't be previewed — it may "
                                 "be damaged. You can still try recovering "
                                 "it."}), 422

    return jsonify({'error': 'Preview is only available for photos. '
                             'Recover the file to open it.'}), 404


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
    """Browse filesystem directories for the file browser."""
    path = request.args.get('path', '')

    if not path:
        if platform.system() == 'Darwin':
            path = '/Volumes'
        elif platform.system() == 'Windows':
            path = 'C:\\'
        else:
            path = '/'

    path = os.path.expanduser(path)

    # If it's a file, return it directly
    if os.path.isfile(path):
        return jsonify({
            'current': path,
            'is_file': True,
            'items': []
        })

    if not os.path.isdir(path):
        # Try parent
        parent = os.path.dirname(path)
        if os.path.isdir(parent):
            path = parent
        else:
            path = '/'

    items = []
    try:
        # Add parent directory
        parent = os.path.dirname(path)
        if parent != path:
            items.append({
                'name': '📁 ..',
                'path': parent,
                'is_dir': True,
                'size_human': ''
            })

        entries = sorted(os.listdir(path), key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
        for entry in entries:
            if entry.startswith('.'):
                continue
            full = os.path.join(path, entry)
            is_dir = os.path.isdir(full)
            size_human = ''
            if not is_dir:
                try:
                    size = os.path.getsize(full)
                    if size < 1024:
                        size_human = f'{size} B'
                    elif size < 1024 * 1024:
                        size_human = f'{size / 1024:.1f} KB'
                    elif size < 1024 * 1024 * 1024:
                        size_human = f'{size / (1024*1024):.1f} MB'
                    else:
                        size_human = f'{size / (1024*1024*1024):.2f} GB'
                except OSError:
                    size_human = '?'

            items.append({
                'name': entry,
                'path': full,
                'is_dir': is_dir,
                'size_human': size_human
            })
    except PermissionError:
        pass

    return jsonify({
        'current': path,
        'is_file': False,
        'items': items
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

    # os.geteuid doesn't exist on Windows — the old banner crashed at launch
    if hasattr(os, 'geteuid'):
        admin_str = 'Yes' if os.geteuid() == 0 else 'No'
    else:
        try:
            import ctypes
            admin_str = 'Yes' if ctypes.windll.shell32.IsUserAnAdmin() != 0 else 'No'
        except Exception:
            admin_str = 'No'

    print(f"""
╔══════════════════════════════════════════════════╗
║            🐌 SlugRecover v1.0                   ║
║         File Recovery & Carving Tool             ║
╠══════════════════════════════════════════════════╣
║  Web UI: http://localhost:{port:<5}                  ║
║  Platform: {platform.system():<20}              ║
║  Admin: {admin_str:<5}                                ║
╚══════════════════════════════════════════════════╝
""")

    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
