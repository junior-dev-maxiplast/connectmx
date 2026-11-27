/* MXDrill: menu de opções dos gráficos e visuais do MaxiSys e a janela "Expandir dados".

   Como abre o menu: botão direito no gráfico, toque longo (celular) ou o botão ⋯ do card.
   Shift + botão direito continua abrindo o menu do navegador.

   - Os gráficos de maxisysCharts.js (MXCharts) e maxisysLive.js (MXLive) se registram
     sozinhos: todos ganham "Mostrar como tabela", "Copiar dados" e "Exportar CSV".
   - Quem passa `drill` na configuração do gráfico ganha "Expandir dados" no ponto clicado
     (mês, dia, fatia, item do ranking). A função recebe o índice do ponto e devolve, ou
     promete, as linhas por trás dele:
       { title, subtitle, summary: [{ label, value, note, tone }],
         sections: [{ key, label, columns, rows, empty, note, sort: { key, dir }, groupBy: [chaves] }] }
     coluna = { key, label, type: text|int|dec|money|kg|pct|date, digits, total: true,
                ratio: [numerador, denominador], href, sub, badge, tone, format(valor, linha) }
   - Visuais em HTML (barras de progresso, listas) usam MXDrill.attach(elemento, spec) com
     `data-mx-index` em cada item. spec = { name, label(i), table(), drill(i), canDrill(i), actions(i) }. */
(function () {
    'use strict';

    if (window.MXDrill) return; // carregado duas vezes na mesma página: o primeiro vale

    const specs = new WeakMap();
    const LONG_PRESS_MS = 520;
    const PAGE_SIZE = 200;

    // ------------------------------------------------------------------ formatos (pt-BR)
    const formatters = new Map();
    const numberFormat = (digits) => {
        const key = `n${digits}`;
        if (!formatters.has(key)) formatters.set(key, new Intl.NumberFormat('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits }));
        return formatters.get(key);
    };
    const moneyFormat = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', minimumFractionDigits: 2, maximumFractionDigits: 2 });
    // pct: razão (0,12 = 12%); pct100: já em pontos percentuais (12 = 12%), como os BIs mandam.
    const NUMERIC = new Set(['int', 'dec', 'money', 'kg', 'pct', 'pct100']);
    const isNumeric = (column) => NUMERIC.has(column.type) || (!column.type && column.numeric);
    const isEmpty = (value) => value == null || value === '' || (typeof value === 'number' && !Number.isFinite(value));

    function formatDate(value, withTime) {
        const text = String(value);
        const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(text);
        if (!match) return text;
        const day = `${match[3]}/${match[2]}/${match[1]}`;
        return withTime && match[4] ? `${day} ${match[4]}:${match[5]}` : day;
    }

    function formatValue(value, column, row) {
        if (column.format) return column.format(value, row);
        if (isEmpty(value)) return '–';
        switch (column.type) {
            case 'int': return numberFormat(0).format(value);
            case 'dec': return numberFormat(column.digits == null ? 2 : column.digits).format(value);
            case 'money': return moneyFormat.format(value);
            case 'kg': return `${numberFormat(column.digits || 0).format(value)} kg`;
            case 'pct': return `${numberFormat(column.digits == null ? 1 : column.digits).format(value * 100)}%`;
            case 'pct100': return `${numberFormat(column.digits == null ? 1 : column.digits).format(value)}%`;
            case 'date': return formatDate(value, false);
            case 'datetime': return formatDate(value, true);
            default: return String(value);
        }
    }

    // Valor para planilha: número com vírgula decimal e sem separador de milhar (o Excel em
    // português lê como número), data dd/mm/aaaa e percentual com o símbolo.
    function exportValue(value, column, row) {
        if (isEmpty(value)) return '';
        if (column.type === 'pct') return `${String(+(value * 100).toFixed(4)).replace('.', ',')}%`;
        if (column.type === 'pct100') return `${String(+Number(value).toFixed(4)).replace('.', ',')}%`;
        if (isNumeric(column)) return String(+Number(value).toFixed(column.type === 'money' ? 2 : 4)).replace('.', ',');
        if (column.type === 'date' || column.type === 'datetime') return formatDate(value, column.type === 'datetime');
        if (column.format && !isNumeric(column)) return column.format(value, row);
        return String(value);
    }

    // Número escrito em português dentro de um texto da tela ("1.234", "12,5%", "R$ 3,2 mil",
    // "4,1d"): serve para ordenar as tabelas lidas do HTML dos BIs como número.
    function parseLocaleNumber(text) {
        const match = /-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+(?:,\d+)?/.exec(String(text == null ? '' : text));
        if (!match) return null;
        const number = Number(match[0].replace(/\./g, '').replace(',', '.'));
        return Number.isFinite(number) ? number : null;
    }

    const normalize = (text) => String(text == null ? '' : text).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    const slug = (text) => normalize(text).replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'dados';
    const safeHref = (href) => (typeof href === 'string' && /^(\/(?!\/)|#|\?)/.test(href) ? href : null);

    // ------------------------------------------------------------------ DOM
    const NS = 'http://www.w3.org/2000/svg';
    const ICONS = {
        expand: '<path d="M4 6h16M4 12h16M4 18h10"/><path d="m17 15 3 3-3 3"/>',
        table: '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 10h16M4 14.5h16M10 10v9"/>',
        copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
        download: '<path d="M12 4v11M7 10l5 5 5-5"/><path d="M5 19h14"/>',
        link: '<path d="M14 5h5v5M19 5l-8 8"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
        filter: '<path d="M4 5h16l-6 8v5l-4 2v-7Z"/>',
        dots: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
        close: '<path d="m7 7 10 10M17 7 7 17"/>',
        back: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
        search: '<circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/>',
    };
    function icon(name) {
        const svg = document.createElementNS(NS, 'svg');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('class', 'mx-ico');
        svg.setAttribute('aria-hidden', 'true');
        svg.innerHTML = ICONS[name] || '';
        if (name === 'dots') svg.classList.add('is-filled');
        return svg;
    }
    function html(tag, className, text, parent) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        if (parent) parent.appendChild(node);
        return node;
    }

    // ------------------------------------------------------------------ registro
    // Nome do visual: o título do card (ou do painel dos BIs); sem cabeçalho, o rótulo da seção.
    function cardTitle(container) {
        if (container.dataset.mxName) return container.dataset.mxName;
        const panel = container.closest('.bi-panel');
        const panelTitle = panel && panel.querySelector(':scope > .bi-panel-head h2');
        if (panelTitle) return panelTitle.textContent.replace(/\s+/g, ' ').trim();
        const card = container.closest('.mx-card');
        const title = card && card.querySelector('.mx-card-title');
        if (title) return title.textContent.trim();
        const section = container.closest('.mx-section');
        const label = section && section.querySelector('.mx-section-label');
        return label ? [...label.childNodes].filter((node) => node.nodeType === 3).map((node) => node.textContent).join('').trim() : '';
    }

    function attach(container, spec) {
        if (!container || !spec) return;
        const previous = specs.get(container);
        specs.set(container, spec);
        container.setAttribute('data-mx-drill', '');
        if (!previous) addCardButton(container);
        if (spec.drill) addDrillBadge(container);
    }

    // Texto da dica dos pontos que abrem registros (gráficos, barras e tabelas dos BIs).
    const DRILL_HINT = 'Botão direito: expandir dados';

    function cardHead(container) {
        const panel = container.closest('.bi-panel');
        const card = panel || container.closest('.mx-card');
        if (!card) return null;
        const head = card.querySelector(panel ? ':scope > .bi-panel-head' : ':scope > .mx-card-head');
        if (head || panel) return head;
        // Card sem cabeçalho (o título fica no rótulo da seção, como nas pontes do Gerencial): uma
        // faixa só com o selo e o ⋯, para o indicador aparecer também ali.
        const tools = html('div', 'mx-card-head mx-card-tools');
        card.insertBefore(tools, card.firstChild);
        return tools;
    }

    /* Indicador de que o card tem "Expandir dados": um selo no cabeçalho, ao lado do ⋯. O
       clique abre a tabela do gráfico com o botão "Expandir" em cada linha (o caminho de quem
       usa teclado ou celular, sem botão direito). */
    function addDrillBadge(container) {
        container.classList.add('is-drillable');
        const head = cardHead(container);
        if (!head || head.querySelector('.mx-drill-badge')) return;
        const badge = html('button', 'mx-drill-badge');
        badge.type = 'button';
        badge.append(icon('expand'), html('span', null, 'Expandir dados'));
        badge.title = 'Este gráfico abre os registros por trás de cada ponto: clique com o botão direito numa barra, fatia ou linha (no celular, toque longo) ou clique aqui para escolher na tabela.';
        badge.setAttribute('aria-label', `Expandir dados: ${(specFor(container) || {}).name || 'gráfico'}`);
        badge.addEventListener('click', (event) => {
            event.stopPropagation();
            const spec = specFor(container);
            if (!spec) return;
            // Card de indicador: um ponto só, o selo já abre os registros.
            if (container.classList.contains('is-kpi-drill') && (!spec.canDrill || spec.canDrill(0))) expand(spec, 0, 'reset');
            else showTable(spec);
        });
        const menuButton = head.querySelector('.mx-card-menu');
        if (menuButton) {
            // O selo vai antes do ⋯ e passa a ser quem empurra os dois para a direita.
            if (menuButton.classList.contains('is-end')) { menuButton.classList.remove('is-end'); badge.classList.add('is-end'); }
            head.insertBefore(badge, menuButton);
        } else {
            head.appendChild(badge);
        }
    }

    function specFor(container) {
        const spec = specs.get(container);
        if (!spec) return null;
        if (!spec.name) spec.name = cardTitle(container) || spec.fallbackName || 'Gráfico';
        return spec;
    }

    // Botão ⋯ no cabeçalho do card ou do painel (um por cabeçalho): abre o mesmo menu, sem ponto.
    function addCardButton(container) {
        const panel = container.closest('.bi-panel');
        const head = cardHead(container);
        if (!head || head.querySelector('.mx-card-menu')) return;
        if (panel) head.classList.add('has-mx-menu');
        const button = html('button', 'mx-icon-btn mx-card-menu');
        button.type = 'button';
        button.title = 'Opções do gráfico';
        button.setAttribute('aria-haspopup', 'menu');
        button.setAttribute('aria-expanded', 'false');
        button.appendChild(icon('dots'));
        const pushedRight = panel ? head.querySelector(':scope > span') : head.querySelector('.mx-card-aside, [style*="margin-left:auto"], [style*="margin-left: auto"]');
        if (!pushedRight) button.classList.add('is-end');
        button.addEventListener('click', (event) => {
            event.stopPropagation();
            const spec = specFor(container);
            if (!spec) return;
            if (menu.isOpenFor(button)) { menu.close(); return; }
            const box = button.getBoundingClientRect();
            menu.open({ container, spec, index: null, x: box.right, y: box.bottom + 4, alignRight: true, opener: button, restore: button, keyboard: event.detail === 0 });
        });
        head.appendChild(button);
        const label = () => `Opções: ${(specFor(container) || {}).name || 'gráfico'}`;
        button.setAttribute('aria-label', label());
    }

    function pointFrom(container, target) {
        const hit = target && target.closest ? target.closest('[data-mx-index]') : null;
        if (!hit || !container.contains(hit)) return null;
        const index = Number(hit.getAttribute('data-mx-index'));
        return Number.isInteger(index) && index >= 0 ? { index, element: hit } : null;
    }

    // ------------------------------------------------------------------ menu
    const menu = (() => {
        let root = null;
        let current = null;
        function build() {
            root = html('div', 'mx-ctx');
            root.setAttribute('role', 'menu');
            root.tabIndex = -1;
            root.hidden = true;
            document.body.appendChild(root);
            root.addEventListener('keydown', onKey);
            root.addEventListener('contextmenu', (event) => event.preventDefault());
        }
        function items() { return [...root.querySelectorAll('.mx-ctx-item:not([disabled])')]; }
        function onKey(event) {
            const list = items();
            const position = list.indexOf(document.activeElement);
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                const step = event.key === 'ArrowDown' ? 1 : -1;
                const next = list[(position + step + list.length) % list.length] || list[0];
                if (next) next.focus();
            } else if (event.key === 'Home' || event.key === 'End') {
                event.preventDefault();
                const next = event.key === 'Home' ? list[0] : list[list.length - 1];
                if (next) next.focus();
            } else if (event.key === 'Escape') {
                event.preventDefault();
                close(true);
            } else if (event.key === 'Tab') {
                close(false);
            }
        }
        function addItem(label, iconName, run, options = {}) {
            const item = html('button', `mx-ctx-item${options.primary ? ' is-primary' : ''}`);
            item.type = 'button';
            item.setAttribute('role', 'menuitem');
            item.tabIndex = -1;
            item.appendChild(icon(iconName));
            html('span', null, label, item);
            if (options.disabled) item.disabled = true;
            // O foco volta para quem abriu o menu (o ⋯ ou o ponto) antes da ação: a janela que
            // a ação abre devolve o foco para lá quando fechar.
            item.addEventListener('click', () => { close(true); run(); });
            root.appendChild(item);
            return item;
        }
        function open(context) {
            if (!root) build();
            close(false);
            current = context;
            const { spec, index } = context;
            root.replaceChildren();
            const head = html('div', 'mx-ctx-head', null, root);
            head.setAttribute('aria-hidden', 'true');
            html('strong', null, spec.name, head);
            const pointLabel = index != null && spec.label ? spec.label(index) : null;
            if (pointLabel) html('span', null, pointLabel, head);
            root.setAttribute('aria-label', `${spec.name}${pointLabel ? ` · ${pointLabel}` : ''}`);

            const canDrill = !!spec.drill && index != null && (!spec.canDrill || spec.canDrill(index));
            if (canDrill) addItem(`Expandir dados${pointLabel ? ` de ${pointLabel}` : ''}`, 'expand', () => expand(spec, index, 'reset'), { primary: true });
            (index != null && spec.actions ? spec.actions(index) || [] : []).forEach((action) => {
                const href = safeHref(action.href);
                addItem(action.label, action.icon || (href ? 'link' : 'filter'), () => {
                    if (href) window.location.href = href;
                    else if (action.run) action.run();
                });
            });
            if (root.querySelector('.mx-ctx-item')) html('div', 'mx-ctx-sep', null, root).setAttribute('role', 'separator');
            const hasTable = !!spec.table;
            addItem('Mostrar como tabela', 'table', () => showTable(spec), { disabled: !hasTable });
            addItem('Copiar dados', 'copy', () => copyRows(chartTable(spec), spec.name), { disabled: !hasTable });
            addItem('Exportar CSV', 'download', () => downloadCsv(chartTable(spec), spec.name), { disabled: !hasTable });
            const hint = spec.drill && index == null
                ? 'Clique com o botão direito numa barra ou item para expandir os dados dele.'
                : 'Shift + botão direito abre o menu do navegador.';
            html('p', 'mx-ctx-foot', hint, root).setAttribute('aria-hidden', 'true');

            root.hidden = false;
            root.style.left = '0px';
            root.style.top = '0px';
            const width = root.offsetWidth;
            const height = root.offsetHeight;
            const left = context.alignRight ? context.x - width : context.x;
            root.style.left = `${Math.max(8, Math.min(left, window.innerWidth - width - 8))}px`;
            root.style.top = `${Math.max(8, context.y + height > window.innerHeight - 8 ? context.y - height : context.y)}px`;
            if (context.opener) context.opener.setAttribute('aria-expanded', 'true');
            const first = items()[0];
            if (context.keyboard && first) first.focus();
            else root.focus({ preventScroll: true });
        }
        function close(restoreFocus) {
            if (!root || root.hidden) return;
            root.hidden = true;
            const opener = current && current.opener;
            if (opener) opener.setAttribute('aria-expanded', 'false');
            if (restoreFocus && current && current.restore && document.contains(current.restore)) current.restore.focus({ preventScroll: true });
            current = null;
        }
        document.addEventListener('pointerdown', (event) => {
            if (!root || root.hidden || root.contains(event.target)) return;
            // O próprio botão ⋯ alterna o menu no clique; fechar aqui o reabriria em seguida.
            if (current && current.opener && current.opener.contains(event.target)) return;
            close(false);
        }, true);
        window.addEventListener('resize', () => close(false));
        window.addEventListener('blur', () => close(false));
        document.addEventListener('scroll', (event) => { if (root && !root.hidden && !root.contains(event.target)) close(false); }, true);
        return { open, close, isOpenFor: (opener) => !!(root && !root.hidden && current && current.opener === opener) };
    })();

    // Toque longo (celular): o iPhone não dispara "contextmenu"; no Android ele vem depois.
    let pressTimer = null;
    let pressStart = null;
    let suppressNativeMenu = 0;
    let swallowClick = false;

    // Botão direito em qualquer gráfico registrado.
    // Campo de formulário (a observação da Agenda, por exemplo) fica com o menu do navegador.
    const FORM_FIELDS = 'input, textarea, select, [contenteditable="true"]';
    document.addEventListener('contextmenu', (event) => {
        if (event.shiftKey) return;
        if (event.target.closest && event.target.closest(FORM_FIELDS)) return;
        const container = event.target.closest ? event.target.closest('[data-mx-drill]') : null;
        if (!container) return;
        if (Date.now() - suppressNativeMenu < 900) { event.preventDefault(); return; }
        const spec = specFor(container);
        if (!spec) return;
        event.preventDefault();
        clearTimeout(pressTimer);
        pressTimer = null;
        if (event.pointerType === 'touch') swallowClick = true; // toque longo do Android
        const point = pointFrom(container, event.target);
        // Menu aberto pelo teclado (tecla Menu ou Shift+F10) chega sem coordenadas do mouse.
        let { clientX: x, clientY: y } = event;
        const keyboard = !x && !y;
        if (keyboard) {
            const box = (point ? point.element : container).getBoundingClientRect();
            x = box.left + Math.min(box.width / 2, 24);
            y = box.top + Math.min(box.height / 2, 24);
        }
        menu.open({ container, spec, index: point ? point.index : null, x, y, keyboard, restore: document.activeElement });
    });

    // O dedo levantando depois do toque longo não pode virar clique (o clique filtra a página):
    // descarta só o clique que chega antes de um novo toque.
    document.addEventListener('click', (event) => {
        if (!swallowClick) return;
        swallowClick = false;
        event.preventDefault();
        event.stopPropagation();
    }, true);
    document.addEventListener('pointerdown', (event) => {
        swallowClick = false;
        if (event.pointerType !== 'touch') return;
        if (event.target.closest && event.target.closest(FORM_FIELDS)) return;
        const container = event.target.closest ? event.target.closest('[data-mx-drill]') : null;
        if (!container) return;
        pressStart = { x: event.clientX, y: event.clientY };
        const target = event.target;
        clearTimeout(pressTimer);
        pressTimer = setTimeout(() => {
            pressTimer = null;
            const spec = specFor(container);
            if (!spec) return;
            suppressNativeMenu = Date.now();
            swallowClick = true;
            const point = pointFrom(container, target);
            menu.open({ container, spec, index: point ? point.index : null, x: pressStart.x, y: pressStart.y, keyboard: false });
        }, LONG_PRESS_MS);
    }, { capture: true, passive: true });
    const cancelPress = (event) => {
        if (!pressTimer) return;
        if (event.type === 'pointermove' && pressStart && Math.hypot(event.clientX - pressStart.x, event.clientY - pressStart.y) < 10) return;
        clearTimeout(pressTimer);
        pressTimer = null;
    };
    ['pointerup', 'pointercancel', 'pointermove'].forEach((type) => document.addEventListener(type, cancelPress, { passive: true }));

    // ------------------------------------------------------------------ tabela do gráfico
    function chartTable(spec) {
        const table = spec.table ? spec.table() : null;
        return table ? { columns: table.columns || [], rows: table.rows || [] } : { columns: [], rows: [] };
    }

    function showTable(spec) {
        const table = chartTable(spec);
        const title = spec.tableTitle || 'Dados do gráfico';
        const section = { key: 'chart', label: title, columns: table.columns, rows: table.rows, empty: 'Sem dados para mostrar.' };
        if (spec.drill) {
            section.rowAction = {
                label: 'Expandir',
                enabled: (row) => row.__index != null && (!spec.canDrill || spec.canDrill(row.__index)),
                run: (row) => expand(spec, row.__index, 'push'),
            };
        }
        modal.show({ kicker: spec.name, title, subtitle: table.note || '', sections: [section] }, { reset: true });
    }

    // ------------------------------------------------------------------ expandir dados
    // mode: 'reset' (abre do zero), 'push' (a partir da tabela do gráfico: o Voltar retorna
    // a ela) ou 'replace' (troca a tela atual, como no "Tentar de novo").
    let loadToken = 0;
    function expand(spec, index, mode = 'reset') {
        const token = ++loadToken;
        const label = spec.label ? spec.label(index) : '';
        const meta = { kicker: spec.name, title: label || 'Detalhe' };
        const how = { reset: mode === 'reset', push: mode === 'push' };
        const retry = () => expand(spec, index, 'replace');
        let result;
        try {
            result = spec.drill(index);
        } catch (error) {
            modal.show({ ...meta, error }, { ...how, retry });
            return;
        }
        if (result && typeof result.then === 'function') {
            modal.show({ ...meta, loading: true }, how);
            result.then((value) => {
                if (token === loadToken) modal.show({ ...meta, ...value }, {});
            }, (error) => {
                if (token === loadToken) modal.show({ ...meta, error }, { retry });
            });
            return;
        }
        modal.show({ ...meta, ...(result || {}) }, how);
    }

    // ------------------------------------------------------------------ cópia e exportação
    function rowsAsText(table, separator) {
        const columns = table.columns.filter((column) => !column.hidden);
        const escape = (value) => {
            const text = String(value == null ? '' : value);
            return /["\n\r]/.test(text) || text.includes(separator) ? `"${text.replace(/"/g, '""')}"` : text;
        };
        const header = [];
        columns.forEach((column) => {
            header.push(column.label);
            if (column.sub && table.rows.some((row) => !isEmpty(row[column.sub]))) header.push(`${column.label} (detalhe)`);
        });
        const lines = [header.map(escape).join(separator)];
        table.rows.forEach((row) => {
            const cells = [];
            columns.forEach((column) => {
                cells.push(exportValue(row[column.key], column, row));
                if (column.sub && table.rows.some((other) => !isEmpty(other[column.sub]))) cells.push(isEmpty(row[column.sub]) ? '' : String(row[column.sub]));
            });
            lines.push(cells.map(escape).join(separator));
        });
        return lines.join('\r\n');
    }

    function downloadCsv(table, name) {
        const blob = new Blob(['﻿', rowsAsText(table, ';')], { type: 'text/csv;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = html('a');
        link.href = url;
        link.download = `${slug(name)}.csv`;
        (modal.isOpen() ? modal.root() : document.body).appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
        toast(`${table.rows.length} linha(s) exportada(s) em CSV.`);
    }

    async function copyRows(table, name) {
        const text = rowsAsText(table, '\t');
        let done = false;
        if (navigator.clipboard && window.isSecureContext) {
            try { await navigator.clipboard.writeText(text); done = true; } catch (error) { done = false; }
        }
        if (!done) {
            // Fora de HTTPS o navegador não libera a área de transferência pela API nova.
            const area = html('textarea', 'mx-drill-clip');
            area.value = text;
            area.setAttribute('readonly', '');
            (modal.isOpen() ? modal.root() : document.body).appendChild(area);
            area.select();
            try { done = document.execCommand('copy'); } catch (error) { done = false; }
            area.remove();
        }
        toast(done ? `${table.rows.length} linha(s) copiada(s): cole no Excel.` : 'Não foi possível copiar: use Exportar CSV.', !done);
    }

    let toastNode = null;
    let toastTimer = null;
    function toast(message, isError) {
        const host = modal.isOpen() ? modal.root() : document.body;
        if (!toastNode) {
            toastNode = html('div', 'mx-drill-toast');
            toastNode.setAttribute('role', 'status');
            toastNode.setAttribute('aria-live', 'polite');
        }
        if (toastNode.parentNode !== host) host.appendChild(toastNode);
        toastNode.textContent = message;
        toastNode.classList.toggle('is-bad', !!isError);
        toastNode.classList.add('is-visible');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => toastNode.classList.remove('is-visible'), 2600);
    }

    // ------------------------------------------------------------------ agrupar, somar e ordenar
    // total: true (ou 'sum') soma; 'avg' faz a média das linhas que têm valor; ratio: [a, b]
    // recalcula a razão das somas (ex.: R$/kg, margem).
    function totalsOf(rows, columns) {
        const totals = {};
        columns.forEach((column) => {
            if (!column.total || !isNumeric(column)) return;
            if (column.total === 'avg') {
                const values = rows.map((row) => row[column.key]).filter((value) => !isEmpty(value)).map(Number);
                totals[column.key] = values.length ? values.reduce((acc, value) => acc + value, 0) / values.length : null;
                return;
            }
            totals[column.key] = rows.reduce((acc, row) => acc + (Number(row[column.key]) || 0), 0);
        });
        columns.forEach((column) => {
            if (!column.ratio) return;
            const [top, bottom] = column.ratio;
            const sumOf = (key) => rows.reduce((acc, row) => acc + (Number(row[key]) || 0), 0);
            const denominator = sumOf(bottom);
            totals[column.key] = denominator ? sumOf(top) / denominator : null;
        });
        return totals;
    }

    function groupRows(rows, columns, key) {
        const column = columns.find((item) => item.key === key);
        const groups = new Map();
        rows.forEach((row) => {
            const id = isEmpty(row[key]) ? '–' : String(row[key]);
            if (!groups.has(id)) groups.set(id, []);
            groups.get(id).push(row);
        });
        const keep = columns.filter((item) => item.key !== key && ((item.total && isNumeric(item)) || item.ratio));
        const hiddenKeys = new Set();
        columns.forEach((item) => { if (item.ratio) item.ratio.forEach((part) => hiddenKeys.add(part)); });
        const grouped = [...groups.values()].map((members) => {
            const first = members[0];
            const row = { [key]: first[key], __count: members.length };
            if (column && column.href) row[column.href] = members.every((item) => item[column.href] === first[column.href]) ? first[column.href] : null;
            if (column && column.sub) row[column.sub] = members.every((item) => item[column.sub] === first[column.sub]) ? first[column.sub] : null;
            Object.assign(row, totalsOf(members, keep));
            hiddenKeys.forEach((part) => { row[part] = members.reduce((acc, item) => acc + (Number(item[part]) || 0), 0); });
            return row;
        });
        const groupedColumns = [
            { ...(column || { key, label: key }), badge: null, tone: null, width: null },
            { key: '__count', label: 'Linhas', type: 'int', total: true },
            ...keep,
        ];
        return { rows: grouped, columns: groupedColumns };
    }

    function compare(column, direction) {
        const factor = direction === 'asc' ? 1 : -1;
        const collator = new Intl.Collator('pt-BR', { numeric: true, sensitivity: 'base' });
        return (a, b) => {
            const left = a[column.key];
            const right = b[column.key];
            if (isEmpty(left) && isEmpty(right)) return 0;
            if (isEmpty(left)) return 1;
            if (isEmpty(right)) return -1;
            if (isNumeric(column)) return (Number(left) - Number(right)) * factor;
            if (column.sortNumeric) {
                const leftNumber = parseLocaleNumber(left);
                const rightNumber = parseLocaleNumber(right);
                if (leftNumber != null && rightNumber != null) return (leftNumber - rightNumber) * factor;
            }
            return collator.compare(String(left), String(right)) * factor;
        };
    }

    // ------------------------------------------------------------------ janela
    const modal = (() => {
        let dialog = null;
        let parts = null;
        let stack = [];
        let view = null;
        let retry = null;
        let returnFocus = null;

        function build() {
            dialog = html('dialog', 'mx-drill');
            dialog.setAttribute('aria-labelledby', 'mxDrillTitle');
            const panel = html('div', 'mx-drill-panel', null, dialog);
            const head = html('header', 'mx-drill-head', null, panel);
            const back = html('button', 'mx-icon-btn mx-drill-back', null, head);
            back.type = 'button';
            back.setAttribute('aria-label', 'Voltar');
            back.title = 'Voltar';
            back.appendChild(icon('back'));
            const heading = html('div', 'mx-drill-heading', null, head);
            const kicker = html('span', 'mx-kicker', null, heading);
            const title = html('h2', 'mx-drill-title', null, heading);
            title.id = 'mxDrillTitle';
            const subtitle = html('p', 'mx-drill-sub', null, heading);
            const close = html('button', 'mx-icon-btn mx-drill-close', null, head);
            close.type = 'button';
            close.setAttribute('aria-label', 'Fechar');
            close.title = 'Fechar (Esc)';
            close.appendChild(icon('close'));
            const summary = html('div', 'mx-drill-summary', null, panel);
            const tabs = html('div', 'mx-drill-tabs', null, panel);
            tabs.setAttribute('role', 'tablist');
            const tools = html('div', 'mx-drill-tools', null, panel);
            const searchBox = html('label', 'mx-drill-search', null, tools);
            searchBox.appendChild(icon('search'));
            const search = html('input', 'mx-input', null, searchBox);
            search.type = 'search';
            search.placeholder = 'Filtrar linhas';
            search.setAttribute('aria-label', 'Filtrar linhas da tabela');
            const groupWrap = html('label', 'mx-drill-group', null, tools);
            html('span', null, 'Agrupar por', groupWrap);
            const group = html('select', 'mx-select', null, groupWrap);
            const count = html('span', 'mx-drill-count', null, tools);
            count.setAttribute('aria-live', 'polite');
            const actions = html('div', 'mx-drill-actions', null, tools);
            const copy = html('button', 'mx-btn mx-btn-ghost', null, actions);
            copy.type = 'button';
            copy.append(icon('copy'), document.createTextNode('Copiar'));
            copy.title = 'Copiar a tabela para colar no Excel';
            const csv = html('button', 'mx-btn mx-btn-ghost', null, actions);
            csv.type = 'button';
            csv.append(icon('download'), document.createTextNode('Exportar CSV'));
            const note = html('p', 'mx-drill-note', null, panel);
            const body = html('div', 'mx-drill-body', null, panel);
            body.tabIndex = 0;
            body.setAttribute('role', 'region');
            body.setAttribute('aria-label', 'Linhas');
            const table = html('table', 'mx-table mx-drill-table', null, body);
            const thead = html('thead', null, null, table);
            const tbody = html('tbody', null, null, table);
            const tfoot = html('tfoot', null, null, table);
            const empty = html('p', 'mx-empty', null, body);
            const more = html('button', 'mx-btn mx-btn-ghost mx-drill-more', null, body);
            more.type = 'button';
            const state = html('div', 'mx-drill-state', null, panel);
            parts = { panel, back, kicker, title, subtitle, close, summary, tabs, tools, search, groupWrap, group, count, copy, csv, note, body, table, thead, tbody, tfoot, empty, more, state };

            close.addEventListener('click', () => dialog.close());
            back.addEventListener('click', () => { if (stack.length) { view = stack.pop(); render(); } });
            dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
            // O "close" chega depois (tarefa própria): se a janela já foi reaberta, não apaga nada.
            dialog.addEventListener('close', () => {
                if (dialog.open) return;
                loadToken += 1;
                stack = [];
                view = null;
                document.documentElement.classList.remove('mx-drill-open');
                if (returnFocus && document.contains(returnFocus)) returnFocus.focus({ preventScroll: true });
                returnFocus = null;
            });
            dialog.addEventListener('contextmenu', (event) => event.stopPropagation());
            search.addEventListener('input', () => { if (!view) return; view.query = search.value; view.limit = PAGE_SIZE; renderRows(); });
            group.addEventListener('change', () => { if (!view) return; view.groupBy = group.value || null; view.sortKey = null; view.limit = PAGE_SIZE; renderRows(); });
            more.addEventListener('click', () => { if (!view) return; view.limit += PAGE_SIZE; renderRows(); });
            copy.addEventListener('click', () => { const table = currentTable(); if (table) copyRows(table, exportName()); });
            csv.addEventListener('click', () => { const table = currentTable(); if (table) downloadCsv(table, exportName()); });
            document.body.appendChild(dialog);
        }

        function show(result, options = {}) {
            if (!dialog) build();
            if (options.push && view) stack.push(view);
            else if (options.reset) stack = [];
            retry = options.retry || null;
            view = { result, section: 0, query: '', groupBy: null, sortKey: null, sortDir: 'desc', limit: PAGE_SIZE };
            const first = (result.sections || [])[0];
            if (first && first.sort) { view.sortKey = first.sort.key; view.sortDir = first.sort.dir || 'desc'; }
            render();
            if (!dialog.open) {
                const active = document.activeElement;
                returnFocus = active && active !== document.body && !active.closest('.mx-ctx') ? active : null;
                dialog.showModal();
                document.documentElement.classList.add('mx-drill-open');
                parts.close.focus({ preventScroll: true });
            }
        }

        function exportName() {
            const result = view.result;
            const section = (result.sections || [])[view.section];
            return [result.kicker, result.title, section && result.sections.length > 1 ? section.label : ''].filter(Boolean).join(' ');
        }

        function render() {
            const { result } = view;
            parts.back.hidden = !stack.length;
            parts.kicker.textContent = result.kicker || '';
            parts.title.textContent = result.title || '';
            parts.subtitle.textContent = result.subtitle || '';
            parts.subtitle.hidden = !result.subtitle;
            parts.summary.replaceChildren();
            (result.summary || []).forEach((fact) => {
                const box = html('div', `mx-drill-fact${fact.tone ? ` is-${fact.tone}` : ''}`, null, parts.summary);
                html('span', null, fact.label, box);
                html('strong', null, fact.value, box);
                if (fact.note) html('small', null, fact.note, box);
            });
            parts.summary.hidden = !(result.summary || []).length;
            parts.state.replaceChildren();
            parts.state.hidden = true;
            const ready = !result.loading && !result.error;
            parts.tools.hidden = !ready;
            parts.body.hidden = !ready;
            parts.tabs.hidden = true;
            parts.note.hidden = true;
            if (result.loading) {
                parts.state.hidden = false;
                parts.state.className = 'mx-drill-state is-loading';
                const lines = html('div', 'mx-drill-skeleton', null, parts.state);
                for (let index = 0; index < 6; index += 1) html('i', null, null, lines);
                html('p', null, 'Consultando o ERP Senior…', parts.state);
                return;
            }
            if (result.error) {
                parts.state.hidden = false;
                parts.state.className = 'mx-drill-state is-error';
                html('strong', null, 'Não foi possível expandir os dados.', parts.state);
                html('p', null, result.error.message || String(result.error), parts.state);
                if (retry) {
                    const again = html('button', 'mx-btn', 'Tentar de novo', parts.state);
                    again.type = 'button';
                    again.addEventListener('click', retry);
                }
                return;
            }
            renderTabs();
            renderSection();
        }

        function renderTabs() {
            const sections = view.result.sections || [];
            parts.tabs.replaceChildren();
            parts.tabs.hidden = sections.length < 2;
            sections.forEach((section, index) => {
                const tab = html('button', `mx-drill-tab${index === view.section ? ' is-active' : ''}`, null, parts.tabs);
                tab.type = 'button';
                tab.setAttribute('role', 'tab');
                tab.setAttribute('aria-selected', index === view.section ? 'true' : 'false');
                tab.tabIndex = index === view.section ? 0 : -1;
                html('span', null, section.label, tab);
                html('b', null, numberFormat(0).format((section.rows || []).length), tab);
                tab.addEventListener('click', () => selectSection(index));
                tab.addEventListener('keydown', (event) => {
                    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
                    event.preventDefault();
                    const next = (index + (event.key === 'ArrowRight' ? 1 : -1) + sections.length) % sections.length;
                    selectSection(next);
                    parts.tabs.children[next].focus();
                });
            });
        }

        function selectSection(index) {
            const section = view.result.sections[index];
            view.section = index;
            view.query = '';
            view.groupBy = null;
            view.limit = PAGE_SIZE;
            view.sortKey = section.sort ? section.sort.key : null;
            view.sortDir = section.sort ? section.sort.dir || 'desc' : 'desc';
            renderTabs();
            renderSection();
        }

        function renderSection() {
            const section = (view.result.sections || [])[view.section] || { columns: [], rows: [] };
            parts.search.value = view.query;
            parts.note.textContent = section.note || '';
            parts.note.hidden = !section.note;
            const options = (section.groupBy || []).map((key) => section.columns.find((column) => column.key === key)).filter(Boolean);
            parts.group.replaceChildren();
            parts.groupWrap.hidden = !options.length;
            if (options.length) {
                html('option', null, 'Nenhum (todas as linhas)', parts.group).value = '';
                options.forEach((column) => { const option = html('option', null, column.label, parts.group); option.value = column.key; });
                parts.group.value = view.groupBy || '';
            }
            parts.search.parentNode.hidden = (section.rows || []).length < 8;
            renderRows();
        }

        function currentRows() {
            const section = (view.result.sections || [])[view.section] || { columns: [], rows: [] };
            let rows = section.rows || [];
            let columns = (section.columns || []).filter((column) => !column.hidden);
            if (view.groupBy) ({ rows, columns } = groupRows(rows, section.columns || [], view.groupBy));
            const term = normalize(view.query.trim());
            if (term) {
                const textual = columns.filter((column) => !isNumeric(column));
                rows = rows.filter((row) => textual.some((column) => normalize(`${row[column.key] == null ? '' : formatValue(row[column.key], column, row)} ${column.sub && row[column.sub] ? row[column.sub] : ''}`).includes(term)));
            }
            const sortColumn = columns.find((column) => column.key === view.sortKey);
            if (sortColumn) rows = rows.slice().sort(compare(sortColumn, view.sortDir));
            return { section, rows, columns };
        }

        function currentTable() {
            if (!view || view.result.loading || view.result.error) return null;
            const { rows, columns } = currentRows();
            return { rows, columns };
        }

        function renderRows() {
            const { section, rows, columns } = currentRows();
            const action = !view.groupBy ? section.rowAction : null;
            // Cabeçalho com ordenação (clique alterna maior/menor primeiro).
            parts.thead.replaceChildren();
            const headRow = html('tr', null, null, parts.thead);
            columns.forEach((column) => {
                const th = html('th', isNumeric(column) ? 'is-num' : '', null, headRow);
                const button = html('button', 'mx-drill-sort', column.label, th);
                button.type = 'button';
                th.setAttribute('aria-sort', view.sortKey === column.key ? (view.sortDir === 'asc' ? 'ascending' : 'descending') : 'none');
                button.addEventListener('click', () => {
                    if (view.sortKey === column.key) view.sortDir = view.sortDir === 'asc' ? 'desc' : 'asc';
                    else { view.sortKey = column.key; view.sortDir = isNumeric(column) || column.sortNumeric || column.type === 'date' || column.type === 'datetime' ? 'desc' : 'asc'; }
                    renderRows();
                });
            });
            if (action) html('th', 'mx-drill-action-col', '', headRow).setAttribute('aria-label', 'Ações');

            parts.tbody.replaceChildren();
            const fragment = document.createDocumentFragment();
            rows.slice(0, view.limit).forEach((row) => {
                const tr = html('tr', row.__tone ? `is-${row.__tone}` : null, null, fragment);
                columns.forEach((column) => tr.appendChild(cell(row, column)));
                if (action) {
                    const td = html('td', 'mx-drill-action-col', null, tr);
                    if (!action.enabled || action.enabled(row)) {
                        const button = html('button', 'mx-btn mx-btn-ghost mx-drill-row-btn', null, td);
                        button.type = 'button';
                        button.append(icon('expand'), document.createTextNode(action.label));
                        button.setAttribute('aria-label', `${action.label}: ${row[columns[0].key]}`);
                        button.addEventListener('click', () => action.run(row));
                    }
                }
            });
            parts.tbody.appendChild(fragment);

            parts.tfoot.replaceChildren();
            const totals = totalsOf(rows, columns);
            if (rows.length > 1 && Object.keys(totals).length) {
                const tr = html('tr', null, null, parts.tfoot);
                columns.forEach((column, index) => {
                    const td = html('td', isNumeric(column) ? 'is-num' : '', null, tr);
                    if (index === 0) td.textContent = `Total${view.query ? ' filtrado' : ''}`;
                    else if (column.key in totals) td.textContent = formatValue(totals[column.key], column, totals);
                });
                if (action) html('td', null, null, tr);
            }

            parts.empty.hidden = rows.length > 0;
            parts.empty.textContent = view.query ? 'Nenhuma linha com esse filtro.' : (section.empty || 'Nenhum registro.');
            parts.table.hidden = !rows.length;
            const all = view.groupBy ? rows.length : (section.rows || []).length;
            parts.count.textContent = view.query
                ? `${numberFormat(0).format(rows.length)} de ${numberFormat(0).format(all)} linha(s)`
                : `${numberFormat(0).format(rows.length)} linha(s)${view.groupBy ? ` agrupada(s) de ${numberFormat(0).format((section.rows || []).length)}` : ''}`;
            const left = rows.length - view.limit;
            parts.more.hidden = left <= 0;
            parts.more.textContent = `Mostrar mais ${numberFormat(0).format(Math.min(left, PAGE_SIZE))} (faltam ${numberFormat(0).format(left)})`;
            parts.copy.disabled = !rows.length;
            parts.csv.disabled = !rows.length;
        }

        function cell(row, column) {
            // Texto longo (cliente, produto, orientação) quebra linha; o resto fica numa linha só.
            const wrap = column.wrap || (column.wrap !== false && (column.sub || column.href) && !isNumeric(column));
            const td = html('td', [isNumeric(column) ? 'is-num' : '', column.strong ? 'is-strong' : '', wrap ? 'is-wrap' : ''].filter(Boolean).join(' ') || null);
            const value = row[column.key];
            const text = formatValue(value, column, row);
            const href = column.href ? safeHref(row[column.href]) : null;
            if (href && !isEmpty(value)) html('a', null, text, td).href = href;
            else td.textContent = text;
            if (column.badge && !isEmpty(row[column.badge])) {
                const badge = html('span', `mx-badge${row[column.tone] ? ` is-${row[column.tone]}` : ''}`, row[column.badge]);
                if (td.textContent) td.append(' ');
                td.appendChild(badge);
            }
            if (column.sub && !isEmpty(row[column.sub])) html('span', 'mx-cell-sub', row[column.sub], td);
            return td;
        }

        return { show, isOpen: () => !!(dialog && dialog.open), root: () => dialog };
    })();

    // ------------------------------------------------------------------ BIs em HTML (.bi-panel)
    /* Os BIs do TI, Viagens, Romaneios e Compras desenham barras, colunas e tabelas em HTML no
       servidor. Cada visual dentro de um .bi-panel entra no menu sozinho, com a tabela lida da
       tela. No visual, opcionais:
         data-mx-name="Nome"                     nome no menu (senão, o título do painel)
         data-mx-label-name="Mês"                título da 1ª coluna da tabela
         data-mx-columns="Abertos:int:sum|Fechados:int:sum"   nome:tipo:total de data-mx-values
         data-mx-dim="tipo"                      "Expandir dados": a dimensão que o servidor filtra
       No painel (ou acima): data-mx-detail-url="/dashes/.../detalhe/?<recorte da página>".
       Em cada ponto: data-mx-label, data-mx-values="12|4" e data-mx-key (o valor do filtro). */
    const BI_VISUALS = '.bi-chart, .bi-bars, .bi-table, .bi-heat, .bi-aging, .tv-dev';
    const squash = (node) => (node ? node.textContent.replace(/\s+/g, ' ').trim() : '');

    function biPoints(visual) {
        const children = [...visual.children];
        if (visual.matches('.bi-chart')) return children.filter((node) => node.tagName === 'DIV');
        if (visual.matches('.bi-bars')) return children.filter((node) => node.matches('.bi-bar'));
        if (visual.matches('.bi-table')) return [...visual.querySelectorAll(':scope > tbody > tr')].filter((row) => row.children.length > 1);
        if (visual.matches('.bi-heat')) return children.filter((node) => node.matches('.bi-heat-row:not(.is-head)'));
        if (visual.matches('.tv-dev')) return children.filter((node) => node.matches('.tv-dev-row'));
        return children.filter((node) => node.tagName === 'DIV'); // .bi-aging
    }

    function biPointLabel(point, visual) {
        if (point.dataset.mxLabel) return point.dataset.mxLabel;
        if (visual.matches('.bi-chart')) {
            const title = (point.getAttribute('title') || '').split(':')[0].trim();
            return title || squash(point.querySelector('small'));
        }
        if (visual.matches('.bi-bars')) return squash(point.querySelector('.bi-bar-head strong'));
        if (visual.matches('.bi-table')) {
            const cell = point.querySelector('td');
            return squash(cell && (cell.querySelector('strong') || cell));
        }
        if (visual.matches('.tv-dev')) return squash(point.querySelector('.tv-plate'));
        return squash(point.querySelector('span')); // .bi-heat-row, .bi-aging
    }

    const biPointSub = (point, visual) => (visual.matches('.bi-bars') ? squash(point.querySelector('.bi-bar-sub')) : '');

    // Texto do valor do ponto, em partes: "1.234 · 12%" -> ["1.234", "12%"].
    function biPointParts(point, visual) {
        let parts;
        if (visual.matches('.bi-chart')) {
            // Sem a linha da dica que o scanner acrescenta ao title.
            const title = (point.getAttribute('title') || '').replace(`\n${DRILL_HINT}`, '');
            const cut = title.indexOf(':');
            parts = [cut >= 0 ? title.slice(cut + 1) : squash(point.querySelector('b'))];
        } else if (visual.matches('.bi-bars')) {
            parts = [squash(point.querySelector('.bi-bar-head span'))];
        } else if (visual.matches('.tv-dev')) {
            const small = point.querySelector('small');
            parts = small ? [...small.childNodes].filter((node) => node.nodeType === 3).map((node) => node.textContent) : [];
        } else {
            parts = [squash(point.querySelector('strong'))];
        }
        return parts.flatMap((text) => text.split(/\s+·\s+|,\s+/)).map((text) => text.replace(/\s+/g, ' ').trim()).filter(Boolean);
    }

    function biDeclaredColumns(visual) {
        return (visual.dataset.mxColumns || '').split('|').filter(Boolean).map((part) => {
            const [label, type, total] = part.split(':').map((piece) => (piece || '').trim());
            return { label, type: type || 'dec', total: total === 'sum' ? true : total || null };
        });
    }

    function biTable(visual) {
        const points = biPoints(visual);
        if (visual.matches('.bi-table')) {
            const heads = [...visual.querySelectorAll(':scope > thead th')].map(squash);
            const columns = heads.map((label, index) => ({ key: `c${index}`, label: label || `Coluna ${index + 1}`, sub: `c${index}_sub`, sortNumeric: index > 0 }));
            const rows = points.map((tr, rowIndex) => {
                const row = { __index: rowIndex };
                [...tr.children].forEach((td, index) => {
                    const strong = td.querySelector('.bi-cell strong');
                    const small = td.querySelector('.bi-cell small');
                    row[`c${index}`] = strong ? squash(strong) : squash(td);
                    if (small && squash(small)) row[`c${index}_sub`] = squash(small);
                });
                return row;
            });
            return { columns, rows };
        }
        if (visual.matches('.bi-heat')) {
            const head = [...visual.children].find((node) => node.matches('.bi-heat-row.is-head'));
            const hours = head ? [...head.children].slice(1).map(squash) : [];
            const columns = [
                { key: 'label', label: visual.dataset.mxLabelName || 'Dia' },
                ...hours.map((label, index) => ({ key: `h${index}`, label, type: 'int', total: true })),
                { key: 'sum', label: 'Total', type: 'int', total: true, strong: true },
            ];
            const rows = points.map((line, rowIndex) => {
                const cells = [...line.querySelectorAll('i')].map((cell) => parseLocaleNumber(squash(cell)) || 0);
                const row = { label: squash(line.querySelector('span')), sum: cells.reduce((acc, value) => acc + value, 0), __index: rowIndex };
                cells.forEach((value, index) => { row[`h${index}`] = value; });
                return row;
            });
            return { columns, rows };
        }
        const declared = biDeclaredColumns(visual);
        const columns = [{ key: 'label', label: visual.dataset.mxLabelName || 'Item', sub: 'sub' }];
        const base = (point, rowIndex) => ({ label: biPointLabel(point, visual), sub: biPointSub(point, visual), __index: rowIndex });
        if (declared.length && points.some((point) => point.dataset.mxValues != null)) {
            declared.forEach((column, index) => columns.push({ key: `v${index}`, label: column.label, type: column.type, total: column.total, digits: column.type === 'dec' || column.type === 'pct100' ? 1 : undefined }));
            const rows = points.map((point, rowIndex) => {
                const values = (point.dataset.mxValues || '').split('|');
                const row = base(point, rowIndex);
                declared.forEach((column, index) => {
                    const raw = values[index];
                    const number = raw == null || raw === '' ? null : Number(String(raw).replace(',', '.'));
                    row[`v${index}`] = !NUMERIC.has(column.type) ? (raw || null) : Number.isFinite(number) ? number : null;
                });
                return row;
            });
            return { columns, rows };
        }
        const parts = points.map((point) => biPointParts(point, visual));
        const width = Math.max(0, ...parts.map((list) => list.length));
        for (let index = 0; index < width; index += 1) {
            columns.push({ key: `t${index}`, label: (declared[index] && declared[index].label) || (index ? `Valor ${index + 1}` : 'Valor'), sortNumeric: true });
        }
        const rows = points.map((point, rowIndex) => {
            const row = base(point, rowIndex);
            parts[rowIndex].forEach((text, index) => { row[`t${index}`] = text; });
            return row;
        });
        return { columns, rows };
    }

    function biDrill(visual, points) {
        const dim = visual.dataset.mxDim;
        const holder = visual.closest('[data-mx-detail-url]');
        if (!dim || !holder) return {};
        return {
            drill: (index) => {
                const point = points[index];
                const url = new URL(holder.dataset.mxDetailUrl, window.location.href);
                url.searchParams.set('dim', dim);
                url.searchParams.set('valor', point.dataset.mxKey);
                url.searchParams.set('rotulo', biPointLabel(point, visual));
                return fetchJSON(`${url.pathname}${url.search}`);
            },
            canDrill: (index) => !!points[index] && points[index].dataset.mxKey != null,
        };
    }

    function scanBiPanels(root) {
        (root || document).querySelectorAll('.bi-panel').forEach((panel) => {
            panel.querySelectorAll(BI_VISUALS).forEach((visual) => {
                if (specs.has(visual)) return;
                const points = biPoints(visual);
                if (!points.length) return;
                points.forEach((point, index) => point.setAttribute('data-mx-index', index));
                const drill = biDrill(visual, points);
                if (drill.drill) {
                    // Dica nativa do ponto: o que já estava no title e, na linha de baixo, o botão direito.
                    points.forEach((point, index) => {
                        if (!drill.canDrill(index)) return;
                        const title = point.getAttribute('title');
                        point.setAttribute('title', title ? `${title}\n${DRILL_HINT}` : DRILL_HINT);
                    });
                }
                attach(visual, {
                    name: visual.dataset.mxName || '',
                    tableTitle: visual.dataset.mxTableTitle || '',
                    label: (index) => (points[index] ? biPointLabel(points[index], visual) : ''),
                    table: () => biTable(visual),
                    ...drill,
                });
            });
        });
    }
    // ------------------------------------------------------------------ cards de indicador (KPI)
    /* O card inteiro é o ponto: botão direito em qualquer lugar dele abre "Expandir dados" com os
       registros por trás do número. O selo (só o ícone) e o ⋯ ficam no canto do card.
       spec = { drill(), canDrill(), name, tableTitle, table(), actions() } — todos opcionais menos
       o drill; sem `table`, "Mostrar como tabela" traz o indicador como está na tela.
       Declarativo (o detalhe vem do servidor): data-mx-kpi-url="/dashes/.../detalhe/?…" no card. */
    function kpi(card, spec) {
        if (!card || !spec) return;
        const text = (selector) => squash(card.querySelector(selector));
        const name = spec.name || text('.mx-kpi-label') || 'Indicador';
        card.setAttribute('data-mx-index', '0');
        card.classList.add('is-kpi-drill');
        attach(card, {
            name,
            tableTitle: spec.tableTitle || 'Indicador como está na tela',
            label: () => '',
            table: spec.table || (() => ({
                columns: [{ key: 'label', label: 'Indicador' }, { key: 'value', label: 'Valor', strong: true }, { key: 'foot', label: 'Contexto', wrap: true }],
                rows: [{ label: name, value: text('.mx-kpi-value, .mx-big-days, strong'), foot: text('.mx-kpi-foot, .mx-hint, small'), __index: 0 }],
            })),
            drill: spec.drill ? () => spec.drill() : null,
            canDrill: spec.canDrill ? () => !!spec.canDrill() : null,
            actions: spec.actions ? () => spec.actions() : null,
        });
        if (spec.drill && !card.title) card.title = `${name}\n${DRILL_HINT}`;
        const badge = card.querySelector('.mx-drill-badge');
        if (badge) badge.title = 'Este indicador abre os registros por trás do número: clique aqui ou com o botão direito no card (no celular, toque longo).';
    }

    function scanKpis(root) {
        (root || document).querySelectorAll('[data-mx-kpi-url]').forEach((card) => {
            if (specs.has(card)) return;
            kpi(card, { drill: () => fetchJSON(card.dataset.mxKpiUrl) });
        });
    }

    // O script carrega no topo do <body> dos BIs (menu lateral): os painéis vêm depois.
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => { scanBiPanels(); scanKpis(); });
    else { scanBiPanels(); scanKpis(); }

    // ------------------------------------------------------------------ dados do servidor
    async function fetchJSON(url) {
        let response;
        try {
            response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        } catch (error) {
            throw new Error('Sem conexão com o servidor. Confira a rede e tente de novo.');
        }
        if (response.redirected && /\/entrar\/?/.test(response.url)) throw new Error('Sua sessão do Dashes terminou. Recarregue a página e entre de novo.');
        const type = response.headers.get('content-type') || '';
        if (!type.includes('application/json')) {
            if (response.status === 403) throw new Error('Você não tem acesso aos dados deste painel.');
            throw new Error(`O servidor respondeu ${response.status} sem os dados esperados.`);
        }
        const data = await response.json();
        if (!response.ok || data.error) throw new Error(data.error || `O servidor respondeu ${response.status}.`);
        return data;
    }

    // URL da página atual com os parâmetros trocados (mantém filtros como ?grupo=).
    function urlWith(base, params, keep) {
        const url = new URL(base, window.location.href);
        const current = new URLSearchParams(window.location.search);
        (keep || []).forEach((key) => current.getAll(key).forEach((value) => url.searchParams.append(key, value)));
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value == null || value === '') url.searchParams.delete(key);
            else url.searchParams.set(key, value);
        });
        return `${url.pathname}${url.search}`;
    }

    window.MXDrill = {
        attach,
        kpi,
        scan: (root) => { scanBiPanels(root); scanKpis(root); },
        hint: DRILL_HINT,
        expand: (container, index) => { const spec = specFor(container); if (spec && spec.drill) expand(spec, index, 'reset'); },
        showTable: (container) => { const spec = specFor(container); if (spec) showTable(spec); },
        open: (result) => modal.show(result, { reset: true }),
        fetchJSON,
        urlWith,
        format: formatValue,
        closeMenu: () => menu.close(false),
    };
})();
