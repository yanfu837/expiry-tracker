(() => {
    const assistant = document.querySelector('[data-analyze-url]');
    const upload = document.querySelector('#id_document');
    if (!assistant || !upload) return;
    const form = upload.form;
    const token = form.querySelector('[name="scan_token"]');
    const status = document.querySelector('#document-analysis-status');
    const warnings = document.querySelector('#document-analysis-warnings');
    const evidence = document.querySelector('#document-analysis-evidence');
    const retry = document.querySelector('#analyze-document');
    const dirty = new Set();
    const fields = ['name', 'expiry_date', 'notes'];
    for (const key of fields) {
        form.elements[key]?.addEventListener('input', () => dirty.add(key));
        if (form.elements[key]?.value) dirty.add(key); // Keep saved item fields.
    }
    let controller;
    let pending = false;
    let blocked = false;
    async function analyze() {
        const file = upload.files[0];
        controller?.abort();
        controller = null;
        token.value = '';
        warnings.replaceChildren();
        evidence.textContent = '';
        if (!file) { pending = false; blocked = false; status.textContent = ''; return; }
        if (!file.size || file.size > 10 * 1024 * 1024) {
            pending = false;
            blocked = true;
            status.textContent = 'The file is empty or exceeds 10 MB. Choose another file or save without a document.';
            if (retry) retry.disabled = true;
            return;
        }
        if (assistant.dataset.available !== 'true') {
            blocked = true;
            status.textContent = 'Private scanning is not enabled. Remove the new document before saving.';
            return;
        }
        controller = new AbortController();
        const current = controller;
        pending = true;
        blocked = true;
        retry.disabled = true;
        status.textContent = 'Scanning and extracting on the private server…';
        const body = new FormData();
        body.append('document', file);
        try {
            const response = await fetch(assistant.dataset.analyzeUrl, {
                method: 'POST', body, signal: current.signal, credentials: 'same-origin',
                headers: {'X-CSRFToken': form.querySelector('[name="csrfmiddlewaretoken"]').value},
            });
            if (response.redirected) throw new Error('Your session has expired. Log in and try again.');
            const result = await response.json().catch(() => ({message: 'Request verification has expired. Refresh and try again.'}));
            if (current !== controller) return;
            if (!response.ok) throw new Error(result.message || 'Scanning and extraction did not finish. The file was not saved.');
            if (!result.fields || !result.scan_token) throw new Error('The extracted result could not be used. Please retry extraction.');
            for (const key of fields) {
                if (!dirty.has(key) && result.fields[key] != null) form.elements[key].value = result.fields[key];
            }
            for (const warning of result.fields.warnings) {
                const li = document.createElement('li');
                li.textContent = warning;
                warnings.append(li);
            }
            if (result.fields.evidence) evidence.textContent = 'Expiry date source: ' + result.fields.evidence;
            token.value = result.scan_token;
            blocked = false;
            status.textContent = 'Scan passed. Suggestions filled the available blank fields; existing and edited values were kept. Review before saving.';
        } catch (error) {
            if (error.name === 'AbortError' || current !== controller) return;
            status.textContent = error.message || 'Scanning did not finish. The file was not saved.';
        } finally {
            if (current === controller) {
                pending = false;
                retry.disabled = !upload.files.length;
            }
        }
    }
    upload.addEventListener('change', analyze);
    retry?.addEventListener('click', analyze);
    form.addEventListener('submit', (event) => {
        if (upload.files.length && (pending || blocked)) {
            event.preventDefault();
            status.textContent = pending ? 'Scanning and extraction are still running. Please wait.' : 'The file has not passed scanning. Retry extraction or remove the document.';
            status.scrollIntoView({block: 'center'});
        }
    });
})();
