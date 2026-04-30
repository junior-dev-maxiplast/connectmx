(function () {
    const STORAGE_KEY = "connectmx_theme";

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

    // Once DOM exists, sync toggles.
    document.addEventListener("DOMContentLoaded", syncSwitches);

    // Cross-tab sync.
    window.addEventListener("storage", (e) => {
        if (e.key !== STORAGE_KEY) return;
        currentTheme = applyTheme(e.newValue || preferredTheme());
        syncSwitches();
    });
})();

