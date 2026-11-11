/* Comportamento do shell MaxiSys (menu, gaveta no celular, tema e título da barra),
   igual em todas as telas. Os controles são marcados com data-mx-*; o estado
   do menu recolhido fica em <html class="mx-rail"> e em localStorage. */
(function () {
    "use strict";

    var root = document.documentElement;
    var store = {
        get: function (key) { try { return localStorage.getItem(key); } catch (error) { return null; } },
        set: function (key, value) { try { localStorage.setItem(key, value); } catch (error) { /* modo privado */ } },
    };

    function setRail(on) {
        root.classList.toggle("mx-rail", on);
        store.set("mx_sidebar", on ? "collapsed" : "open");
        document.querySelectorAll("[data-mx-collapse]").forEach(function (button) {
            button.setAttribute("aria-label", on ? "Expandir menu lateral" : "Recolher menu lateral");
            button.title = on ? "Expandir menu" : "Recolher menu";
        });
    }

    function setDrawer(open) {
        root.classList.toggle("mx-drawer-open", open);
        document.querySelectorAll("[data-mx-scrim]").forEach(function (scrim) { scrim.hidden = !open; });
    }

    function toggleTheme() {
        var dark = window.ConnectMXTheme ? window.ConnectMXTheme.get() === "dark" : root.getAttribute("data-theme") === "dark";
        var next = dark ? "light" : "dark";
        if (window.ConnectMXTheme) window.ConnectMXTheme.set(next);
        else root.setAttribute("data-theme", next);
        document.dispatchEvent(new CustomEvent("mx:theme"));
    }

    document.addEventListener("click", function (event) {
        var control = event.target.closest("[data-mx-collapse], [data-mx-menu], [data-mx-scrim], [data-mx-theme]");
        if (!control) return;
        if (control.hasAttribute("data-mx-collapse")) setRail(!root.classList.contains("mx-rail"));
        else if (control.hasAttribute("data-mx-menu")) setDrawer(!root.classList.contains("mx-drawer-open"));
        else if (control.hasAttribute("data-mx-scrim")) setDrawer(false);
        else if (control.hasAttribute("data-mx-theme")) toggleTheme();
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && root.classList.contains("mx-drawer-open")) setDrawer(false);
    });

    /* Formulários que gastam algo (tokens da Econodata, análises de IA) pedem
       confirmação (data-mx-confirm) e/ou mostram que estão trabalhando
       (data-mx-busy), e travam o botão depois do envio, para um clique duplo não
       cobrar duas vezes. */
    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (!form.hasAttribute) return;
        var confirmText = form.getAttribute("data-mx-confirm");
        var busyText = form.getAttribute("data-mx-busy");
        if (!confirmText && !busyText) return;
        if (form.getAttribute("data-mx-sent") === "1" || (confirmText && !window.confirm(confirmText))) {
            event.preventDefault();
            return;
        }
        form.setAttribute("data-mx-sent", "1");
        form.querySelectorAll("button[type=submit]").forEach(function (button) {
            button.disabled = true;
            button.setAttribute("aria-busy", "true");
            if (busyText) button.textContent = busyText;
        });
    });

    if (root.classList.contains("mx-rail")) setRail(true);

    /* Título da barra superior nas telas antigas: o item ativo do menu e, sem
       ele, a parte do <title> depois do "|" ("ConnectMX | Fila Geral"). */
    document.querySelectorAll("[data-mx-appbar-title]").forEach(function (title) {
        var text = title.textContent.trim();
        if (!text) {
            var active = document.querySelector(".mx-sidebar .mx-nav-item.is-active .mx-nav-text");
            var fromTitle = (document.title || "").split("|").pop().trim();
            text = (active && active.textContent.trim()) || fromTitle || "ConnectMX";
            title.textContent = text;
        }
        document.querySelectorAll("[data-mx-current-tab]").forEach(function (tab) {
            var label = tab.querySelector("[data-mx-tab-label]");
            if (label && !label.textContent.trim()) label.textContent = text;
        });
    });
})();
