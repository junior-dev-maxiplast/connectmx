/* Metas de Vendas (Dashes): grade família × mês em kg que grava na USU_TORCMET.
   Servidor: tiqueue/sales_goals_views.py. Só as células alteradas vão para o salvar, cada uma com o valor
   que a tela mostrava ao abrir ("original"), para o servidor recusar se alguém gravou nela nesse meio-tempo. */
(() => {
    'use strict';

    const card = document.getElementById('mxGoals');
    const dataNode = document.getElementById('mxgData');
    if (!card || !dataNode) return;

    const MONTHS = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
    const MAX_WEIGHT = 999999999.999;
    const body = document.getElementById('mxgBody');
    const wrap = document.getElementById('mxgWrap');
    const empty = document.getElementById('mxgEmpty');
    const statusNode = document.getElementById('mxgStatus');
    const errorBox = document.getElementById('mxgError');
    const saveButton = document.getElementById('mxgSave');
    const undoButton = document.getElementById('mxgUndo');
    const addButton = document.getElementById('mxgAdd');
    const familySelect = document.getElementById('mxgFamily');
    const grandTotal = document.getElementById('mxgGrandTotal');
    const columnTotals = [...card.querySelectorAll('[data-mxg-col-total]')];
    const csrfInput = card.querySelector('input[name="csrfmiddlewaretoken"]');
    const families = JSON.parse(document.getElementById('mxgFamilies').textContent || '[]');
    const familyNames = Object.fromEntries(families.map((item) => [item.code, item.name]));

    const shown = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 3 });
    const editing = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 3, useGrouping: false });
    const display = (value) => (value == null ? '' : shown.format(value));
    const canonical = (value) => (value == null ? null : value.toFixed(3));
    const toNumber = (text) => (text == null ? null : Number(text) || null);
    const same = (left, right) => Math.abs((left || 0) - (right || 0)) < 0.0005;

    let rows = [];
    let saving = false;
    const rowByTr = new Map();

    /** "231.648,36", "231648,36" e "231648.36" valem o mesmo; zero e vazio = sem meta. */
    const parse = (raw) => {
        let text = String(raw == null ? '' : raw).replace(/[\s ]/g, '');
        if (!text) return { value: null };
        if (text.includes(',')) text = text.replace(/\./g, '').replace(',', '.');
        else if (/^\d{1,3}(\.\d{3})+$/.test(text)) text = text.replace(/\./g, '');
        if (!/^\d+(\.\d+)?$/.test(text)) return { invalid: true };
        const value = Math.round(Number(text) * 1000) / 1000;
        if (!Number.isFinite(value) || value > MAX_WEIGHT) return { invalid: true };
        return { value: value === 0 ? null : value };
    };

    const makeRow = (family, name, months, launches) => ({
        family,
        name: name || familyNames[family] || '',
        cells: MONTHS.map((_, index) => {
            const original = toNumber(months ? months[index] : null);
            return { original, value: original, invalid: false, conflict: false, launches: (launches && launches[index]) || [] };
        }),
    });

    const smallButton = (label, title, onClick, danger) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `mxg-row-btn${danger ? ' is-danger' : ''}`;
        button.textContent = label;
        button.title = title;
        button.setAttribute('aria-label', title);
        button.addEventListener('click', onClick);
        return button;
    };

    const renderRow = (row) => {
        const tr = document.createElement('tr');
        tr.dataset.family = row.family;
        const head = document.createElement('th');
        head.scope = 'row';
        head.className = 'mxg-family-col';
        const code = document.createElement('b');
        code.textContent = row.family;
        const name = document.createElement('span');
        name.className = 'mx-cell-sub';
        name.textContent = row.name || 'família fora do cadastro';
        head.append(code, name);
        tr.append(head);
        row.cells.forEach((cell, index) => {
            const td = document.createElement('td');
            td.className = 'mxg-cell';
            const input = document.createElement('input');
            input.type = 'text';
            input.inputMode = 'decimal';
            input.autocomplete = 'off';
            input.spellcheck = false;
            input.className = 'mxg-input';
            input.dataset.month = String(index + 1);
            input.setAttribute('aria-label', `${row.family} ${row.name || ''} · ${MONTHS[index]}`.trim());
            input.value = display(cell.value);
            if (cell.launches.length) {
                input.title = cell.launches.length > 1
                    ? `Lançamentos ${cell.launches.join(', ')} (somados; ao salvar fica um só)`
                    : `Lançamento ${cell.launches[0]}`;
            }
            cell.input = input;
            cell.td = td;
            td.append(input);
            tr.append(td);
        });
        row.totalCell = document.createElement('td');
        row.totalCell.className = 'is-num is-strong mxg-row-total';
        tr.append(row.totalCell);
        const tools = document.createElement('td');
        tools.className = 'mxg-row-tools';
        tools.append(
            smallButton('Repetir jan.', `Copiar janeiro de ${row.family} para os 12 meses`, () => repeatFirst(row)),
            smallButton('Limpar', `Tirar as metas de ${row.family} (vale ao salvar)`, () => clearRow(row), true),
        );
        tr.append(tools);
        row.tr = tr;
        rowByTr.set(tr, row);
        return tr;
    };

    const renderAll = () => {
        rowByTr.clear();
        body.replaceChildren(...rows.map(renderRow));
        refreshFamilyOptions();
        refresh();
    };

    const refreshFamilyOptions = () => {
        const used = new Set(rows.map((row) => row.family));
        const finished = document.createElement('optgroup');
        finished.label = 'Produto acabado (800…)';
        const others = document.createElement('optgroup');
        others.label = 'Demais famílias';
        families.filter((item) => !used.has(item.code)).forEach((item) => {
            const option = document.createElement('option');
            option.value = item.code;
            option.textContent = `${item.code} · ${item.name}`;
            (item.code.startsWith('8') ? finished : others).append(option);
        });
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = 'Escolha a família…';
        familySelect.replaceChildren(placeholder, ...[finished, others].filter((group) => group.children.length));
        addButton.disabled = familySelect.options.length <= 1;
    };

    const refresh = () => {
        let dirty = 0;
        let invalid = 0;
        let grand = 0;
        const columns = MONTHS.map(() => ({ sum: 0, any: false }));
        rows.forEach((row) => {
            let rowSum = 0;
            let rowAny = false;
            row.cells.forEach((cell, index) => {
                cell.dirty = !cell.invalid && !same(cell.value, cell.original);
                cell.td.classList.toggle('is-dirty', cell.dirty);
                cell.td.classList.toggle('is-invalid', cell.invalid);
                cell.td.classList.toggle('is-conflict', cell.conflict);
                if (cell.dirty) dirty += 1;
                if (cell.invalid) invalid += 1;
                if (!cell.invalid && cell.value != null) {
                    rowSum += cell.value;
                    rowAny = true;
                    columns[index].sum += cell.value;
                    columns[index].any = true;
                }
            });
            row.totalCell.textContent = rowAny ? shown.format(rowSum) : '';
            grand += rowSum;
        });
        columns.forEach((column, index) => { columnTotals[index].textContent = column.any ? shown.format(column.sum) : ''; });
        grandTotal.textContent = shown.format(grand);
        wrap.hidden = rows.length === 0;
        empty.hidden = rows.length > 0;
        saveButton.disabled = saving || dirty === 0 || invalid > 0;
        undoButton.disabled = saving || (dirty === 0 && invalid === 0 && !rows.some((row) => row.isNew));
        if (!saving && !statusNode.dataset.sticky) {
            setStatus(invalid
                ? `${invalid} valor(es) inválido(s): use números em kg, sem sinal.`
                : dirty ? `${dirty} célula(s) alterada(s), ainda não salva(s).` : '', invalid ? 'warn' : '');
        }
        card.dataset.dirty = String(dirty);
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

    const setCell = (cell, raw) => {
        const parsed = parse(raw);
        cell.invalid = Boolean(parsed.invalid);
        cell.conflict = false;
        if (!cell.invalid) cell.value = parsed.value;
        if (document.activeElement !== cell.input) cell.input.value = cell.invalid ? String(raw).trim() : display(cell.value);
    };

    const cellFromInput = (input) => {
        const row = rowByTr.get(input.closest('tr'));
        return row ? { row, cell: row.cells[Number(input.dataset.month) - 1] } : null;
    };

    const repeatFirst = (row) => {
        const first = row.cells[0];
        if (first.invalid) return;
        row.cells.slice(1).forEach((cell) => {
            cell.value = first.value;
            cell.invalid = false;
            cell.conflict = false;
            cell.input.value = display(cell.value);
        });
        clearStickyStatus();
        refresh();
    };

    const clearRow = (row) => {
        if (row.isNew && row.cells.every((cell) => cell.original == null)) {
            rows = rows.filter((item) => item !== row);
            renderAll();
            return;
        }
        row.cells.forEach((cell) => {
            cell.value = null;
            cell.invalid = false;
            cell.conflict = false;
            cell.input.value = '';
        });
        clearStickyStatus();
        refresh();
    };

    const clearStickyStatus = () => {
        delete statusNode.dataset.sticky;
        showError('');
    };

    const load = (payload) => {
        rows = (payload.rows || []).map((item) => makeRow(item.family, item.name, item.months, item.launches));
        renderAll();
    };

    const move = (input, step) => {
        const found = cellFromInput(input);
        if (!found) return;
        const index = rows.indexOf(found.row) + step;
        const target = rows[index];
        if (target) target.cells[Number(input.dataset.month) - 1].input.focus();
    };

    body.addEventListener('focusin', (event) => {
        const input = event.target.closest('input.mxg-input');
        const found = input && cellFromInput(input);
        if (!found || found.cell.invalid) return;
        input.value = found.cell.value == null ? '' : editing.format(found.cell.value);
        input.select();
    });

    body.addEventListener('focusout', (event) => {
        const input = event.target.closest('input.mxg-input');
        const found = input && cellFromInput(input);
        if (found && !found.cell.invalid) input.value = display(found.cell.value);
    });

    body.addEventListener('input', (event) => {
        const input = event.target.closest('input.mxg-input');
        const found = input && cellFromInput(input);
        if (!found) return;
        setCell(found.cell, input.value);
        clearStickyStatus();
        refresh();
    });

    body.addEventListener('keydown', (event) => {
        const input = event.target.closest('input.mxg-input');
        if (!input || event.key !== 'Enter') return;
        event.preventDefault();
        move(input, event.shiftKey ? -1 : 1);
    });

    // Colar um bloco do Excel (tabulações e quebras de linha) preenche a partir da célula, para a direita e para baixo.
    body.addEventListener('paste', (event) => {
        const input = event.target.closest('input.mxg-input');
        const found = input && cellFromInput(input);
        const text = (event.clipboardData || window.clipboardData)?.getData('text') || '';
        if (!found || !/[\t\n]/.test(text.replace(/[\r\n]+$/, ''))) return;
        event.preventDefault();
        const lines = text.replace(/\r/g, '').replace(/\n+$/, '').split('\n').map((line) => line.split('\t'));
        const startRow = rows.indexOf(found.row);
        const startMonth = Number(input.dataset.month) - 1;
        lines.forEach((columns, lineIndex) => {
            const row = rows[startRow + lineIndex];
            if (!row) return;
            columns.forEach((raw, columnIndex) => {
                const cell = row.cells[startMonth + columnIndex];
                if (cell) setCell(cell, raw);
            });
        });
        input.value = found.cell.invalid
            ? String(lines[0][0]).trim()
            : (found.cell.value == null ? '' : editing.format(found.cell.value));
        clearStickyStatus();
        refresh();
    });

    addButton.addEventListener('click', () => {
        const code = familySelect.value;
        if (!code) {
            familySelect.focus();
            return;
        }
        const row = makeRow(code, familyNames[code], null, null);
        row.isNew = true;
        rows.push(row);
        rows.sort((left, right) => left.family.localeCompare(right.family));
        renderAll();
        row.cells[0].input.focus();
    });

    // Desfazer descarta o que não foi salvo e traz os valores atuais do ERP (inclusive o que outra pessoa gravou).
    undoButton.addEventListener('click', async () => {
        const dirty = Number(card.dataset.dirty || 0);
        if (dirty && !window.confirm(`Descartar ${dirty} alteração(ões) que ainda não foram salvas?`)) return;
        clearStickyStatus();
        try {
            await reload();
        } catch (error) {
            load(lastPayload);
            showError(`Não foi possível recarregar do ERP (${error.message}); a grade voltou ao que estava ao abrir.`);
        }
    });

    const jsonUrl = () => {
        const params = new URLSearchParams({
            empresa: card.dataset.empresa, filial: card.dataset.filial, ano: card.dataset.ano, formato: 'json',
        });
        return `${card.dataset.pageUrl}?${params}`;
    };

    const reload = async () => {
        const response = await fetch(jsonUrl(), { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
        lastPayload = payload;
        load(payload);
        return payload;
    };

    const markConflicts = (conflicts) => {
        const keys = new Set(conflicts.map((item) => `${item.familia}|${item.mes}`));
        rows.forEach((row) => row.cells.forEach((cell, index) => { cell.conflict = keys.has(`${row.family}|${index + 1}`); }));
    };

    const save = async () => {
        const cells = [];
        rows.forEach((row) => row.cells.forEach((cell, index) => {
            if (cell.dirty) cells.push({ familia: row.family, mes: index + 1, valor: canonical(cell.value), original: canonical(cell.original) });
        }));
        if (!cells.length) return;
        saving = true;
        showError('');
        setStatus(`Gravando ${cells.length} célula(s)…`, '', true);
        refresh();
        try {
            const response = await fetch(card.dataset.saveUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': csrfInput ? csrfInput.value : '' },
                body: JSON.stringify({
                    empresa: Number(card.dataset.empresa), filial: Number(card.dataset.filial), ano: Number(card.dataset.ano), celulas: cells,
                }),
            });
            let result = {};
            try { result = await response.json(); } catch (error) { result = {}; }
            if (response.ok && result.ok) {
                const counts = result.counts || {};
                saving = false;
                const payload = await reload();
                // A meta mudou: o peso dos representantes (grade de baixo) foi refeito no servidor.
                document.dispatchEvent(new CustomEvent('mxg:saved', { detail: payload }));
                const reps = result.reps_updated ? ` Peso dos representantes refeito em ${result.reps_updated} linha(s).` : '';
                setStatus(`Salvo: ${counts.inserir || 0} incluída(s), ${counts.atualizar || 0} alterada(s), ${counts.apagar || 0} apagada(s).${reps}`, 'good', true);
            } else {
                if (response.status === 409) markConflicts(result.conflicts || []);
                showError(result.error || `Não foi possível gravar (HTTP ${response.status}). Se a sessão do Dashes expirou, entre de novo.`);
                setStatus('Nada foi gravado.', 'warn', true);
            }
        } catch (error) {
            showError(`Não foi possível gravar: ${error.message}`);
            setStatus('Nada foi gravado.', 'warn', true);
        } finally {
            saving = false;
            refresh();
        }
    };

    saveButton.addEventListener('click', save);

    document.querySelectorAll('[data-mxg-filter]').forEach((select) => {
        select.addEventListener('change', () => select.form.submit());
    });

    window.addEventListener('beforeunload', (event) => {
        if (Number(card.dataset.dirty || 0) > 0 && !saving) {
            event.preventDefault();
            event.returnValue = '';
        }
    });

    let lastPayload = JSON.parse(dataNode.textContent || '{}');
    load(lastPayload);
})();
