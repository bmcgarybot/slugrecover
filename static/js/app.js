/* SlugRecover — Main JavaScript */

// ─── State ──────────────────────────────────────────────

let scanState = {
    status: 'idle',
    source: null,
    eventSource: null,
    selectedFiles: new Set(),
};

// ─── Scan Controls ──────────────────────────────────────

function startScan() {
    const sourceInput = document.getElementById('source-path');
    const source = sourceInput ? sourceInput.value.trim() : '';
    if (!source) {
        alert('Please enter a source path or select a drive.');
        return;
    }

    // Get selected file types
    const types = [];
    document.querySelectorAll('.type-checkbox input:checked').forEach(cb => {
        types.push(cb.value);
    });

    const outputDir = document.getElementById('output-dir')?.value || '';

    fetch('/api/scan/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            source: source,
            file_types: types.length > 0 ? types : null,
            output_dir: outputDir || undefined,
        }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            alert('Error: ' + data.error);
            return;
        }
        showProgress();
        startProgressStream();
    })
    .catch(err => alert('Failed to start scan: ' + err));
}

function pauseScan() {
    fetch('/api/scan/pause', { method: 'POST' })
        .then(r => r.json())
        .then(() => updateStatusBadge('paused'));
}

function resumeScan() {
    fetch('/api/scan/resume', { method: 'POST' })
        .then(r => r.json())
        .then(() => updateStatusBadge('scanning'));
}

function cancelScan() {
    if (!confirm('Cancel the current scan?')) return;
    fetch('/api/scan/cancel', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
            updateStatusBadge('cancelled');
            if (scanState.eventSource) {
                scanState.eventSource.close();
                scanState.eventSource = null;
            }
        });
}

// ─── Progress Streaming ─────────────────────────────────

function startProgressStream() {
    if (scanState.eventSource) {
        scanState.eventSource.close();
    }

    scanState.eventSource = new EventSource('/api/scan/progress/stream');

    scanState.eventSource.onmessage = function(event) {
        const data = JSON.parse(event.data);
        updateProgress(data);

        if (data.status === 'complete' || data.status === 'cancelled' || data.status === 'error') {
            scanState.eventSource.close();
            scanState.eventSource = null;

            if (data.status === 'complete') {
                loadResults();
            }
        }
    };

    scanState.eventSource.onerror = function() {
        scanState.eventSource.close();
        scanState.eventSource = null;
    };
}

function updateProgress(data) {
    scanState.status = data.status;
    updateStatusBadge(data.status);

    // Progress bar
    const bar = document.getElementById('progress-bar');
    const text = document.getElementById('progress-text');
    if (bar) {
        bar.style.width = data.percent + '%';
        bar.className = 'progress-bar ' + data.status;
    }
    if (text) {
        text.textContent = data.percent.toFixed(1) + '%';
    }

    // Stats
    setTextById('stat-speed', data.speed);
    setTextById('stat-files', data.total_files);
    setTextById('stat-elapsed', formatTime(data.elapsed));
    setTextById('stat-eta', data.eta ? formatTime(data.eta) : '—');
    setTextById('stat-scanned', formatBytes(data.scanned_bytes) + ' / ' + formatBytes(data.total_bytes));

    // Found files chips
    const foundGrid = document.getElementById('found-grid');
    if (foundGrid && data.files_found) {
        foundGrid.innerHTML = '';
        for (const [ext, count] of Object.entries(data.files_found)) {
            const chip = document.createElement('div');
            chip.className = 'found-chip';
            chip.innerHTML = `${ext.toUpperCase()} <span class="count">${count}</span>`;
            foundGrid.appendChild(chip);
        }
    }

    // Error
    if (data.error) {
        const errEl = document.getElementById('scan-error');
        if (errEl) {
            errEl.textContent = data.error;
            errEl.style.display = 'block';
        }
    }

    // Show/hide buttons
    const pauseBtn = document.getElementById('btn-pause');
    const resumeBtn = document.getElementById('btn-resume');
    const cancelBtn = document.getElementById('btn-cancel');

    if (pauseBtn) pauseBtn.style.display = data.status === 'scanning' ? '' : 'none';
    if (resumeBtn) resumeBtn.style.display = data.status === 'paused' ? '' : 'none';
    if (cancelBtn) cancelBtn.style.display = ['scanning', 'paused'].includes(data.status) ? '' : 'none';

    // If complete, show results link
    if (data.status === 'complete' && data.results) {
        showResults(data.results);
    }
}

function showProgress() {
    const setupSection = document.getElementById('scan-setup');
    const progressSection = document.getElementById('scan-progress');
    if (setupSection) setupSection.style.display = 'none';
    if (progressSection) {
        progressSection.style.display = 'block';
        progressSection.classList.add('active');
    }
}

// ─── Results ────────────────────────────────────────────

function loadResults() {
    fetch('/api/scan/results')
        .then(r => r.json())
        .then(data => {
            if (data.results) {
                showResults(data.results);
            }
        });
}

function showResults(results) {
    const grid = document.getElementById('results-grid');
    const section = document.getElementById('results-section');
    const countEl = document.getElementById('results-count');

    if (!grid) return;
    if (section) section.style.display = 'block';
    if (countEl) countEl.textContent = results.length + ' files found';

    grid.innerHTML = '';
    scanState.selectedFiles.clear();

    results.forEach(file => {
        const card = document.createElement('div');
        card.className = 'result-card';
        card.dataset.id = file.id;

        const thumbHtml = file.category === 'image'
            ? `<img src="/api/thumbnail/${file.id}" onerror="this.parentElement.innerHTML='<div class=\\'no-thumb\\'>${file.icon}</div>'">`
            : `<div class="no-thumb">${file.icon}</div>`;

        card.innerHTML = `
            <input type="checkbox" class="result-select" data-id="${file.id}" onclick="event.stopPropagation(); toggleSelect('${file.id}')">
            <div class="result-thumb">${thumbHtml}</div>
            <div class="result-info">
                <span class="result-type" style="background:${file.color}20; color:${file.color}">${file.extension.toUpperCase()}</span>
                <div class="result-size">${file.size_human}</div>
                <div class="result-offset">${file.offset_hex}</div>
            </div>
        `;

        card.addEventListener('click', () => toggleSelect(file.id));
        grid.appendChild(card);
    });
}

function toggleSelect(fileId) {
    const card = document.querySelector(`.result-card[data-id="${fileId}"]`);
    const cb = document.querySelector(`.result-select[data-id="${fileId}"]`);

    if (scanState.selectedFiles.has(fileId)) {
        scanState.selectedFiles.delete(fileId);
        if (card) card.classList.remove('selected');
        if (cb) cb.checked = false;
    } else {
        scanState.selectedFiles.add(fileId);
        if (card) card.classList.add('selected');
        if (cb) cb.checked = true;
    }

    updateRecoverButtons();
}

function selectAll() {
    document.querySelectorAll('.result-card').forEach(card => {
        const id = card.dataset.id;
        scanState.selectedFiles.add(id);
        card.classList.add('selected');
        const cb = card.querySelector('.result-select');
        if (cb) cb.checked = true;
    });
    updateRecoverButtons();
}

function selectNone() {
    scanState.selectedFiles.clear();
    document.querySelectorAll('.result-card').forEach(card => {
        card.classList.remove('selected');
        const cb = card.querySelector('.result-select');
        if (cb) cb.checked = false;
    });
    updateRecoverButtons();
}

function updateRecoverButtons() {
    const btn = document.getElementById('btn-recover-selected');
    if (btn) {
        btn.textContent = `Recover Selected (${scanState.selectedFiles.size})`;
        btn.disabled = scanState.selectedFiles.size === 0;
    }
}

function recoverSelected() {
    if (scanState.selectedFiles.size === 0) return;
    const outputDir = document.getElementById('output-dir')?.value || '';

    fetch('/api/recover', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            file_ids: Array.from(scanState.selectedFiles),
            output_dir: outputDir || undefined,
        }),
    })
    .then(r => r.json())
    .then(data => {
        alert(`Recovery complete!\n✅ ${data.succeeded} succeeded\n❌ ${data.failed} failed\n\nOutput: ${data.output_dir}`);
    })
    .catch(err => alert('Recovery failed: ' + err));
}

function recoverAll() {
    if (!confirm('Recover all found files?')) return;
    const outputDir = document.getElementById('output-dir')?.value || '';

    fetch('/api/recover/all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ output_dir: outputDir || undefined }),
    })
    .then(r => r.json())
    .then(data => {
        alert(`Recovery complete!\n✅ ${data.succeeded} succeeded\n❌ ${data.failed} failed\n\nOutput: ${data.output_dir}`);
    })
    .catch(err => alert('Recovery failed: ' + err));
}

// ─── Drive Selection ────────────────────────────────────

function selectDrive(path) {
    const input = document.getElementById('source-path');
    if (input) input.value = path;

    document.querySelectorAll('.drive-item').forEach(item => {
        item.classList.toggle('selected', item.dataset.path === path);
    });
}

// ─── File Type Checkboxes ───────────────────────────────

function toggleTypeCheckbox(el) {
    el.classList.toggle('checked');
    const cb = el.querySelector('input[type="checkbox"]');
    if (cb) cb.checked = el.classList.contains('checked');
}

function selectAllTypes() {
    document.querySelectorAll('.type-checkbox').forEach(el => {
        el.classList.add('checked');
        const cb = el.querySelector('input[type="checkbox"]');
        if (cb) cb.checked = true;
    });
}

function selectNoTypes() {
    document.querySelectorAll('.type-checkbox').forEach(el => {
        el.classList.remove('checked');
        const cb = el.querySelector('input[type="checkbox"]');
        if (cb) cb.checked = false;
    });
}

// ─── Settings ───────────────────────────────────────────

function saveSettings() {
    const data = {
        output_dir: document.getElementById('settings-output-dir')?.value || '',
        chunk_size: parseInt(document.getElementById('settings-chunk-size')?.value || '512'),
        read_buffer_mb: parseInt(document.getElementById('settings-buffer-mb')?.value || '4'),
    };

    fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'updated') {
            alert('Settings saved!');
        }
    })
    .catch(err => alert('Failed to save settings: ' + err));
}

// ─── Helpers ────────────────────────────────────────────

function updateStatusBadge(status) {
    const badge = document.getElementById('global-status');
    if (badge) {
        badge.className = 'status-badge ' + status;
        badge.textContent = status.charAt(0).toUpperCase() + status.slice(1);
    }
}

function setTextById(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function formatTime(seconds) {
    if (!seconds || seconds < 0) return '—';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
}

// ─── Init ───────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function() {
    // Check if there's an active scan
    fetch('/api/scan/progress')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'scanning' || data.status === 'paused') {
                showProgress();
                startProgressStream();
            } else if (data.status === 'complete' && data.total_files > 0) {
                loadResults();
            }
        });
});
