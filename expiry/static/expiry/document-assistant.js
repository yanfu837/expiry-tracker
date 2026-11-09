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
        if (file.size > 10 * 1024 * 1024) {
            pending = false;
            blocked = true;
            status.textContent = '檔案超過 10 MB，請改用較小的文件。';
            if (retry) retry.disabled = true;
            return;
        }
        if (assistant.dataset.available !== 'true') {
            blocked = true;
            status.textContent = '私人掃毒服務尚未啟用，請移除新附件後儲存。';
            return;
        }
        controller = new AbortController();
        const current = controller;
        pending = true;
        blocked = true;
        retry.disabled = true;
        status.textContent = '正在私人伺服器掃毒與辨識，請稍候…';
        const body = new FormData();
        body.append('document', file);
        try {
            const response = await fetch(assistant.dataset.analyzeUrl, {
                method: 'POST', body, signal: current.signal, credentials: 'same-origin',
                headers: {'X-CSRFToken': form.querySelector('[name="csrfmiddlewaretoken"]').value},
            });
            if (response.redirected) throw new Error('登入已過期，請重新登入後再試。');
            const result = await response.json().catch(() => ({message: '請求驗證已過期，請重新整理後再試。'}));
            if (current !== controller) return;
            if (!response.ok) throw new Error(result.message || '無法完成掃毒與辨識，檔案尚未儲存。');
            if (!result.fields || !result.scan_token) throw new Error('辨識結果無法使用，請重新辨識文件。');
            for (const key of fields) {
                if (!dirty.has(key) && result.fields[key] != null) form.elements[key].value = result.fields[key];
            }
            for (const warning of result.fields.warnings) {
                const li = document.createElement('li');
                li.textContent = warning;
                warnings.append(li);
            }
            if (result.fields.evidence) evidence.textContent = '到期日來源：' + result.fields.evidence;
            token.value = result.scan_token;
            blocked = false;
            status.textContent = '掃描已通過。可辨識的空白欄位已帶入，原有或手動修改的內容已保留；請確認後再儲存。';
        } catch (error) {
            if (error.name === 'AbortError' || current !== controller) return;
            status.textContent = error.message || '無法完成掃毒，檔案尚未儲存。';
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
            status.textContent = pending ? '掃描與辨識尚未完成，請稍候。' : '檔案尚未通過掃描，請重新辨識或移除附件。';
            status.scrollIntoView({block: 'center'});
        }
    });
})();
