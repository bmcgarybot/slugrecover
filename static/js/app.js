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
        showAlert('Please choose a drive or enter a path in Step 1.');
        return;
    }

    // Get selected file types
    const types = [];
    document.querySelectorAll('.type-card.checked input[type="checkbox"]').forEach(cb => {
        types.push(cb.value);
    });

    if (types.length === 0) {
        showAlert('Please select at least one file type in Step 2.');
        return;
    }

    const outputDir = document.getElementById('output-dir')?.value || '';

    // Disable start button
    const startBtn = document.getElementById('btn-start');
    if (startBtn) {
        startBtn.disabled = true;
        startBtn.innerHTML = '⏳ Starting...';
    }

    fetch('/api/scan/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            source: source,
            file_types: types,
            output_dir: outputDir || undefined,
        }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            showAlert('Error: ' + data.error);
            if (startBtn) {
                startBtn.disabled = false;
                startBtn.innerHTML = '🔍 Start Recovery Scan';
            }
            return;
        }
        showProgress();
        startProgressStream();
    })
    .catch(err => {
        showAlert('Failed to start scan: ' + err);
        if (startBtn) {
            startBtn.disabled = false;
            startBtn.innerHTML = '🔍 Start Recovery Scan';
        }
    });
}

function pauseScan() {
    fetch('/api/scan/pause', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
            updateStatusBadge('paused');
            setText('progress-title', 'Scan Paused');
            setText('progress-subtitle', 'Click Resume to continue scanning.');
        });
}

function resumeScan() {
    fetch('/api/scan/resume', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
            updateStatusBadge('scanning');
            setText('progress-title', 'Scanning for files...');
            setText('progress-subtitle', 'This can take a while for large drives. You can pause anytime.');
        });
}

function cancelScan() {
    if (!confirm('Are you sure you want to cancel the scan?')) return;
    fetch('/api/scan/cancel', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
            updateStatusBadge('cancelled');
            setText('progress-title', 'Scan Cancelled');
            setText('progress-subtitle', 'Go back to the Dashboard to start a new scan.');
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

        if (['complete', 'cancelled', 'error'].includes(data.status)) {
            scanState.eventSource.close();
            scanState.eventSource = null;

            if (data.status === 'complete') {
                loadResults();
                setText('progress-title', '✅ Scan Complete!');
                setText('progress-subtitle', `Found ${data.total_files} files. Scroll down to recover them.`);
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
        bar.className = 'progress-bar ' + (data.status === 'scanning' ? 'scanning' : '');
    }
    if (text) {
        text.textContent = data.percent.toFixed(1) + '%';
    }

    // Stats
    setText('stat-speed', data.speed);
    setText('stat-files', data.total_files.toString());
    setText('stat-elapsed', formatTime(data.elapsed));
    setText('stat-eta', data.eta ? formatTime(data.eta) : '—');
    setText('stat-scanned', formatBytes(data.scanned_bytes) + ' of ' + formatBytes(data.total_bytes) + ' scanned');

    // Found files chips
    const foundGrid = document.getElementById('found-grid');
    if (foundGrid && data.files_found) {
        const entries = Object.entries(data.files_found);
        if (entries.length > 0) {
            foundGrid.innerHTML = '';
            for (const [ext, count] of entries) {
                const chip = document.createElement('div');
                chip.className = 'found-chip';
                chip.innerHTML = `${ext.toUpperCase()} <span class="count">${count}</span>`;
                foundGrid.appendChild(chip);
            }
        }
    }

    // Error display
    if (data.error) {
        const errEl = document.getElementById('scan-error');
        if (errEl) {
            errEl.textContent = '❌ ' + data.error;
            errEl.style.display = 'block';
        }
    }

    // Button visibility
    const pauseBtn = document.getElementById('btn-pause');
    const resumeBtn = document.getElementById('btn-resume');
    const cancelBtn = document.getElementById('btn-cancel');

    if (pauseBtn) pauseBtn.style.display = data.status === 'scanning' ? '' : 'none';
    if (resumeBtn) resumeBtn.style.display = data.status === 'paused' ? '' : 'none';
    if (cancelBtn) cancelBtn.style.display = ['scanning', 'paused'].includes(data.status) ? '' : 'none';

    // If complete with results
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
            if (data.results && data.results.length > 0) {
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
    if (countEl) countEl.textContent = results.length;

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
            <div class="result-select" onclick="event.stopPropagation(); toggleSelect('${file.id}')"></div>
            <div class="result-thumb">${thumbHtml}</div>
            <div class="result-info">
                <span class="result-type-badge" style="background:${file.color}15; color:${file.color}">${file.extension.toUpperCase()}</span>
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

    if (scanState.selectedFiles.has(fileId)) {
        scanState.selectedFiles.delete(fileId);
        if (card) card.classList.remove('selected');
    } else {
        scanState.selectedFiles.add(fileId);
        if (card) card.classList.add('selected');
    }

    updateRecoverButtons();
}

function selectAll() {
    document.querySelectorAll('.result-card').forEach(card => {
        scanState.selectedFiles.add(card.dataset.id);
        card.classList.add('selected');
    });
    updateRecoverButtons();
}

function selectNone() {
    scanState.selectedFiles.clear();
    document.querySelectorAll('.result-card').forEach(card => {
        card.classList.remove('selected');
    });
    updateRecoverButtons();
}

function updateRecoverButtons() {
    const btn = document.getElementById('btn-recover-selected');
    if (btn) {
        const count = scanState.selectedFiles.size;
        btn.innerHTML = `💾 Recover Selected (${count})`;
        btn.disabled = count === 0;
    }
}

function recoverSelected() {
    if (scanState.selectedFiles.size === 0) return;
    const outputDir = document.getElementById('output-dir')?.value || '';

    const btn = document.getElementById('btn-recover-selected');
    if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Recovering...'; }

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
        showAlert(`✅ Recovery complete!\n\n${data.succeeded} files saved\n${data.failed} failed\n\nSaved to: ${data.output_dir}`);
        updateRecoverButtons();
    })
    .catch(err => {
        showAlert('Recovery failed: ' + err);
        updateRecoverButtons();
    });
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
        showAlert(`✅ Recovery complete!\n\n${data.succeeded} files saved\n${data.failed} failed\n\nSaved to: ${data.output_dir}`);
    })
    .catch(err => showAlert('Recovery failed: ' + err));
}

// ─── Drive Selection ────────────────────────────────────

function selectDrive(path) {
    const input = document.getElementById('source-path');
    if (input) input.value = path;

    document.querySelectorAll('.drive-item').forEach(item => {
        item.classList.toggle('selected', item.dataset.path === path);
    });
}

// ─── File Type Controls ─────────────────────────────────

function toggleType(el) {
    el.classList.toggle('checked');
    const cb = el.querySelector('input[type="checkbox"]');
    if (cb) cb.checked = el.classList.contains('checked');
}

function selectAllTypes() {
    document.querySelectorAll('.type-card').forEach(el => {
        el.classList.add('checked');
        const cb = el.querySelector('input[type="checkbox"]');
        if (cb) cb.checked = true;
    });
}

function selectNoTypes() {
    document.querySelectorAll('.type-card').forEach(el => {
        el.classList.remove('checked');
        const cb = el.querySelector('input[type="checkbox"]');
        if (cb) cb.checked = false;
    });
}

function selectCategory(category) {
    // Uncheck all first
    selectNoTypes();
    // Check only the category
    document.querySelectorAll(`.type-card[data-category="${category}"]`).forEach(el => {
        el.classList.add('checked');
        const cb = el.querySelector('input[type="checkbox"]');
        if (cb) cb.checked = true;
    });
}

// ─── File Browser ───────────────────────────────────────

let browserState = {
    target: null, // 'source' or 'output'
    currentPath: '',
    selectedPath: '',
};

function openFileBrowser(target) {
    browserState.target = target;
    const browser = document.getElementById('file-browser');
    if (browser) {
        browser.style.display = 'block';
        navigateBrowser(''); // Start at default location
    }
}

function closeFileBrowser() {
    const browser = document.getElementById('file-browser');
    if (browser) browser.style.display = 'none';
}

function navigateBrowser(path) {
    fetch('/api/browse?path=' + encodeURIComponent(path))
        .then(r => r.json())
        .then(data => {
            if (data.error && (!data.items || data.items.length === 0)) {
                showAlert(data.error);
                return;
            }

            browserState.currentPath = data.current;
            browserState.selectedPath = data.current;

            const pathInput = document.getElementById('browser-path-input');
            if (pathInput) pathInput.value = data.current;

            const list = document.getElementById('browser-list');
            if (!list) return;

            list.innerHTML = '';

            if (data.is_file) {
                // Selected a file — auto-select it
                browserState.selectedPath = data.current;
                selectBrowserPath();
                return;
            }

            data.items.forEach(item => {
                const el = document.createElement('div');
                el.className = 'browser-item';
                el.innerHTML = `
                    <span class="icon">${item.is_dir ? '📁' : '📄'}</span>
                    <span class="name">${item.name}</span>
                    <span class="size">${item.size_human}</span>
                `;

                if (item.is_dir) {
                    el.addEventListener('dblclick', () => navigateBrowser(item.path));
                    el.addEventListener('click', () => {
                        list.querySelectorAll('.browser-item').forEach(i => i.classList.remove('selected'));
                        el.classList.add('selected');
                        browserState.selectedPath = item.path;
                        updateBrowserSelectBtn();
                    });
                } else {
                    el.addEventListener('click', () => {
                        list.querySelectorAll('.browser-item').forEach(i => i.classList.remove('selected'));
                        el.classList.add('selected');
                        browserState.selectedPath = item.path;
                        updateBrowserSelectBtn();
                    });
                    el.addEventListener('dblclick', () => {
                        browserState.selectedPath = item.path;
                        selectBrowserPath();
                    });
                }

                list.appendChild(el);
            });

            updateBrowserSelectBtn();
        })
        .catch(err => showAlert('Browse error: ' + err));
}

function updateBrowserSelectBtn() {
    const btn = document.getElementById('browser-select-btn');
    if (btn) btn.disabled = !browserState.selectedPath;
}

function selectBrowserPath() {
    const targetId = browserState.target === 'output' ? 'output-dir' : 'source-path';
    const input = document.getElementById(targetId);
    if (input) input.value = browserState.selectedPath;
    closeFileBrowser();
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
            showAlert('✅ Settings saved!');
        }
    })
    .catch(err => showAlert('Failed to save: ' + err));
}

// ─── Helpers ────────────────────────────────────────────

function updateStatusBadge(status) {
    const badge = document.getElementById('global-status');
    if (!badge) return;

    const labels = {
        idle: 'Ready',
        scanning: 'Scanning',
        paused: 'Paused',
        complete: 'Done',
        cancelled: 'Cancelled',
        error: 'Error',
    };

    badge.className = 'status-badge ' + status;
    badge.textContent = labels[status] || status;
}

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function showAlert(msg) {
    alert(msg);
}

function formatTime(seconds) {
    if (!seconds || seconds < 0) return '—';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}

function formatBytes(bytes) {
    if (!bytes) return '0 B';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
}

// ─── Init ───────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function() {
    // Check if there's an active or completed scan
    fetch('/api/scan/progress')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'scanning' || data.status === 'paused') {
                showProgress();
                startProgressStream();
            } else if (data.status === 'complete' && data.total_files > 0) {
                showProgress();
                updateProgress(data);
                setText('progress-title', '✅ Scan Complete!');
                setText('progress-subtitle', `Found ${data.total_files} files. Scroll down to recover them.`);
                loadResults();
            }
        });
});
