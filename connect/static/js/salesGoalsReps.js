/* Metas por representante (USU_TORCGER) na tela Metas de Vendas. Só o % é digitado; o peso de cada mês é
   meta da família × % / 100, com a mesma conta do servidor (tiqueue/sales_goals_reps.py): em gramas inteiras e,
   quando os % da família somam 100, o resto vai para os maiores restos, então a soma fecha a meta exata. */
(() => {
    'use strict';

    const card = document.getElementById('mxReps');
    const dataNode = document.getElementById('mxgData');
    if (!card || !dataNode) return;

    const MONTHS = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
    const UNITS = 1000000n; // 100,0000% em passos de 0,0001
    const body = document.getElementById('mxrBody');
    const wrap = document.getElementById('mxrWrap');
    const empty = document.getElementById('mxrEmpty');
    const statusNode = document.getElementById('mxrStatus');
    const errorBox = document.getElementById('mxrError');
    const saveButton = document.getElementById('mxrSave');
    const undoButton = document.getElementById('mxrUndo');
    const computeButton = document.getElementById('mxrCompute');
    const baseSelect = document.getElementById('mxrBase');
    const addFamily = document.getElementById('mxrAddFamily');
    const addRep = document.getElementById('mxrAddRep');
    const addButton = document.getElementById('mxrAdd');
    const filterSelect = document.getElementById('mxrFilter');
    const csrfInput = card.querySelector('input[name="csrfmiddlewaretoken"]');
    const catalog = JSON.parse(document.getElementById('mxrCatalog').textContent || '[]');
    const catalogByCode = new Map(catalog.map((item) => [item.code, item]));

    const kgShown = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 3 });
    const pctShown = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 4 });

    let families = [];
    let saving = false;
    let lastPayload = JSON.parse(dataNode.textContent || '{}');

    // ---- números exatos: kg em gramas e % em décimos de milésimo (BigInt), como no servidor ----
    const gramsFrom = (text) => (text == null ? null : BigInt(String(text).replace('.', '')));
    const unitsFrom = (text) => {
        if (text == null) return null;
        const [whole, fraction = ''] = String(text).split('.');
        return BigInt(whole) * 10000n + BigInt((fraction + '0000').slice(0, 4));
    };
    const unitsText = (units) => (units == null ? null : `${units / 10000n}.${String(units % 10000n).padStart(4, '0')}`);
    const unitsNumber = (units) => Number(units) / 10000;
    const gramsNumber = (grams) => Number(grams) / 1000;
    const showPercent = (units) => (units == null ? '' : pctShown.format(unitsNumber(units)));
    const showKg = (grams) => (grams == null ? '' : kgShown.format(gramsNumber(grams)));

    /** "22,5", "22.5" e "22,01234" (arredonda a 4 casas); vazio ou zero = sem representante. */
    const parsePercent = (raw) => {
        const text = String(raw == null ? '' : raw).replace(/[\s %]/g, '').replace(',', '.');
        if (!text) return { units: null };
        if (!/^\d{1,3}(\.\d+)?$/.test(text)) return { invalid: true };
        const [whole, fraction = ''] = text.split('.');
        let units = BigInt(whole) * 10000n + BigInt((fraction + '0000').slice(0, 4));
        if (fraction.length > 4 && fraction[4] >= '5') units += 1n;
        if (units > UNITS) return { invalid: true };
        return { units: units === 0n ? null : units };
    };

    const compareKeys = (left, right) => (left < right ? -1 : left > right ? 1 : 0);

    /** Peso de cada representante no mês: o mesmo `split_weight` do servidor. */
    const splitWeight = (grams, shares) => {
        const result = new Map();
        if (!shares.size) return result;
        const total = [...shares.values()].reduce((sum, units) => sum + units, 0n);
        if (grams && grams > 0n && total === UNITS) {
            const parts = [...shares.entries()].map(([rep, units]) => {
                const product = grams * units;
                return { rep, units, floor: product / UNITS, rest: product % UNITS };
            });
            let left = grams - parts.reduce((sum, part) => sum + part.floor, 0n);
            parts.sort((a, b) => compareKeys(b.rest, a.rest) || compareKeys(b.units, a.units) || compareKeys(String(a.rep), String(b.rep)));
            for (const part of parts) {
                if (left <= 0n) break;
                part.floor += 1n;
                left -= 1n;
            }
            parts.forEach((part) => result.set(part.rep, part.floor));
            return result;
        }
        shares.forEach((units, rep) => result.set(rep, grams ? (grams * units + UNITS / 2n) / UNITS : 0n));
        return result;
    };

    // ---- estado ----
    const repInfo = (code, fallback) => {
        const info = catalogByCode.get(code);
        return {
            name: (fallback && fallback.name) || (info && info.name) || `Representante ${code}`,
            active: fallback && 'active' in fallback ? fallback.active : !info || info.active,
            known: fallback && 'known' in fallback ? fallback.known : Boolean(info),
        };
    };

    const makeRep = (item, isNew) => {
        const original = isNew ? null : unitsFrom(item.percent);
        return {
            rep: item.rep, ...repInfo(item.rep, item), varies: Boolean(item.varies), isNew,
            original, units: isNew ? unitsFrom(item.percent) : original, invalid: false, rawText: null,
        };
    };

    const load = (payload) => {
        families = (payload.reps || []).map((family) => ({
            family: family.family,
            name: family.name,
            meta: family.meta.map(gramsFrom),
            reps: family.reps.map((item) => makeRep(item, false)),
        }));
        render();
    };

    const isDirty = (rep) => !rep.invalid && rep.units !== rep.original && !(rep.isNew && rep.units == null);

    // ---- desenho ----
    const cell = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    };

    const familyHeader = (family, totalUnits, anyRep) => {
        const tr = cell('tr', 'mxr-family');
        tr.dataset.family = family.family;
        const head = cell('th', 'mxg-family-col');
        head.scope = 'rowgroup';
        head.append(cell('b', null, family.family), cell('span', 'mx-cell-sub', family.name || 'família fora do cadastro'));
        tr.append(head);
        const sum = cell('td', 'is-num mxr-sum');
        if (anyRep) {
            const badge = cell('span', 'mx-badge', `${showPercent(totalUnits) || '0'}%`);
            if (totalUnits === UNITS) {
                badge.classList.add('is-good');
                badge.title = 'Os % somam 100: o peso dos representantes fecha a meta.';
            } else {
                badge.classList.add(totalUnits > UNITS ? 'is-bad' : 'is-warn');
                const gap = totalUnits > UNITS ? totalUnits - UNITS : UNITS - totalUnits;
                badge.title = totalUnits > UNITS ? `Passa de 100% em ${showPercent(gap)}%` : `Faltam ${showPercent(gap)}% sem representante`;
            }
            sum.append(badge);
        }
        tr.append(sum);
        let total = 0n;
        family.meta.forEach((grams) => {
            tr.append(cell('td', 'is-num mxr-meta', grams == null ? '—' : showKg(grams)));
            total += grams || 0n;
        });
        tr.append(cell('td', 'is-num is-strong mxr-meta', showKg(total)));
        tr.append(cell('td', 'mxg-row-tools'));
        return tr;
    };

    const repRow = (family, rep, weights) => {
        const tr = cell('tr', 'mxr-rep');
        tr.dataset.family = family.family;
        tr.dataset.rep = String(rep.rep);
        if (rep.units == null && !rep.isNew && rep.original != null) tr.classList.add('is-removed');
        const head = cell('th', 'mxg-family-col');
        head.scope = 'row';
        const title = cell('span', 'mxr-rep-name');
        title.append(cell('b', null, String(rep.rep)), document.createTextNode(` ${rep.name}`));
        head.append(title);
        const tags = [];
        if (!rep.active) tags.push(['is-bad', 'inativo no ERP']);
        if (!rep.known) tags.push(['is-bad', 'fora do cadastro']);
        if (rep.varies) tags.push(['is-warn', '% varia por mês']);
        if (rep.isNew) tags.push(['is-info', 'novo']);
        if (tags.length) {
            const holder = cell('span', 'mx-cell-sub');
            tags.forEach(([tone, label]) => holder.append(cell('span', `mx-badge ${tone}`, label), document.createTextNode(' ')));
            head.append(holder);
        }
        tr.append(head);
        const pct = cell('td', 'mxg-cell mxr-pct');
        const input = document.createElement('input');
        input.type = 'text';
        input.inputMode = 'decimal';
        input.autocomplete = 'off';
        input.className = 'mxg-input';
        input.setAttribute('aria-label', `% de ${rep.rep} ${rep.name} na família ${family.family}`);
        input.value = rep.invalid ? rep.rawText : showPercent(rep.units);
        input.dataset.family = family.family;
        input.dataset.rep = String(rep.rep);
        input.dataset.action = 'percent';
        pct.classList.toggle('is-dirty', isDirty(rep));
        pct.classList.toggle('is-invalid', rep.invalid);
        pct.classList.toggle('is-conflict', Boolean(rep.conflict));
        pct.append(input);
        tr.append(pct);
        let total = 0n;
        weights.forEach((byRep) => {
            const grams = byRep.get(rep.rep);
            tr.append(cell('td', 'is-num mxr-kg', grams == null ? '' : showKg(grams)));
            total += grams || 0n;
        });
        tr.append(cell('td', 'is-num is-strong', rep.units == null ? '' : showKg(total)));
        const tools = cell('td', 'mxg-row-tools');
        const remove = cell('button', 'mxg-row-btn is-danger', 'Remover');
        remove.type = 'button';
        remove.title = `Tirar ${rep.rep} da família ${family.family} (vale ao salvar)`;
        remove.setAttribute('aria-label', remove.title);
        remove.dataset.family = family.family;
        remove.dataset.rep = String(rep.rep);
        remove.dataset.action = 'remove';
        remove.addEventListener('click', () => removeRep(family, rep));
        tools.append(remove);
        tr.append(tools);
        return tr;
    };

    const render = () => {
        // Guarda o campo ou botão da grade que está com o foco, para devolvê-lo depois de redesenhar.
        const active = document.activeElement;
        const focusKey = active && body.contains(active) && active.dataset.action
            ? { action: active.dataset.action, family: active.dataset.family, rep: active.dataset.rep } : null;
        const filter = filterSelect.value;
        const rows = [];
        let dirty = 0;
        let invalid = 0;
        families.forEach((family) => {
            const shares = new Map();
            let totalUnits = 0n;
            family.reps.forEach((rep) => {
                if (isDirty(rep)) dirty += 1;
                if (rep.invalid) invalid += 1;
                if (!rep.invalid && rep.units != null) {
                    shares.set(rep.rep, rep.units);
                    totalUnits += rep.units;
                }
            });
            const weights = family.meta.map((grams) => splitWeight(grams, shares));
            const visible = family.reps.filter((rep) => !filter || String(rep.rep) === filter);
            if (filter && !visible.length) return;
            rows.push(familyHeader(family, totalUnits, family.reps.length > 0));
            if (!family.reps.length) {
                const tr = cell('tr', 'mxr-none');
                const td = cell('td', 'mx-hint', 'Nenhum representante nesta família: use "Calcular % pelo faturamento" ou "Adicionar representante".');
                td.colSpan = 16;
                tr.append(td);
                rows.push(tr);
            }
            visible.forEach((rep, index) => {
                const row = repRow(family, rep, weights);
                // Zebrado dentro de cada família: o primeiro representante de cada uma começa claro.
                if (index % 2 === 1) row.classList.add('is-alt');
                rows.push(row);
            });
        });
        body.replaceChildren(...rows);
        wrap.hidden = families.length === 0;
        empty.hidden = families.length > 0;
        computeButton.disabled = saving || families.length === 0;
        saveButton.disabled = saving || dirty === 0 || invalid > 0;
        undoButton.disabled = saving || (dirty === 0 && invalid === 0);
        if (!saving && !statusNode.dataset.sticky) {
            setStatus(invalid
                ? `${invalid} % inválido(s): use de 0 a 100, até 4 casas.`
                : dirty ? `${dirty} representante(s) alterado(s), ainda não salvo(s).` : '', invalid ? 'warn' : '');
        }
        card.dataset.dirty = String(dirty);
        refreshSelects();
        if (focusKey) {
            const again = [...body.querySelectorAll(`[data-action="${focusKey.action}"]`)]
                .find((node) => node.dataset.family === focusKey.family && node.dataset.rep === focusKey.rep);
            if (again) again.focus();
        }
    };

    // O % é aplicado quando o campo perde o foco; redesenha depois que o foco já foi para o próximo campo (Tab/Enter),
    // para devolvê-lo ao campo certo.
    let pending = null;
    const scheduleRender = () => {
        if (pending) clearTimeout(pending);
        pending = setTimeout(() => {
            pending = null;
            render();
        }, 0);
    };

    const refreshSelects = () => {
        const currentFamily = addFamily.value;
        addFamily.replaceChildren(...families.map((family) => {
            const option = cell('option', null, `${family.family} · ${family.name || ''}`);
            option.value = family.family;
            return option;
        }));
        if (currentFamily && families.some((family) => family.family === currentFamily)) addFamily.value = currentFamily;
        const family = families.find((item) => item.family === addFamily.value);
        const used = new Set(family ? family.reps.map((rep) => rep.rep) : []);
        const currentRep = addRep.value;
        const placeholder = cell('option', null, 'Escolha o representante…');
        placeholder.value = '';
        addRep.replaceChildren(placeholder, ...catalog.filter((item) => !used.has(item.code)).map((item) => {
            const option = cell('option', null, `${item.code} · ${item.name}${item.active ? '' : ' (inativo)'}`);
            option.value = String(item.code);
            return option;
        }));
        if (currentRep && [...addRep.options].some((option) => option.value === currentRep)) addRep.value = currentRep;
        addButton.disabled = saving || !families.length;
        const currentFilter = filterSelect.value;
        const reps = new Map();
        families.forEach((item) => item.reps.forEach((rep) => reps.set(rep.rep, rep.name)));
        const all = cell('option', null, 'Todos os representantes');
        all.value = '';
        filterSelect.replaceChildren(all, ...[...reps.entries()].sort((a, b) => a[1].localeCompare(b[1], 'pt-BR')).map(([code, name]) => {
            const option = cell('option', null, `${code} · ${name}`);
            option.value = String(code);
            return option;
        }));
        filterSelect.value = reps.has(Number(currentFilter)) ? currentFilter : '';
    };

    const setStatus = (text, tone, sticky) => {
        statusNode.textContent = text;
        statusNode.className = `mxg-status${tone ? ` is-${tone}` : ''}`;
        if (sticky) statusNode.dataset.sticky = '1';
        else delete statusNode.dataset.sticky;
    };

    const showError = (text) => {
        errorBox.textContent = text;
        errorBox.hidden = !text;
    };

    const clearMessages = () => {
        delete statusNode.dataset.sticky;
        showError('');
    };

    const findRep = (input) => {
        const family = families.find((item) => item.family === input.dataset.family);
        const rep = family && family.reps.find((item) => String(item.rep) === input.dataset.rep);
        return rep ? { family, rep } : null;
    };

    const removeRep = (family, rep) => {
        if (rep.isNew) family.reps = family.reps.filter((item) => item !== rep);
        else {
            rep.units = null;
            rep.invalid = false;
            rep.conflict = false;
        }
        clearMessages();
        render();
    };

    // ---- eventos ----
    body.addEventListener('focusin', (event) => {
        const input = event.target.closest('input.mxg-input');
        const found = input && findRep(input);
        if (!found || found.rep.invalid) return;
        input.value = found.rep.units == null ? '' : showPercent(found.rep.units).replace(/\./g, '');
        input.select();
    });

    body.addEventListener('change', (event) => {
        const input = event.target.closest('input.mxg-input');
        const found = input && findRep(input);
        if (!found) return;
        const parsed = parsePercent(input.value);
        found.rep.invalid = Boolean(parsed.invalid);
        found.rep.rawText = input.value;
        found.rep.conflict = false;
        if (!found.rep.invalid) found.rep.units = parsed.units;
        clearMessages();
        scheduleRender();
    });

    body.addEventListener('keydown', (event) => {
        const input = event.target.closest('input.mxg-input');
        if (!input || event.key !== 'Enter') return;
        event.preventDefault();
        input.dispatchEvent(new Event('change', { bubbles: true }));
        const inputs = [...body.querySelectorAll('input.mxg-input')];
        const current = inputs.findIndex((item) => item.dataset.family === input.dataset.family && item.dataset.rep === input.dataset.rep);
        const next = inputs[current + (event.shiftKey ? -1 : 1)];
        if (next) next.focus();
    });

    addFamily.addEventListener('change', refreshSelects);
    filterSelect.addEventListener('change', () => render());

    addButton.addEventListener('click', () => {
        const family = families.find((item) => item.family === addFamily.value);
        const code = Number(addRep.value);
        if (!family || !code) {
            (family ? addRep : addFamily).focus();
            return;
        }
        family.reps.push(makeRep({ rep: code, percent: null }, true));
        filterSelect.value = '';
        clearMessages();
        render();
        const input = body.querySelector(`input[data-family="${family.family}"][data-rep="${code}"]`);
        if (input) input.focus();
    });

    computeButton.addEventListener('click', async () => {
        const label = baseSelect.options[baseSelect.selectedIndex] ? baseSelect.options[baseSelect.selectedIndex].text : baseSelect.value;
        if (families.some((family) => family.reps.length)
            && !window.confirm(`Trocar os % atuais pelos calculados com o faturamento de ${label}? Nada é gravado até você salvar.`)) return;
        const params = new URLSearchParams({ empresa: card.dataset.empresa, filial: card.dataset.filial, ano: card.dataset.ano, base: baseSelect.value });
        computeButton.disabled = true;
        setStatus('Calculando pelo faturamento…', '', true);
        showError('');
        try {
            const response = await fetch(`${card.dataset.computeUrl}?${params}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
            let applied = 0;
            const without = [];
            families.forEach((family) => {
                const result = data.families[family.family];
                if (!result) return;
                if (!result.reps.length) {
                    without.push(family.family);
                    return;
                }
                const computed = new Map(result.reps.map((item) => [item.rep, unitsFrom(item.percent)]));
                family.reps.forEach((rep) => {
                    rep.units = computed.has(rep.rep) ? computed.get(rep.rep) : null;
                    rep.invalid = false;
                    rep.conflict = false;
                });
                result.reps.forEach((item) => {
                    if (!family.reps.some((rep) => rep.rep === item.rep)) family.reps.push(makeRep(item, true));
                });
                family.reps.sort((a, b) => compareKeys(b.units || 0n, a.units || 0n) || a.rep - b.rep);
                applied += 1;
            });
            render();
            setStatus(`% calculado pelo faturamento de ${label} em ${applied} família(s)${without.length ? `; sem faturamento na base: ${without.join(', ')}` : ''}. Confira e salve.`, 'good', true);
        } catch (error) {
            showError(`Não foi possível calcular: ${error.message}`);
            setStatus('', '', false);
        } finally {
            computeButton.disabled = saving || families.length === 0;
        }
    });

    const jsonUrl = () => {
        const params = new URLSearchParams({ empresa: card.dataset.empresa, filial: card.dataset.filial, ano: card.dataset.ano, formato: 'json' });
        return `${card.dataset.pageUrl}?${params}`;
    };

    const reload = async () => {
        const response = await fetch(jsonUrl(), { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
        lastPayload = payload;
        load(payload);
    };

    undoButton.addEventListener('click', async () => {
        const dirty = Number(card.dataset.dirty || 0);
        if (dirty && !window.confirm(`Descartar ${dirty} alteração(ões) de representantes que ainda não foram salvas?`)) return;
        clearMessages();
        try {
            await reload();
        } catch (error) {
            load(lastPayload);
            showError(`Não foi possível recarregar do ERP (${error.message}); a grade voltou ao que estava ao abrir.`);
        }
    });

    saveButton.addEventListener('click', async () => {
        const changes = [];
        families.forEach((family) => family.reps.forEach((rep) => {
            if (isDirty(rep)) {
                changes.push({ familia: family.family, representante: rep.rep, percentual: unitsText(rep.units), original: unitsText(rep.original) });
            }
        }));
        if (!changes.length) return;
        saving = true;
        showError('');
        setStatus(`Gravando ${changes.length} representante(s)…`, '', true);
        render();
        try {
            const response = await fetch(card.dataset.saveUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': csrfInput ? csrfInput.value : '' },
                body: JSON.stringify({ empresa: Number(card.dataset.empresa), filial: Number(card.dataset.filial), ano: Number(card.dataset.ano), representantes: changes }),
            });
            let result = {};
            try { result = await response.json(); } catch (error) { result = {}; }
            if (response.ok && result.ok) {
                const counts = result.counts || {};
                saving = false;
                await reload();
                setStatus(`Salvo: ${counts.inserir || 0} linha(s) incluída(s), ${counts.atualizar || 0} alterada(s), ${counts.apagar || 0} apagada(s).`, 'good', true);
            } else {
                if (response.status === 409) {
                    const keys = new Set((result.conflicts || []).map((item) => `${item.familia}|${item.representante}`));
                    families.forEach((family) => family.reps.forEach((rep) => { rep.conflict = keys.has(`${family.family}|${rep.rep}`); }));
                }
                showError(result.error || `Não foi possível gravar (HTTP ${response.status}). Se a sessão do Dashes expirou, entre de novo.`);
                setStatus('Nada foi gravado.', 'warn', true);
            }
        } catch (error) {
            showError(`Não foi possível gravar: ${error.message}`);
            setStatus('Nada foi gravado.', 'warn', true);
        } finally {
            saving = false;
            render();
        }
    });

    // A grade de famílias salvou: a meta mudou e o servidor refez o peso dos representantes. Atualiza a meta
    // de cada família sem perder os % que ainda não foram salvos aqui.
    document.addEventListener('mxg:saved', (event) => {
        const payload = event.detail || {};
        if (!payload.reps) return;
        lastPayload = payload;
        if (!Number(card.dataset.dirty || 0)) {
            load(payload);
            return;
        }
        const byFamily = new Map(payload.reps.map((family) => [family.family, family]));
        families.forEach((family) => {
            const fresh = byFamily.get(family.family);
            if (fresh) family.meta = fresh.meta.map(gramsFrom);
        });
        payload.reps.forEach((fresh) => {
            if (!families.some((family) => family.family === fresh.family)) {
                families.push({ family: fresh.family, name: fresh.name, meta: fresh.meta.map(gramsFrom), reps: fresh.reps.map((item) => makeRep(item, false)) });
            }
        });
        families.sort((a, b) => compareKeys(a.family, b.family));
        render();
    });

    window.addEventListener('beforeunload', (event) => {
        if (Number(card.dataset.dirty || 0) > 0 && !saving) {
            event.preventDefault();
            event.returnValue = '';
        }
    });

    load(lastPayload);
})();
