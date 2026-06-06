const STREAMVAULT_API_URL = window.location.origin;

function applyStoredTheme() {
    const stored = localStorage.getItem('dark_theme');
    if (stored === 'true') {
        document.documentElement.classList.add('dark');
        document.documentElement.classList.remove('light');
        return;
    }
    if (stored === 'false') {
        document.documentElement.classList.add('light');
        document.documentElement.classList.remove('dark');
        return;
    }
    if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
        document.documentElement.classList.add('dark');
        document.documentElement.classList.remove('light');
    }
}

let svTranslations = {};
let svCurrentLang = 'ru';

function getCachedLang() {
    return localStorage.getItem('sv_language') || 'ru';
}

function getCachedTranslations(lang) {
    try {
        const data = localStorage.getItem(`sv_i18n_${lang}`);
        return data ? JSON.parse(data) : null;
    } catch {
        return null;
    }
}

function cacheTranslations(lang, data) {
    try {
        localStorage.setItem(`sv_i18n_${lang}`, JSON.stringify(data));
    } catch {}
}

async function loadTranslations(lang) {
    try {
        const res = await fetch(`${STREAMVAULT_API_URL}/api/i18n/${lang}`);
        if (res.ok) {
            const data = await res.json();
            cacheTranslations(lang, data);
            return data;
        }
    } catch (e) {
        console.warn('Failed to fetch translations:', e);
    }
    return null;
}

function t(key, params = {}) {
    const keys = key.split('.');
    let val = svTranslations;
    for (const k of keys) {
        if (val && typeof val === 'object' && k in val) {
            val = val[k];
        } else {
            return key;
        }
    }
    if (typeof val !== 'string') return key;
    return val.replace(/\{(\w+)\}/g, (_, p) => params[p] ?? `{${p}}`);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text ?? '';
    return div.innerHTML;
}

function applyTranslations() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        el.textContent = t(key);
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        el.placeholder = t(key);
    });
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title');
        el.title = t(key);
    });
}

async function initI18n() {
    svCurrentLang = getCachedLang();
    const cached = getCachedTranslations(svCurrentLang);
    if (cached) {
        svTranslations = cached;
        applyTranslations();
    }

    try {
        const settingsRes = await fetch(`${STREAMVAULT_API_URL}/api/settings`);
        if (settingsRes.ok) {
            const settings = await settingsRes.json();
            const serverLang = settings.language || 'ru';
            if (serverLang !== svCurrentLang) {
                svCurrentLang = serverLang;
                localStorage.setItem('sv_language', serverLang);
            }
            const fresh = await loadTranslations(svCurrentLang);
            if (fresh) {
                svTranslations = fresh;
                applyTranslations();
            }
        }
    } catch {
        // server unavailable
    }
}

function showToast(message, title = '', type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    const palette = {
        success: { icon: 'check_circle', accent: 'text-emerald-500' },
        warning: { icon: 'warning', accent: 'text-amber-500' },
        error: { icon: 'error', accent: 'text-red-500' },
        info: { icon: 'info', accent: 'text-primary' },
    };
    const tone = palette[type] || palette.info;
    const titleHtml = title ? `<p class="text-sm font-semibold text-on-surface truncate">${escapeHtml(title)}</p>` : '';

    toast.className = 'bg-surface-container-low border border-outline-variant rounded-xl p-4 shadow-lg flex flex-col gap-2 animate-slide-in';
    toast.innerHTML = `
        <div class="flex items-start gap-3">
            <span class="material-symbols-outlined ${tone.accent} text-[22px] shrink-0 mt-0.5">${tone.icon}</span>
            <div class="flex-1 min-w-0">
                ${titleHtml}
                <p class="text-sm text-on-surface-variant">${escapeHtml(message)}</p>
            </div>
            <button type="button" class="text-on-surface-variant hover:text-on-surface shrink-0" data-toast-close>
                <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
        </div>
    `;

    toast.querySelector('[data-toast-close]')?.addEventListener('click', () => toast.remove());
    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('animate-slide-out');
        setTimeout(() => toast.remove(), 260);
    }, 4500);
}

function showDraftSettingToast(title, value) {
    showToast(`${value}. ${t('notifications.apply_changes')}`, title, 'info');
}

window.StreamVault = {
    applyStoredTheme,
    getCachedLang,
    getCachedTranslations,
    cacheTranslations,
    loadTranslations,
    t,
    applyTranslations,
    initI18n,
    escapeHtml,
    showToast,
    showDraftSettingToast,
};

window.applyStoredTheme = applyStoredTheme;
window.getCachedLang = getCachedLang;
window.getCachedTranslations = getCachedTranslations;
window.cacheTranslations = cacheTranslations;
window.loadTranslations = loadTranslations;
window.t = t;
window.applyTranslations = applyTranslations;
window.initI18n = initI18n;
window.escapeHtml = escapeHtml;
window.showToast = showToast;
window.showDraftSettingToast = showDraftSettingToast;
