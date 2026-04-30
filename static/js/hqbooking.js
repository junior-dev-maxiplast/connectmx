(function () {
    const boot = window.HQBOOKING_BOOTSTRAP || {};
    let currentYear = Number(boot.year);
    let currentMonth = Number(boot.month);
    let activeReservationCount = 0;
    let maxActiveReservations = 3;
    let pendingReserveDate = null;
    let allEnvironments = [];
    let selectedEnvIds = new Set();

    const monthLabel = document.getElementById("monthLabel");
    const grid = document.getElementById("calendarGrid");
    const prevBtn = document.getElementById("prevMonthBtn");
    const nextBtn = document.getElementById("nextMonthBtn");
    const toast = document.getElementById("hqbToast");
    const counter = document.getElementById("hqbReservationCounter");
    const reserveModal = document.getElementById("hqbReserveModal");
    const modalClose = document.getElementById("hqbModalClose");
    const modalCancel = document.getElementById("hqbModalCancelBtn");
    const modalConfirm = document.getElementById("hqbModalConfirmBtn");
    const modalDateLabel = document.getElementById("hqbModalDateLabel");
    const modalStart = document.getElementById("hqbStartTime");
    const modalEnd = document.getElementById("hqbEndTime");
    const modalReason = document.getElementById("hqbReason");
    const envAddBtn = document.getElementById("hqbEnvAddBtn");
    const envAllBtn = document.getElementById("hqbEnvAllBtn");
    const envPicker = document.getElementById("hqbEnvPicker");
    const selectedEnvsEl = document.getElementById("hqbSelectedEnvs");

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
        clearTimeout(showToast._timer);
        showToast._timer = setTimeout(() => toast.classList.remove("is-show"), 2200);
    }

    function escapeHtml(str) {
        return String(str || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function formatBrDate(iso) {
        if (!iso) return "-";
        const p = String(iso).split("-");
        if (p.length !== 3) return iso;
        return `${p[2]}/${p[1]}/${p[0]}`;
    }

    function monthNamePt(year, month) {
        const dt = new Date(year, month - 1, 1);
        return dt.toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
    }

    async function fetchCalendar() {
        const res = await fetch(`/sede/api/calendar/?year=${currentYear}&month=${currentMonth}`);
        const data = await res.json();
        if (!res.ok || data.status !== "ok") {
            throw new Error(data.message || "Erro ao carregar calendario");
        }
        activeReservationCount = Number(data.active_reservation_count || 0);
        maxActiveReservations = Number(data.max_active_reservations || 3);
        allEnvironments = Array.isArray(data.environments) ? data.environments : [];
        renderCalendar(data);
        renderEnvironmentPicker();
    }

    function renderEnvironmentPicker() {
        if (!envPicker) return;
        if (!allEnvironments.length) {
            envPicker.innerHTML = `<div class="hqb-env-empty">Nenhum ambiente cadastrado.</div>`;
            return;
        }
        envPicker.innerHTML = allEnvironments
            .map((env) => {
                const disabled = selectedEnvIds.has(Number(env.id)) ? "disabled" : "";
                const title = env.description ? ` title="${escapeHtml(env.description)}"` : "";
                return `<button type="button" class="hqb-env-pick-btn" data-id="${env.id}"${disabled}${title}>${escapeHtml(env.name)}</button>`;
            })
            .join("");
    }

    function renderSelectedEnvironments() {
        if (!selectedEnvsEl) return;
        if (!selectedEnvIds.size) {
            selectedEnvsEl.innerHTML = `<div class="hqb-env-empty">Nenhum ambiente selecionado.</div>`;
            return;
        }
        const map = new Map(allEnvironments.map((e) => [Number(e.id), e]));
        const items = Array.from(selectedEnvIds)
            .map((id) => map.get(Number(id)))
            .filter(Boolean);
        selectedEnvsEl.innerHTML = items
            .map((env) => {
                const title = env.description ? ` title="${escapeHtml(env.description)}"` : "";
                return `
                    <div class="hqb-env-chip"${title}>
                        <span>${escapeHtml(env.name)}</span>
                        <button type="button" data-id="${env.id}">&times;</button>
                    </div>
                `;
            })
            .join("");
    }

    function renderCalendar(data) {
        if (!monthLabel || !grid) return;
        monthLabel.textContent = monthNamePt(data.year, data.month);
        if (counter) {
            counter.textContent = `Reservas ativas: ${activeReservationCount}/${maxActiveReservations}`;
        }
        grid.innerHTML = "";

        const firstWeekday = data.days.length ? data.days[0].weekday : 0;
        for (let i = 0; i < firstWeekday; i++) {
            const empty = document.createElement("div");
            empty.className = "hqb-day empty";
            grid.appendChild(empty);
        }

        data.days.forEach((d) => {
            const card = document.createElement("article");
            card.className = "hqb-day";
            const minePending = d.is_mine && d.my_status === "PENDING";
            const mineApproved = d.is_mine && d.my_status === "APPROVED";
            const mineRejected = d.is_mine && d.my_status === "REJECTED";

            if (d.is_past) {
                card.classList.add("past");
            } else if (d.is_blocked) {
                card.classList.add("blocked");
            } else if (minePending) {
                card.classList.add("pending");
            } else if (mineApproved) {
                card.classList.add("mine");
            } else if (d.is_reserved) {
                card.classList.add("reserved");
            } else if (mineRejected) {
                card.classList.add("rejected");
            } else {
                card.classList.add("available");
            }

            let statusClass = "available";
            let statusText = "Disponivel";
            let ownerText = "Sem reserva";
            if (d.is_past) {
                statusClass = "past";
                statusText = "Passado";
                ownerText = "Nao disponivel para reserva";
            } else if (d.is_blocked) {
                statusClass = "blocked";
                statusText = "Indisponivel";
                ownerText = d.block_reason ? `Motivo: ${escapeHtml(d.block_reason)}` : "Data bloqueada pelo administrador";
            } else if (minePending) {
                statusClass = "pending";
                statusText = "Solicitacao pendente";
                ownerText = "Aguardando aprovacao admin";
            } else if (mineApproved) {
                statusClass = "mine";
                statusText = "Reservado por voce";
                ownerText = `Matricula: ${escapeHtml(d.reserved_by || boot.employeeId || "")}`;
            } else if (d.is_reserved) {
                statusClass = "reserved";
                statusText = "Reservado";
                ownerText = `Matricula: ${escapeHtml(d.reserved_by)}`;
            } else if (mineRejected) {
                statusClass = "rejected";
                statusText = "Solicitacao recusada";
                ownerText = "Data livre para novas solicitacoes";
            }

            const slotText = d.start_time && d.end_time ? `Horario: ${escapeHtml(d.start_time)} - ${escapeHtml(d.end_time)}` : "";
            const reasonText = (d.reason && (d.is_reserved || minePending || mineApproved || mineRejected)) ? `Motivo: ${escapeHtml(d.reason)}` : "";
            const envText = Array.isArray(d.environments) && d.environments.length ? `Ambientes: ${escapeHtml(d.environments.join(", "))}` : "";
            card.title = envText ? envText.replace(/^Ambientes:\s*/, "") : "";

            const actions = document.createElement("div");
            actions.className = "hqb-day-actions";

            if (d.is_past || d.is_blocked) {
                const hint = document.createElement("div");
                hint.className = "hqb-day-owner";
                hint.textContent = d.is_past ? "Data encerrada" : "Data indisponivel";
                actions.appendChild(hint);
            } else if (minePending || mineApproved) {
                const cancelBtn = document.createElement("button");
                cancelBtn.className = "hqb-btn-cancel";
                cancelBtn.type = "button";
                cancelBtn.textContent = "Cancelar";
                cancelBtn.addEventListener("click", () => cancelDate(d.date));
                actions.appendChild(cancelBtn);
            } else if (!d.is_reserved && !mineRejected && activeReservationCount < maxActiveReservations) {
                const reserveBtn = document.createElement("button");
                reserveBtn.className = "hqb-btn-reserve";
                reserveBtn.type = "button";
                reserveBtn.textContent = "Reservar";
                reserveBtn.addEventListener("click", () => openReserveModal(d.date));
                actions.appendChild(reserveBtn);
            } else if (!d.is_reserved && activeReservationCount >= maxActiveReservations) {
                const hint = document.createElement("div");
                hint.className = "hqb-day-owner";
                hint.textContent = "Limite de 3 reservas ativas";
                actions.appendChild(hint);
            } else if (mineRejected) {
                const reserveBtn = document.createElement("button");
                reserveBtn.className = "hqb-btn-reserve";
                reserveBtn.type = "button";
                reserveBtn.textContent = "Solicitar novamente";
                reserveBtn.addEventListener("click", () => openReserveModal(d.date));
                actions.appendChild(reserveBtn);
            }

            card.innerHTML = `
                <div class="hqb-day-number">${d.day}</div>
                <div>
                    <div class="hqb-day-status ${statusClass}">${statusText}</div>
                    <div class="hqb-day-owner">${ownerText}</div>
                    ${slotText ? `<div class="hqb-day-owner">${slotText}</div>` : ""}
                    ${reasonText ? `<div class="hqb-day-owner">${reasonText}</div>` : ""}
                    ${envText ? `<div class="hqb-day-owner">${envText}</div>` : ""}
                </div>
            `;
            card.appendChild(actions);
            grid.appendChild(card);
        });
    }

    async function reserveDate(isoDate) {
        const startTime = (modalStart && modalStart.value) ? modalStart.value : "";
        const endTime = (modalEnd && modalEnd.value) ? modalEnd.value : "";
        const reason = (modalReason && modalReason.value) ? modalReason.value.trim() : "";
        const environments = Array.from(selectedEnvIds);

        if (!startTime || !endTime) {
            showToast("Informe horario de inicio e termino.", true);
            return;
        }
        if (!reason) {
            showToast("Informe o motivo da reserva.", true);
            return;
        }
        if (!environments.length) {
            showToast("Selecione ao menos um ambiente.", true);
            return;
        }

        try {
            const res = await fetch("/sede/api/reserve/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCookie("csrftoken"),
                },
                credentials: "same-origin",
                body: JSON.stringify({
                    date: isoDate,
                    start_time: startTime,
                    end_time: endTime,
                    reason: reason,
                    environments: environments,
                }),
            });
            const data = await res.json();
            if (!res.ok || data.status !== "ok") {
                throw new Error(data.message || "Nao foi possivel reservar");
            }
            closeReserveModal();
            showToast("Solicitacao enviada com sucesso.");
            await fetchCalendar();
        } catch (err) {
            showToast(err.message || "Erro ao reservar", true);
        }
    }

    async function cancelDate(isoDate) {
        try {
            const res = await fetch("/sede/api/cancel/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCookie("csrftoken"),
                },
                credentials: "same-origin",
                body: JSON.stringify({ date: isoDate }),
            });
            const data = await res.json();
            if (!res.ok || data.status !== "ok") {
                throw new Error(data.message || "Nao foi possivel cancelar");
            }
            showToast("Solicitacao/reserva cancelada.");
            await fetchCalendar();
        } catch (err) {
            showToast(err.message || "Erro ao cancelar", true);
        }
    }

    function goMonth(delta) {
        currentMonth += delta;
        if (currentMonth <= 0) {
            currentMonth = 12;
            currentYear -= 1;
        } else if (currentMonth > 12) {
            currentMonth = 1;
            currentYear += 1;
        }
        fetchCalendar().catch((e) => showToast(e.message || "Erro", true));
    }

    function openReserveModal(isoDate) {
        pendingReserveDate = isoDate;
        selectedEnvIds = new Set();
        if (modalDateLabel) modalDateLabel.textContent = formatBrDate(isoDate);
        if (modalStart) modalStart.value = "";
        if (modalEnd) modalEnd.value = "";
        if (modalReason) modalReason.value = "";
        if (envPicker) envPicker.style.display = "none";
        renderEnvironmentPicker();
        renderSelectedEnvironments();
        if (reserveModal) reserveModal.style.display = "grid";
    }

    function closeReserveModal() {
        pendingReserveDate = null;
        if (reserveModal) reserveModal.style.display = "none";
    }

    if (prevBtn) prevBtn.addEventListener("click", () => goMonth(-1));
    if (nextBtn) nextBtn.addEventListener("click", () => goMonth(1));

    if (modalClose) modalClose.addEventListener("click", closeReserveModal);
    if (modalCancel) modalCancel.addEventListener("click", closeReserveModal);
    if (reserveModal) {
        reserveModal.addEventListener("click", (e) => {
            if (e.target === reserveModal) closeReserveModal();
        });
    }
    if (modalConfirm) {
        modalConfirm.addEventListener("click", () => {
            if (!pendingReserveDate) return;
            reserveDate(pendingReserveDate);
        });
    }

    if (envAddBtn) {
        envAddBtn.addEventListener("click", () => {
            if (!envPicker) return;
            envPicker.style.display = envPicker.style.display === "none" ? "grid" : "none";
        });
    }

    if (envAllBtn) {
        envAllBtn.addEventListener("click", () => {
            selectedEnvIds = new Set(allEnvironments.map((e) => Number(e.id)));
            renderEnvironmentPicker();
            renderSelectedEnvironments();
        });
    }

    if (envPicker) {
        envPicker.addEventListener("click", (e) => {
            const btn = e.target.closest("button[data-id]");
            if (!btn) return;
            const id = Number(btn.dataset.id);
            selectedEnvIds.add(id);
            renderEnvironmentPicker();
            renderSelectedEnvironments();
        });
    }

    if (selectedEnvsEl) {
        selectedEnvsEl.addEventListener("click", (e) => {
            const btn = e.target.closest("button[data-id]");
            if (!btn) return;
            const id = Number(btn.dataset.id);
            selectedEnvIds.delete(id);
            renderEnvironmentPicker();
            renderSelectedEnvironments();
        });
    }

    fetchCalendar().catch((e) => showToast(e.message || "Erro", true));
})();
