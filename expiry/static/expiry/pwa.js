(() => {
    const panel = document.querySelector('#pwa-install-panel');
    const button = document.querySelector('#pwa-install');
    const status = document.querySelector('#pwa-install-status');
    const offlineNotice = document.querySelector('#offline-notice');
    const standalone = window.matchMedia('(display-mode: standalone)');
    let installPrompt = null;
    function installed() {
        if (panel) panel.hidden = standalone.matches || window.navigator.standalone === true;
    }
    function updateConnection() {
        if (offlineNotice) offlineNotice.hidden = navigator.onLine;
    }
    installed(); updateConnection();
    standalone.addEventListener?.('change', installed);
    window.addEventListener('online', updateConnection);
    window.addEventListener('offline', updateConnection);
    window.addEventListener('beforeinstallprompt', event => {
        event.preventDefault(); installPrompt = event;
        if (button) button.hidden = false;
    });
    window.addEventListener('appinstalled', () => {
        installPrompt = null;
        if (panel) panel.hidden = true;
    });
    button?.addEventListener('click', async () => {
        if (!installPrompt) return;
        const prompt = installPrompt; installPrompt = null; button.hidden = true;
        try {
            await prompt.prompt();
            const result = await prompt.userChoice;
            if (status) status.textContent = result.outcome === 'accepted' ? 'Installation requested. Look for Expiry Tracker on your home screen.' : 'You can install later using your browser menu.';
        } catch (_) {
            if (status) status.textContent = 'Use the browser menu to add Expiry Tracker to your home screen.';
        }
    });
    if ('serviceWorker' in navigator && window.isSecureContext) {
        window.addEventListener('load', () => {
            navigator.serviceWorker.register('/service-worker.js', {scope: '/', updateViaCache: 'none'}).catch(() => {
                if (status) status.textContent = 'Installation support is unavailable right now. You can still use the website.';
            });
        });
    }
})();
