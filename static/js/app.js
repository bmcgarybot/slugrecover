/* SlugRecover — UI Logic */

let scanState = { status: 'idle', eventSource: null, selectedFiles: new Set() };

// ─── Scan ───────────────────────────────────────────────

function startScan() {
    const source = val('source-path');
    if (!source) return showAlert('Enter a source path or click a drive.');

    const types = [];
    document.querySelectorAll('.type-chip.checked input').forEach(cb => types.push(cb.value));
    if (!types.length) return showAlert('Select at least one file type.');

    const btn = document.getElementById('btn-start');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Starting...'; }

    post('/api/scan/start', { source, file_types: types, output_dir: val('output-dir') || undefined })
        .then(data => {
            if (data.error) { showAlert(data.error); resetStartBtn(); return; }
            showProgress();
            startStream();
        })
        .catch(e => { showAlert('Failed: ' + e); resetStartBtn(); });
}

function resetStartBtn() {
    const btn = document.getElementById('btn-start');
    if (btn) { btn.disabled = false; btn.textContent = '🔍 Start Scan'; }
}

function pauseScan() { post('/api/scan/pause').then(() => { badge('paused'); setText('progress-title', '⏸ Paused'); }); }
function resumeScan() { post('/api/scan/resume').then(() => { badge('scanning'); setText('progress-title', '🔍 Scanning...'); }); }

function cancelScan() {
    if (!confirm('Cancel scan?')) return;
    post('/api/scan/cancel').then(() => {
        badge('cancelled');
        setText('progress-title', '✕ Cancelled');
        if (scanState.eventSource) { scanState.eventSource.close(); scanState.eventSource = null; }
    });
}

// ─── Progress Stream ────────────────────────────────────

function startStream() {
    if (scanState.eventSource) scanState.eventSource.close();
    scanState.eventSource = new EventSource('/api/scan/progress/stream');
    scanState.eventSource.onmessage = e => {
        const d = JSON.parse(e.data);
        updateProgress(d);
        if (['complete','cancelled','error'].includes(d.status)) {
            scanState.eventSource.close();
            scanState.eventSource = null;
            if (d.status === 'complete') {
                setText('progress-title', '✅ Done — ' + d.total_files + ' files found');
                loadResults();
            }
        }
    };
    scanState.eventSource.onerror = () => { scanState.eventSource.close(); scanState.eventSource = null; };
}

function updateProgress(d) {
    badge(d.status);
    const bar = document.getElementById('progress-bar');
    const txt = document.getElementById('progress-text');
    if (bar) { bar.style.width = d.percent + '%'; bar.className = 'progress-bar ' + (d.status === 'scanning' ? 'scanning' : ''); }
    if (txt) txt.textContent = d.percent.toFixed(1) + '%';

    setText('stat-files', d.total_files);
    setText('stat-speed', d.speed);
    setText('stat-elapsed', fmtTime(d.elapsed));
    setText('stat-eta', d.eta ? fmtTime(d.eta) : '—');
    setText('stat-scanned', fmtBytes(d.scanned_bytes) + ' of ' + fmtBytes(d.total_bytes));

    const fg = document.getElementById('found-grid');
    if (fg && d.files_found) {
        const entries = Object.entries(d.files_found);
        if (entries.length) {
            fg.innerHTML = '';
            entries.forEach(([ext, n]) => {
                fg.innerHTML += `<div class="found-chip">${ext.toUpperCase()} <span class="count">${n}</span></div>`;
            });
        }
    }

    if (d.error) { const el = document.getElementById('scan-error'); if (el) { el.textContent = '❌ ' + d.error; el.style.display = 'block'; } }

    show('btn-pause', d.status === 'scanning');
    show('btn-resume', d.status === 'paused');
    show('btn-cancel', ['scanning','paused'].includes(d.status));
}

function showProgress() {
    const s = document.getElementById('scan-setup');
    const p = document.getElementById('scan-progress');
    if (s) s.style.display = 'none';
    if (p) { p.style.display = 'block'; p.classList.add('active'); }
}

// ─── Results ────────────────────────────────────────────

function loadResults() {
    fetch('/api/scan/results').then(r => r.json()).then(d => { if (d.results?.length) showResults(d.results); });
}

function showResults(results) {
    const grid = document.getElementById('results-grid');
    const section = document.getElementById('results-section');
    if (!grid) return;
    if (section) section.style.display = 'block';
    setText('results-count', results.length);
    grid.innerHTML = '';
    scanState.selectedFiles.clear();

    results.forEach(f => {
        const card = document.createElement('div');
        card.className = 'result-card';
        card.dataset.id = f.id;
        const thumb = f.category === 'image'
            ? `<img src="/api/thumbnail/${f.id}" onerror="this.parentElement.innerHTML='<div class=\\'no-thumb\\'>${f.icon}</div>'">`
            : `<div class="no-thumb">${f.icon}</div>`;
        card.innerHTML = `
            <div class="result-select" onclick="event.stopPropagation();toggleSelect('${f.id}')"></div>
            <div class="result-thumb">${thumb}</div>
            <div class="result-info">
                <span class="result-type-badge" style="background:${f.color}15;color:${f.color}">${f.extension.toUpperCase()}</span>
                <div class="result-size">${f.size_human}</div>
                <div class="result-offset">${f.offset_hex}</div>
            </div>`;
        card.addEventListener('click', () => toggleSelect(f.id));
        grid.appendChild(card);
    });
}

function toggleSelect(id) {
    const card = document.querySelector(`.result-card[data-id="${id}"]`);
    if (scanState.selectedFiles.has(id)) { scanState.selectedFiles.delete(id); card?.classList.remove('selected'); }
    else { scanState.selectedFiles.add(id); card?.classList.add('selected'); }
    updRecBtn();
}

function selectAll() { document.querySelectorAll('.result-card').forEach(c => { scanState.selectedFiles.add(c.dataset.id); c.classList.add('selected'); }); updRecBtn(); }
function selectNone() { scanState.selectedFiles.clear(); document.querySelectorAll('.result-card').forEach(c => c.classList.remove('selected')); updRecBtn(); }

function updRecBtn() {
    const btn = document.getElementById('btn-recover-selected');
    if (btn) { const n = scanState.selectedFiles.size; btn.innerHTML = `💾 Recover (${n})`; btn.disabled = n === 0; }
}

function recoverSelected() {
    if (!scanState.selectedFiles.size) return;
    const btn = document.getElementById('btn-recover-selected');
    if (btn) { btn.disabled = true; btn.textContent = '⏳...'; }
    post('/api/recover', { file_ids: [...scanState.selectedFiles], output_dir: val('output-dir') || undefined })
        .then(d => { showAlert(`✅ ${d.succeeded} recovered, ${d.failed} failed\n${d.output_dir}`); updRecBtn(); })
        .catch(e => { showAlert('Failed: ' + e); updRecBtn(); });
}

function recoverAll() {
    if (!confirm('Recover all files?')) return;
    post('/api/recover/all', { output_dir: val('output-dir') || undefined })
        .then(d => showAlert(`✅ ${d.succeeded} recovered, ${d.failed} failed\n${d.output_dir}`))
        .catch(e => showAlert('Failed: ' + e));
}

// ─── Drive Selection ────────────────────────────────────

function selectDrive(path) {
    document.getElementById('source-path').value = path;
    document.querySelectorAll('.drive-chip').forEach(c => c.classList.toggle('selected', c.title === path));
}

// ─── File Types ─────────────────────────────────────────

function toggleFileTypes() {
    const panel = document.getElementById('type-panel');
    const arrow = document.getElementById('type-arrow');
    if (!panel) return;
    const open = panel.classList.contains('expanded');
    panel.classList.toggle('collapsed', !open);
    panel.classList.toggle('expanded', open ? false : true);
    arrow?.classList.toggle('open', !open);
}

function toggleType(el) {
    if (el.tagName === 'INPUT') { el = el.closest('.type-chip'); }
    el.classList.toggle('checked');
    const cb = el.querySelector('input');
    if (cb) cb.checked = el.classList.contains('checked');
    updTypeSummary();
}

// Attach click handlers
document.addEventListener('click', e => {
    const chip = e.target.closest('.type-chip');
    if (chip && chip.querySelector('input')) { e.preventDefault(); toggleType(chip); }
});

function selectAllTypes() { document.querySelectorAll('.type-chip').forEach(e => { e.classList.add('checked'); e.querySelector('input').checked = true; }); updTypeSummary(); }
function selectNoTypes() { document.querySelectorAll('.type-chip').forEach(e => { e.classList.remove('checked'); e.querySelector('input').checked = false; }); updTypeSummary(); }

function selectCategory(cat) {
    selectNoTypes();
    document.querySelectorAll(`.type-chip[data-category="${cat}"]`).forEach(e => { e.classList.add('checked'); e.querySelector('input').checked = true; });
    updTypeSummary();
}

function updTypeSummary() {
    const total = document.querySelectorAll('.type-chip').length;
    const checked = document.querySelectorAll('.type-chip.checked').length;
    const el = document.getElementById('type-summary');
    if (el) el.textContent = checked === total ? `All ${total} types` : `${checked} of ${total} selected`;
}

// ─── File Browser ───────────────────────────────────────

let browserTarget = null; // 'source' or 'output'
let browserSelected = '';

function openBrowser(target) {
    browserTarget = target;
    document.getElementById('browser-panel').style.display = 'block';
    // Start at current value or default
    const current = val(target === 'output' ? 'output-dir' : 'source-path');
    browseTo(current || '');
}

function closeBrowser() {
    document.getElementById('browser-panel').style.display = 'none';
}

function browseTo(path) {
    fetch('/api/browse?path=' + encodeURIComponent(path))
        .then(r => r.json())
        .then(d => {
            if (d.is_file) { browserSelected = d.current; useBrowserPath(); return; }
            browserSelected = d.current;
            document.getElementById('browser-path').value = d.current;
            const list = document.getElementById('browser-list');
            list.innerHTML = '';
            d.items.forEach(item => {
                const el = document.createElement('div');
                el.className = 'browser-item';
                el.innerHTML = `<span class="bi-icon">${item.is_dir ? '📁' : '📄'}</span><span class="bi-name">${item.name}</span><span class="bi-size">${item.size_human}</span>`;
                el.addEventListener('click', () => {
                    list.querySelectorAll('.browser-item').forEach(i => i.classList.remove('selected'));
                    el.classList.add('selected');
                    browserSelected = item.path;
                });
                el.addEventListener('dblclick', () => {
                    if (item.is_dir) browseTo(item.path);
                    else { browserSelected = item.path; useBrowserPath(); }
                });
                list.appendChild(el);
            });
        });
}

function useBrowserPath() {
    const id = browserTarget === 'output' ? 'output-dir' : 'source-path';
    document.getElementById(id).value = browserSelected;
    closeBrowser();
}

// ─── Settings ───────────────────────────────────────────

function saveSettings() {
    post('/api/settings', {
        output_dir: val('settings-output-dir') || '',
        chunk_size: parseInt(val('settings-chunk-size') || '512'),
        read_buffer_mb: parseInt(val('settings-buffer-mb') || '4'),
    }).then(d => { if (d.status === 'updated') showAlert('✅ Saved!'); }).catch(e => showAlert('Error: ' + e));
}

// ─── Helpers ────────────────────────────────────────────

function post(url, body) { return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : '{}' }).then(r => r.json()); }
function val(id) { return (document.getElementById(id)?.value || '').trim(); }
function setText(id, t) { const e = document.getElementById(id); if (e) e.textContent = t; }
function show(id, v) { const e = document.getElementById(id); if (e) e.style.display = v ? '' : 'none'; }
function badge(s) { const b = document.getElementById('global-status'); if (!b) return; b.className = 'status-badge ' + s; b.textContent = {idle:'Ready',scanning:'Scanning',paused:'Paused',complete:'Done',cancelled:'Cancelled',error:'Error'}[s] || s; }
function showAlert(m) { alert(m); }
function fmtTime(s) { if (!s || s < 0) return '—'; const h=Math.floor(s/3600),m=Math.floor(s%3600/60),sec=Math.floor(s%60); return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`; }
function fmtBytes(b) { if (!b) return '0 B'; if (b<1024) return b+' B'; if (b<1048576) return (b/1024).toFixed(1)+' KB'; if (b<1073741824) return (b/1048576).toFixed(1)+' MB'; return (b/1073741824).toFixed(2)+' GB'; }

// ─── Init ───────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    fetch('/api/scan/progress').then(r => r.json()).then(d => {
        if (d.status === 'scanning' || d.status === 'paused') { showProgress(); startStream(); }
        else if (d.status === 'complete' && d.total_files > 0) { showProgress(); updateProgress(d); setText('progress-title', '✅ Done — ' + d.total_files + ' files'); loadResults(); }
    });
    updTypeSummary();
});
