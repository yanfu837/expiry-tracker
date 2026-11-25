(() => {
    const root = document.querySelector('#batch-import');
    if (!root) return;
    const chooser = document.querySelector('#batch-files');
    const message = document.querySelector('#batch-message');
    const summary = document.querySelector('#batch-summary');
    const saveButton = document.querySelector('#save-confirmed');
    const refreshButton = document.querySelector('#refresh-batch');
    const tbody = document.querySelector('#batch-rows');
    const csrf = root.querySelector('[name=csrfmiddlewaretoken]').value;
    const rows = new Map(), sources = new Map(), queue = [];
    let batch = null, processing = false, saving = false;
    const labels = {waiting: 'Waiting', processing: 'Scanning and extracting', ready: 'Ready to review', failed: 'Processing failed',
        saving: 'Saving', saved: 'Saved', skipped: 'Skipped'};

    async function api(url, data) {
        const options = {credentials: 'same-origin', headers: {'X-CSRFToken': csrf}};
        if (data !== undefined) Object.assign(options, {method: 'POST', body: JSON.stringify(data),
            headers: {...options.headers, 'Content-Type': 'application/json'}});
        const response = await fetch(url, options);
        if (response.redirected) throw new Error('Your session has expired. Please log in again.');
        const payload = await response.json().catch(() => ({message: 'The request did not finish. Refresh or try again later.'}));
        return {ok: response.ok, data: payload};
    }

    function valid(row) {
        return row.nameInput.value.trim() && row.dateInput.value && row.dateInput.validity.valid && row.notesInput.validity.valid;
    }
    function reviewable(row) {
        return !batch?.expired && !row.saved && !row.active && !row.queued && !row.busy
            && ['ready', 'saving'].includes(row.status) && valid(row);
    }
    function updateSummary() {
        const all = [...rows.values()];
        const confirmed = all.filter(row => row.confirmed && reviewable(row));
        const saved = all.filter(row => row.saved).length;
        const completedFiles = new Set(all.filter(row => !['waiting', 'processing'].includes(row.status) && !row.queued).map(row => row.source_key)).size;
        const totalFiles = new Set(all.map(row => row.source_key)).size;
        summary.textContent = all.length ? `Files processed ${completedFiles}/${totalFiles}; ${all.length} expiry items, ${confirmed.length} confirmed, ${saved} saved.` : 'No files selected.';
        saveButton.textContent = `Save confirmed items (${confirmed.length})`;
        saveButton.disabled = !confirmed.length || processing || saving;
        refreshButton.disabled = !batch || processing || saving;
    }
    function renderRow(row) {
        row.node.querySelector('.source-filename').textContent = row.filename;
        row.node.querySelector('.row-status').textContent = row.phase || (row.queued ? 'Waiting' : labels[row.status] || row.status);
        row.node.querySelector('.row-message').textContent = row.message || (!sources.has(row.source_key) && !row.saved ? 'Reselect this file before extraction or saving.' : '');
        row.node.querySelector('.date-evidence').textContent = row.evidence || 'No clear expiry date source was found. Check the document.';
        const warnings = row.node.querySelector('.candidate-warnings');
        warnings.replaceChildren(...(row.warnings || []).map(text => {const li = document.createElement('li'); li.textContent = text; return li;}));
        row.confirmInput.checked = row.confirmed;
        row.confirmInput.disabled = !reviewable(row) || saving;
        for (const input of [row.nameInput, row.dateInput, row.notesInput]) input.disabled = row.saved || (row.active && saving) || !!batch?.expired;
        row.node.querySelector('.retry-candidate').disabled = row.saved || row.active || row.queued || saving || !!batch?.expired;
        row.node.querySelector('.skip-candidate').disabled = row.saved || row.active || row.busy || saving || !!batch?.expired;
        row.node.querySelector('.duplicate-candidate').disabled = !['ready', 'saved'].includes(row.status) || row.active || saving || !!batch?.expired;
        row.fileInput.disabled = row.saved || row.active || saving || !!batch?.expired;
        row.node.querySelector('.reselect-candidate').disabled = row.fileInput.disabled;
        const link = row.node.querySelector('.saved-item');
        link.hidden = !row.item_url;
        if (row.item_url) link.href = row.item_url;
        updateSummary();
    }
    function applyCandidate(data, invalidate = false) {
        let row = rows.get(data.id);
        if (!row) {
            const node = document.querySelector('#candidate-row-template').content.firstElementChild.cloneNode(true);
            row = {dirty: new Set(), confirmed: false, active: false, queued: false, phase: '', node};
            row.nameInput = node.querySelector('.candidate-name');
            row.dateInput = node.querySelector('.candidate-date');
            row.notesInput = node.querySelector('.candidate-notes');
            row.confirmInput = node.querySelector('.candidate-confirm');
            row.fileInput = node.querySelector('.reselect-file');
            node.querySelector('.reselect-candidate').addEventListener('click', () => row.fileInput.click());
            node.dataset.candidateId = data.id;
            for (const [key, input, label] of [['name', row.nameInput, '.name-label'], ['expiry_date', row.dateInput, '.date-label'], ['notes', row.notesInput, '.notes-label']]) {
                input.id = `${key}-${data.id}`;
                node.querySelector(label).htmlFor = input.id;
                input.addEventListener('input', () => {row.dirty.add(key); row.confirmed = false; renderRow(row);});
            }
            row.confirmInput.addEventListener('change', () => {row.confirmed = row.confirmInput.checked && reviewable(row); updateSummary();});
            row.fileInput.addEventListener('change', () => {
                const file = row.fileInput.files[0];
                if (!file) return;
                if (!file.size || file.size > 10*1024*1024) {
                    row.message = 'The file is empty or exceeds 10 MB. Nothing was uploaded. Choose another file or create an item without a document.';
                    row.fileInput.value = ''; renderRow(row); return;
                }
                if (file.size !== row.size || file.name.split('.').pop().toLowerCase() !== row.filename.split('.').pop().toLowerCase()) {
                    row.message = 'Reselect the original file with the same size and format, or start a new batch. Nothing was uploaded.';
                    row.fileInput.value = ''; renderRow(row); return;
                }
                sources.set(row.source_key, file);
                for (const related of rows.values()) if (related.source_key === row.source_key) related.confirmed = false;
                enqueue(row);
            });
            node.querySelector('.retry-candidate').addEventListener('click', async () => {
                try {await refresh(); if (!row.saved && !row.busy) enqueue(row); else if (row.busy) {row.message = 'Still processing. Check the status again shortly.'; renderRow(row);}}
                catch (error) {message.textContent = error.message;}
            });
            for (const [selector, action] of [['.skip-candidate', 'skip'], ['.duplicate-candidate', 'duplicate']]) {
                node.querySelector(selector).addEventListener('click', async () => {
                    if (action === 'skip') row.queued = false;
                    row.active = true; renderRow(row);
                    try {
                        const result = await api(row.urls[action], {});
                        if (!result.ok) throw new Error(result.data.message);
                        if (action === 'skip') row.queued = false;
                        applyCandidate(result.data.candidate, true);
                    } catch (error) {row.message = error.message;}
                    finally {row.active = false; renderRow(row);}
                });
            }
            rows.set(data.id, row); tbody.append(node);
        }
        Object.assign(row, data);
        if (invalidate || row.saved || !['ready', 'saving'].includes(row.status)) row.confirmed = false;
        for (const [key, input] of [['name', row.nameInput], ['expiry_date', row.dateInput], ['notes', row.notesInput]]) {
            if (row.saved || !row.dirty.has(key)) {
                const value = data[key] || '';
                if (input.value !== value) row.confirmed = false;
                input.value = value;
            }
        }
        renderRow(row);
        return row;
    }
    function upload(row, action, data) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open('POST', row.urls[action]); xhr.timeout = 125000;
            xhr.setRequestHeader('X-CSRFToken', csrf);
            xhr.upload.onprogress = event => {if (event.lengthComputable) {row.phase = `Uploading ${Math.round(event.loaded/event.total*100)}%`; renderRow(row);}};
            xhr.upload.onload = () => {row.phase = action === 'save' ? 'Saving' : 'Scanning and extracting'; renderRow(row);};
            xhr.onload = () => {
                try {resolve({ok: xhr.status >= 200 && xhr.status < 300, data: JSON.parse(xhr.responseText)});}
                catch (_) {reject(new Error('Your session or request may have expired. Check the status again.'));}
            };
            xhr.onerror = xhr.ontimeout = () => reject(new Error('The connection did not finish. Check the status and retry this row; saved items will not be duplicated.'));
            xhr.send(data);
        });
    }
    function enqueue(row) {
        if (row.saved || row.active || row.queued || batch?.expired) return;
        const file = sources.get(row.source_key);
        if (!file) {row.fileInput.click(); return;}
        if (!file.size || file.size > 10*1024*1024) {row.message = 'Choose a non-empty file up to 10 MB, or skip this row.'; renderRow(row); return;}
        if (row.status === 'skipped') row.status = 'waiting';
        row.confirmed = false; row.queued = true; row.message = ''; queue.push(row); renderRow(row);
        void processQueue();
    }
    async function processQueue() {
        if (processing) return;
        processing = true; updateSummary();
        while (queue.length) {
            const row = queue.shift();
            if (!row.queued || row.saved || row.status === 'skipped') continue;
            row.queued = false; row.active = true; row.phase = 'Starting'; renderRow(row);
            try {
                const data = new FormData(); data.append('document', sources.get(row.source_key));
                const result = await upload(row, 'analyze', data);
                if (result.data.candidate) applyCandidate(result.data.candidate, true);
                if (!result.ok) throw new Error(result.data.message || 'Extraction did not finish. Retry this document.');
            } catch (error) {row.message = error.message; if (!row.saved && !row.busy) row.status = 'failed';}
            finally {row.active = false; row.phase = ''; renderRow(row);}
        }
        processing = false; updateSummary();
    }
    async function refresh() {
        if (!batch) return;
        const result = await api(batch.status_url);
        if (!result.ok) throw new Error(result.data.message || 'This batch could not be loaded.');
        batch = result.data;
        for (const candidate of batch.candidates) applyCandidate(candidate);
        if (batch.expired) message.textContent = 'This draft batch has expired. Saved items remain in your list. Start a new batch.';
    }
    chooser.addEventListener('change', async () => {
        const files = [...chooser.files];
        if (!files.length) return;
        if (files.length > Number(root.dataset.maxFiles)) {message.textContent = `Choose up to ${root.dataset.maxFiles} files per batch. Nothing was uploaded.`; chooser.value = ''; return;}
        const oversized = files.filter(file => !file.size || file.size > 10*1024*1024);
        if (oversized.length) {message.textContent = `These files are empty or exceed 10 MB: ${oversized.map(file => file.name).join(', ')}. Nothing was uploaded. Choose different files or create an item without a document.`; chooser.value = ''; return;}
        if (files.reduce((sum, file) => sum + file.size, 0) > Number(root.dataset.maxTotalBytes)) {
            message.textContent = 'These files exceed 50 MB in total. Nothing was uploaded. Choose fewer files or create an item without a document.'; chooser.value = ''; return;
        }
        chooser.disabled = true; message.textContent = 'Preparing your review table…';
        const metadata = files.map(file => {const key = crypto.randomUUID(); sources.set(key, file); return {key, name: file.name, size: file.size};});
        try {
            const result = await api(root.dataset.createUrl, {files: metadata});
            if (!result.ok) throw new Error(result.data.message);
            batch = result.data; history.replaceState(null, '', batch.url);
            for (const candidate of batch.candidates) applyCandidate(candidate);
            message.textContent = 'Your review table is ready. Files will be scanned and extracted one at a time. Review and confirm each item.';
            for (const row of rows.values()) if (row.status === 'waiting') enqueue(row);
        } catch (error) {message.textContent = error.message; chooser.disabled = false; chooser.value = ''; sources.clear();}
    });
    refreshButton.addEventListener('click', () => {void refresh().catch(error => {message.textContent = error.message;});});
    saveButton.addEventListener('click', async () => {
        if (saving || processing) return;
        saving = true; updateSummary();
        try {
            await refresh();
            const selected = [...rows.values()].filter(row => row.confirmed && reviewable(row));
            for (const row of selected) {
                if (!row.confirmed || !reviewable(row)) continue;
                const file = sources.get(row.source_key);
                if (!file) {row.message = 'Reselect this file. Saved items will not be created again.'; renderRow(row); continue;}
                row.active = true; row.phase = 'Saving'; renderRow(row);
                try {
                    const data = new FormData(); data.append('document', file); data.append('confirmed', 'true');
                    data.append('name', row.nameInput.value); data.append('expiry_date', row.dateInput.value); data.append('notes', row.notesInput.value);
                    const result = await upload(row, 'save', data);
                    if (result.data.candidate) applyCandidate(result.data.candidate);
                    if (!result.ok) throw new Error(result.data.message || 'This item was not saved. You can retry it individually.');
                } catch (error) {row.message = error.message;}
                finally {row.active = false; row.phase = ''; renderRow(row);}
            }
            message.textContent = 'Saving finished. Check each result; failed and unconfirmed items remain in the review table.';
        } catch (error) {message.textContent = error.message;}
        finally {saving = false; for (const row of rows.values()) renderRow(row);}
    });
    const initial = JSON.parse(document.querySelector('#initial-import-batch').textContent);
    if (initial) {batch = initial; chooser.disabled = true; for (const candidate of batch.candidates) applyCandidate(candidate);}
    updateSummary();
})();
