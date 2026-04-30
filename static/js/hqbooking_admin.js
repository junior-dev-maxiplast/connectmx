(function () {
    const rowsEl = document.getElementById("hqbAdminRows");
    const statusEl = document.getElementById("hqbAdminStatus");
    const envFilterEl = document.getElementById("hqbAdminEnvFilter");
    const orderEl = document.getElementById("hqbAdminOrder");
    const refreshBtn = document.getElementById("hqbAdminRefresh");
    const toast = document.getElementById("hqbToast");

    const envRowsEl = document.getElementById("hqbAdminEnvRows");
    const envNameEl = document.getElementById("hqbEnvName");
    const envDescEl = document.getElementById("hqbEnvDescription");
    const envCreateBtn = document.getElementById("hqbEnvCreateBtn");

    const blockRowsEl = document.getElementById("hqbAdminBlockRows");
    const blockDateEl = document.getElementById("hqbBlockDate");
    const blockReasonEl = document.getElementById("hqbBlockReason");
    const blockCreateBtn = document.getElementById("hqbBlockCreateBtn");

    const tabButtons = Array.from(document.querySelectorAll(".hqb-tab-btn"));
    const tabPanels = {
        reservas: document.getElementById("hqbTabReservas"),
        ambientes: document.getElementById("hqbTabAmbientes"),
        bloqueios: document.getElementById("hqbTabBloqueios"),
    };

    let environmentsCache = [];

    function getCookie(name) {
        const cookies = document.cookie ? document.cookie.split(";") : [];
        for (let i = 0; i < cookies.length; i++) {
            const c = cookies[i].trim();
            if (c.substring(0, name.length + 1) === `${name}=`) {
                return decodeURIComponent(c.substring(name.length + 1));
            }
        }
        return null;
    }

    function showToast(message, isError) {
        if (!toast) return;
        toast.textContent = message || "";
        toast.classList.toggle("is-error", !!isError);
        toast.classList.add("is-show");
        clearTimeout(showToast._t);
        showToast._t = setTimeout(() => toast.classList.remove("is-show"), 2200);
    }

    function esc(str) {
        return String(str || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function fmtDate(iso) {
        const p = String(iso || "").split("-");
        if (p.length !== 3) return iso || "-";
        return `${p[2]}/${p[1]}/${p[0]}`;
    }

    function statusBadge(status) {
        const label = status === "APPROVED" ? "Aprovada" : (status === "REJECTED" ? "Recusada" : "Pendente");
        const cls = status === "APPROVED" ? "hqb-st-approved" : (status === "REJECTED" ? "hqb-st-rejected" : "hqb-st-pending");
        return `<span class="hqb-status ${cls}">${label}</span>`;
    }

    function setTab(name) {
        tabButtons.forEach((btn) => btn.classList.toggle("is-active", btn.dataset.tab === name));
        Object.keys(tabPanels).forEach((k) => {
            if (!tabPanels[k]) return;
            tabPanels[k].style.display = (k === name) ? "" : "none";
        });
    }

    async function apiPost(url, payload) {
        const res = await fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookie("csrftoken"),
            },
            credentials: "same-origin",
            body: payload ? JSON.stringify(payload) : "{}",
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== "ok") {
            throw new Error(data.message || "Erro na operacao");
        }
        return data;
    }

    async function loadEnvironments() {
        const res = await fetch("/sede/admin/api/environments/");
        const data = await res.json();
        if (!res.ok || data.status !== "ok") throw new Error(data.message || "Erro ao carregar ambientes");
        environmentsCache = data.environments || [];

        if (envFilterEl) {
            const current = envFilterEl.value || "";
            const options = [`<option value="">Todos</option>`].concat(
                environmentsCache.map((e) => `<option value="${e.id}">${esc(e.name)}</option>`)
            );
            envFilterEl.innerHTML = options.join("");
            envFilterEl.value = current;
        }

        if (envRowsEl) {
            if (!environmentsCache.length) {
                envRowsEl.innerHTML = `<tr><td colspan="3">Nenhum ambiente cadastrado.</td></tr>`;
            } else {
                envRowsEl.innerHTML = environmentsCache.map((e) => `
                    <tr data-id="${e.id}">
                        <td>${esc(e.name)}</td>
                        <td>${esc(e.description || "-")}</td>
                        <td>
                            <button class="hqb-admin-btn approve" data-action="edit-env" data-id="${e.id}">Editar</button>
                            <button class="hqb-admin-btn reject" data-action="delete-env" data-id="${e.id}">Excluir</button>
                        </td>
                    </tr>
                `).join("");
            }
        }
    }

    async function loadReservations() {
        const status = statusEl ? statusEl.value : "";
        const order = orderEl ? orderEl.value : "asc";
        const environmentId = envFilterEl ? envFilterEl.value : "";
        const query = new URLSearchParams({ status, order, environment_id: environmentId });
        const res = await fetch(`/sede/admin/api/requests/?${query.toString()}`);
        const data = await res.json();
        if (!res.ok || data.status !== "ok") throw new Error(data.message || "Erro ao carregar solicitacoes");

        const requests = data.requests || [];
        if (!requests.length) {
            rowsEl.innerHTML = `<tr><td colspan="7">Nenhuma solicitacao encontrada.</td></tr>`;
            return;
        }
        rowsEl.innerHTML = requests.map((r) => {
            const timeRange = r.start_time && r.end_time ? `${esc(r.start_time)} - ${esc(r.end_time)}` : "-";
            const envNames = (r.environments || []).map((e) => e.name).join(", ");
            const canReview = r.status === "PENDING";
            return `
                <tr data-id="${r.id}">
                    <td>${esc(r.employee_id)}</td>
                    <td>${esc(fmtDate(r.date))}</td>
                    <td>${timeRange}</td>
                    <td>${esc(r.reason || "-")}</td>
                    <td>${esc(envNames || "-")}</td>
                    <td>${statusBadge(r.status)}</td>
                    <td>
                        ${canReview ? `
                            <button class="hqb-admin-btn approve" data-action="approve" data-id="${r.id}">Aprovar</button>
                            <button class="hqb-admin-btn reject" data-action="reject" data-id="${r.id}">Recusar</button>
                        ` : `<span class="hqb-admin-noaction">-</span>`}
                    </td>
                </tr>
            `;
        }).join("");
    }

    async function loadBlocks() {
        const res = await fetch("/sede/admin/api/blocks/");
        const data = await res.json();
        if (!res.ok || data.status !== "ok") throw new Error(data.message || "Erro ao carregar bloqueios");
        const blocks = data.blocks || [];
        if (!blocks.length) {
            blockRowsEl.innerHTML = `<tr><td colspan="4">Nenhuma data bloqueada.</td></tr>`;
            return;
        }
        blockRowsEl.innerHTML = blocks.map((b) => `
            <tr data-id="${b.id}">
                <td>${esc(fmtDate(b.blocked_date))}</td>
                <td>${esc(b.reason || "-")}</td>
                <td>${esc(b.blocked_by || "-")}</td>
                <td><button class="hqb-admin-btn reject" data-action="delete-block" data-id="${b.id}">Remover</button></td>
            </tr>
        `).join("");
    }

    async function reviewRequest(id, action) {
        const url = action === "approve"
            ? `/sede/admin/api/requests/${id}/approve/`
            : `/sede/admin/api/requests/${id}/reject/`;
        const data = await apiPost(url, {});
        showToast(data.message || "Atualizado com sucesso");
        await loadReservations();
    }

    async function createEnvironment() {
        const name = (envNameEl.value || "").trim();
        const description = (envDescEl.value || "").trim();
        await apiPost("/sede/admin/api/environments/create/", { name, description });
        envNameEl.value = "";
        envDescEl.value = "";
        showToast("Ambiente cadastrado com sucesso.");
        await loadEnvironments();
        await loadReservations();
    }

    async function editEnvironment(id) {
        const current = environmentsCache.find((e) => String(e.id) === String(id));
        if (!current) return;
        const name = window.prompt("Nome do ambiente:", current.name || "");
        if (name === null) return;
        const description = window.prompt("Descricao (opcional):", current.description || "");
        if (description === null) return;
        await apiPost(`/sede/admin/api/environments/${id}/update/`, { name: name.trim(), description: description.trim() });
        showToast("Ambiente atualizado.");
        await loadEnvironments();
        await loadReservations();
    }

    async function deleteEnvironment(id) {
        if (!window.confirm("Deseja excluir este ambiente?")) return;
        await apiPost(`/sede/admin/api/environments/${id}/delete/`, {});
        showToast("Ambiente removido.");
        await loadEnvironments();
        await loadReservations();
    }

    async function createBlock() {
        const blocked_date = (blockDateEl.value || "").trim();
        const reason = (blockReasonEl.value || "").trim();
        await apiPost("/sede/admin/api/blocks/create/", { blocked_date, reason });
        blockDateEl.value = "";
        blockReasonEl.value = "";
        showToast("Data bloqueada com sucesso.");
        await loadBlocks();
        await loadReservations();
    }

    async function deleteBlock(id) {
        if (!window.confirm("Deseja remover este bloqueio de data?")) return;
        await apiPost(`/sede/admin/api/blocks/${id}/delete/`, {});
        showToast("Bloqueio removido.");
        await loadBlocks();
    }

    tabButtons.forEach((btn) => {
        btn.addEventListener("click", () => setTab(btn.dataset.tab));
    });

    if (rowsEl) {
        rowsEl.addEventListener("click", (e) => {
            const btn = e.target.closest("button[data-action][data-id]");
            if (!btn) return;
            reviewRequest(btn.dataset.id, btn.dataset.action).catch((err) => showToast(err.message || "Erro", true));
        });
    }

    if (envRowsEl) {
        envRowsEl.addEventListener("click", (e) => {
            const btn = e.target.closest("button[data-action][data-id]");
            if (!btn) return;
            const id = btn.dataset.id;
            const action = btn.dataset.action;
            if (action === "edit-env") {
                editEnvironment(id).catch((err) => showToast(err.message || "Erro", true));
            } else if (action === "delete-env") {
                deleteEnvironment(id).catch((err) => showToast(err.message || "Erro", true));
            }
        });
    }

    if (blockRowsEl) {
        blockRowsEl.addEventListener("click", (e) => {
            const btn = e.target.closest("button[data-action='delete-block'][data-id]");
            if (!btn) return;
            deleteBlock(btn.dataset.id).catch((err) => showToast(err.message || "Erro", true));
        });
    }

    if (statusEl) statusEl.addEventListener("change", () => loadReservations().catch((err) => showToast(err.message || "Erro", true)));
    if (envFilterEl) envFilterEl.addEventListener("change", () => loadReservations().catch((err) => showToast(err.message || "Erro", true)));
    if (orderEl) orderEl.addEventListener("change", () => loadReservations().catch((err) => showToast(err.message || "Erro", true)));
    if (refreshBtn) refreshBtn.addEventListener("click", () => loadReservations().catch((err) => showToast(err.message || "Erro", true)));
    if (envCreateBtn) envCreateBtn.addEventListener("click", () => createEnvironment().catch((err) => showToast(err.message || "Erro", true)));
    if (blockCreateBtn) blockCreateBtn.addEventListener("click", () => createBlock().catch((err) => showToast(err.message || "Erro", true)));

    Promise.all([loadEnvironments(), loadReservations(), loadBlocks()]).catch((err) => showToast(err.message || "Erro", true));
})();
