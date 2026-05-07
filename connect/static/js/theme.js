(function () {
    const STORAGE_KEY = "connectmx_theme";
    const FAVICON_HREF = "/static/assets/cmx-page.png";

    function preferredTheme() {
        try {
            if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
                return "dark";
            }
        } catch (_) {}
        return "light";
    }

    function applyTheme(theme) {
        const t = theme === "dark" ? "dark" : "light";
        document.documentElement.setAttribute("data-theme", t);
        return t;
    }

    let currentTheme = "light";
    try {
        const saved = localStorage.getItem(STORAGE_KEY);
        currentTheme = applyTheme(saved || preferredTheme());
    } catch (_) {
        currentTheme = applyTheme(preferredTheme());
    }

    function syncSwitches() {
        const isDark = currentTheme === "dark";
        document.querySelectorAll("input[data-theme-switch]").forEach((input) => {
            input.checked = isDark;
        });
    }

    function setTheme(theme) {
        currentTheme = applyTheme(theme);
        try {
            localStorage.setItem(STORAGE_KEY, currentTheme);
        } catch (_) {}
        syncSwitches();
    }

    window.ConnectMXTheme = {
        get: () => currentTheme,
        set: setTheme,
        sync: syncSwitches,
    };

    function ensureFavicon() {
        const head = document.head || document.querySelector("head");
        if (!head) return;

        let icon = document.querySelector('link[rel="icon"]');
        if (!icon) {
            icon = document.createElement("link");
            icon.setAttribute("rel", "icon");
            head.appendChild(icon);
        }
        icon.setAttribute("type", "image/png");
        icon.setAttribute("href", FAVICON_HREF);
    }

    // Once DOM exists, sync toggles.
    document.addEventListener("DOMContentLoaded", () => {
        syncSwitches();
        ensureFavicon();
    });

    // Also run immediately for pages where script is loaded late.
    ensureFavicon();

    // Cross-tab sync.
    window.addEventListener("storage", (e) => {
        if (e.key !== STORAGE_KEY) return;
        currentTheme = applyTheme(e.newValue || preferredTheme());
        syncSwitches();
    });
})();
