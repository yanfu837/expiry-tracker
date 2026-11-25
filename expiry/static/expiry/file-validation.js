(() => {
    const limit = 10 * 1024 * 1024;
    document.querySelectorAll('input[type=file][name=document]').forEach(input => {
        const form = input.form;
        const status = document.createElement('p');
        status.className = 'helptext'; status.id = `${input.id}-upload-status`;
        status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
        input.after(status);
        input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'), status.id].filter(Boolean).join(' '));
        function check() {
            const file = input.files[0];
            if (file && (!file.size || file.size > limit)) {
                status.textContent = 'This file is empty or exceeds 10 MB. Nothing was uploaded. Choose another file or save this item without an attachment.';
                input.value = '';
                input.dispatchEvent(new Event('document-rejected'));
                return false;
            }
            status.textContent = '';
            return true;
        }
        input.addEventListener('change', check);
        form?.addEventListener('submit', event => {if (!check()) event.preventDefault();});
    });
})();
