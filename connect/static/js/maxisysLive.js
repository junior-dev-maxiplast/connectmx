/* MXLive: componentes animados e interativos dos painéis do padrão MaxiSys.

   - números que contam até o valor (e a partir do valor anterior quando o filtro muda);
   - ranking com barras que crescem e se reordenam (FLIP);
   - colunas, linhas (com mira e dica), rosca, medidor e calendário de calor, com transição;
   - filtro cruzado: clicar num item filtra a página inteira; os filtros viram etiquetas
     e ficam no endereço (#rep=10004&grupo=...), então o link compartilhado abre igual.

   Cores pelas variáveis CSS (--mx-cat-*, --mx-seq-*, --mx-div-*, --mx-chart-*): o tema
   claro/escuro vale sem recarregar. Quem pede "reduzir movimento" no sistema vê tudo
   sem animação. */
(function () {
    const NS = 'http://www.w3.org/2000/svg';
    const reduceMotion = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)').matches : false;
    const DURATION = reduceMotion ? 0 : 650;
    const ease = (t) => 1 - Math.pow(1 - t, 3);

    // ------------------------------------------------------------------ formatos
    const number = (options) => new Intl.NumberFormat('pt-BR', options);
    const compact = number({ notation: 'compact', maximumFractionDigits: 1 });
    const fmt = {
        money: (value) => (value == null ? '–' : `R$ ${compact.format(value)}`),
        moneyFull: (value) => (value == null ? '–' : number({ style: 'currency', currency: 'BRL', maximumFractionDigits: Math.abs(value) >= 1000 ? 0 : 2 }).format(value)),
        int: (value) => (value == null ? '–' : number({ maximumFractionDigits: 0 }).format(value)),
        dec: (value, digits = 1) => (value == null ? '–' : number({ minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value)),
        pct: (value, digits = 1) => (value == null ? '–' : `${(value * 100).toFixed(digits).replace('.', ',')}%`),
        signedPct: (value) => (value == null ? '–' : `${value > 0 ? '+' : ''}${(value * 100).toFixed(1).replace('.', ',')}%`),
        kg: (value) => (value == null ? '–' : Math.abs(value) >= 1000 ? `${compact.format(value / 1000)} t` : `${Math.round(value)} kg`),
        days: (value) => (value == null ? '–' : `${number({ maximumFractionDigits: 1 }).format(value)} dia${Math.abs(Math.round(value)) === 1 ? '' : 's'}`),
    };

    const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
    const paint = (value) => (value && value.startsWith('--') ? cssVar(value) : value);
    const el = (tag, attrs, parent) => {
        const node = document.createElementNS(NS, tag);
        Object.entries(attrs || {}).forEach(([key, value]) => { if (value != null) node.setAttribute(key, value); });
        if (parent) parent.appendChild(node);
        return node;
    };
    const html = (tag, className, text, parent) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        if (parent) parent.appendChild(node);
        return node;
    };
    const categorical = (index) => `--mx-cat-${(index % 8) + 1}`;

    // ------------------------------------------------------------------ animação
    function interpolate(from, to, k) {
        if (Array.isArray(to)) return to.map((value, index) => interpolate(Array.isArray(from) ? from[index] : 0, value, k));
        if (to == null) return to;
        const start = from == null || Number.isNaN(from) ? 0 : from;
        return start + (to - start) * k;
    }

    function tween(from, to, duration, onFrame) {
        // Aba escondida não pinta quadro (requestAnimationFrame para): vai direto ao valor final.
        if (!duration || document.hidden) { onFrame(to, 1); return () => {}; }
        onFrame(interpolate(from, to, 0), 0);
        const started = performance.now();
        let frame;
        const step = (now) => {
            const t = Math.min((now - started) / duration, 1);
            onFrame(interpolate(from, to, ease(t)), t);
            if (t < 1) frame = requestAnimationFrame(step);
        };
        frame = requestAnimationFrame(step);
        return () => cancelAnimationFrame(frame);
    }

    const counters = new WeakMap();
    function count(node, value, format, duration) {
        if (!node) return;
        const previous = counters.has(node) ? counters.get(node).value : 0;
        if (counters.has(node)) counters.get(node).stop();
        if (value == null || Number.isNaN(value)) { node.textContent = '–'; counters.set(node, { value: 0, stop() {} }); return; }
        const stop = tween(previous, value, duration == null ? DURATION + 250 : duration, (current) => { node.textContent = format(current); });
        counters.set(node, { value, stop });
    }

    function whenVisible(node, callback) {
        if (reduceMotion || !('IntersectionObserver' in window)) { callback(); return; }
        const observer = new IntersectionObserver((entries) => {
            if (entries.some((entry) => entry.isIntersecting)) { observer.disconnect(); callback(); }
        }, { threshold: 0.12 });
        observer.observe(node);
    }

    function tooltip(container) {
        const tip = html('div', 'mx-live-tip', null, container);
        tip.hidden = true;
        return {
            show(lines, x, y) {
                tip.replaceChildren();
                lines.forEach((line, index) => {
                    if (index) tip.appendChild(document.createElement('br'));
                    const node = html(line.strong ? 'strong' : 'span', line.swatch ? 'mx-live-tip-row' : null, line.text, tip);
                    if (line.swatch) {
                        const swatch = html('i', null, null);
                        swatch.style.background = paint(line.swatch);
                        node.prepend(swatch);
                    }
                });
                tip.hidden = false;
                tip.style.left = '0px';
                const box = container.getBoundingClientRect();
                const width = tip.offsetWidth;
                tip.style.left = `${Math.min(Math.max(x - width / 2, 4), box.width - width - 4)}px`;
                tip.style.top = `${Math.max(y - tip.offsetHeight - 12, 4)}px`;
            },
            hide() { tip.hidden = true; },
        };
    }

    // ------------------------------------------------------------------ filtro cruzado
    // Cada dimensão guarda uma lista de valores. Clique escolhe só aquele valor (clicar de
    // novo tira o filtro); Ctrl (⌘ no Mac) + clique soma ou tira o valor da seleção.
    const additive = (event) => event === true || !!(event && (event.ctrlKey || event.metaKey));
    const chosen = (value) => (value == null || value === '' ? [] : (Array.isArray(value) ? value : [value])).map(String);
    // A linha passa quando a dimensão não tem filtro ou o valor está entre os escolhidos.
    const pass = (state, key, value) => {
        const list = chosen(state && state[key]);
        return !list.length || list.includes(String(value));
    };
    const passAll = (row, state, skip, fields) => Object.keys(state || {}).every((key) => {
        if (key === skip || (Array.isArray(skip) && skip.includes(key))) return true;
        const field = fields ? fields[key] : key;
        return field == null || pass(state, key, row[field]);
    });

    function crossFilter(options) {
        const state = {};
        const listeners = [];
        const labels = options.labels || {};
        const names = options.names || {};
        const chips = options.chips ? document.querySelector(options.chips) : null;
        const add = (key, value) => {
            const list = state[key] || (state[key] = []);
            if (!list.includes(value)) list.push(value);
        };
        if (options.fromHash !== false && window.location.hash.length > 1) {
            new URLSearchParams(window.location.hash.slice(1)).forEach((value, key) => {
                if (labels[key] && value !== '') add(key, value);
            });
        }
        const copy = () => Object.fromEntries(Object.entries(state).map(([key, list]) => [key, list.slice()]));
        const nameOf = (key, value) => (typeof names[key] === 'function' ? names[key](value) : names[key] && names[key][value]) || value;
        const emit = () => {
            if (options.fromHash !== false) {
                const params = new URLSearchParams();
                Object.entries(state).forEach(([key, list]) => list.forEach((value) => params.append(key, value)));
                const hash = params.toString();
                window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${hash ? `#${hash}` : ''}`);
            }
            renderChips();
            const snapshot = copy();
            listeners.forEach((listener) => listener(snapshot));
        };
        function renderChips() {
            if (!chips) return;
            chips.replaceChildren();
            const keys = Object.keys(state);
            chips.hidden = !keys.length;
            keys.forEach((key) => {
                const list = state[key];
                const all = list.map((value) => nameOf(key, value));
                const text = all.length > 3 ? `${all.slice(0, 3).join(', ')} +${all.length - 3}` : all.join(', ');
                const chip = html('button', 'mx-chip is-active mx-filter-chip', null, chips);
                chip.type = 'button';
                chip.title = all.join(', ');
                chip.append(html('span', 'mx-filter-chip-key', `${labels[key]}: `), html('span', 'mx-filter-chip-value', text), html('span', 'mx-filter-chip-x', ' ×'));
                chip.setAttribute('aria-label', `Tirar o filtro ${labels[key]} (${all.join(', ')})`);
                chip.addEventListener('click', () => api.set(key, null));
            });
            if (keys.length > 1) {
                const clear = html('button', 'mx-chip mx-filter-chip', 'Limpar filtros', chips);
                clear.type = 'button';
                clear.addEventListener('click', () => api.clear());
            }
        }
        const api = {
            get: copy,
            values: (key) => (state[key] || []).slice(),
            // Um valor só quando há exatamente um escolhido (ex.: a meta de um representante).
            value: (key) => (state[key] && state[key].length === 1 ? state[key][0] : null),
            has: (key, value) => !!state[key] && state[key].includes(String(value)),
            // Troca a seleção da dimensão: um valor, uma lista ou null (tira o filtro).
            set(key, value) {
                const list = [...new Set(chosen(value))];
                if (list.length) state[key] = list;
                else delete state[key];
                emit();
            },
            // Clique: só este valor (de novo: tira). Ctrl/⌘ + clique: soma ou tira da seleção.
            toggle(key, value, event) {
                const text = String(value);
                const list = state[key] || [];
                if (additive(event)) api.set(key, list.includes(text) ? list.filter((item) => item !== text) : [...list, text]);
                else api.set(key, list.length === 1 && list[0] === text ? null : text);
            },
            clear() { Object.keys(state).forEach((key) => delete state[key]); emit(); },
            subscribe(listener) { listeners.push(listener); },
            setName(key, value, name) { if (typeof names[key] !== 'function') { names[key] = names[key] || {}; names[key][value] = name; } },
            refresh: emit,
        };
        renderChips();
        return api;
    }

    // Soma as linhas (fatos) que passam pelo filtro, agrupando por uma dimensão.
    function group(rows, key, measures, filter, skip) {
        const totals = new Map();
        rows.forEach((row) => {
            if (filter && !passAll(row, filter, skip)) return;
            const id = typeof key === 'function' ? key(row) : row[key];
            if (id == null) return;
            const entry = totals.get(id) || Object.fromEntries(measures.map((measure) => [measure, 0]));
            measures.forEach((measure) => { entry[measure] += Number(row[measure]) || 0; });
            totals.set(id, entry);
        });
        return totals;
    }

    function sum(rows, measures, filter, skip) {
        const result = Object.fromEntries(measures.map((measure) => [measure, 0]));
        rows.forEach((row) => {
            if (filter && !passAll(row, filter, skip)) return;
            measures.forEach((measure) => { result[measure] += Number(row[measure]) || 0; });
        });
        return result;
    }

    // ------------------------------------------------------------------ ranking (barras horizontais)
    function ranking(container, options = {}) {
        const format = options.format || fmt.money;
        const limit = options.limit || 10;
        const list = html('ol', 'mx-live-rank', null, container);
        const empty = html('p', 'mx-empty-note', options.emptyText || 'Sem dados para este filtro.', container);
        empty.hidden = true;
        const nodes = new Map();
        function build(item) {
            const li = html('li', 'mx-live-rank-item');
            const button = html('button', 'mx-live-rank-button', null, li);
            button.type = 'button';
            const head = html('span', 'mx-live-rank-head', null, button);
            html('span', 'mx-live-rank-label', item.label, head);
            html('span', 'mx-live-rank-value', '', head);
            const track = html('span', 'mx-live-rank-track', null, button);
            const fill = html('span', 'mx-live-rank-fill', null, track);
            fill.style.width = '0%';
            html('span', 'mx-live-rank-sub', item.sub || '', button);
            if (options.onSelect) button.addEventListener('click', (event) => options.onSelect(item.key, item, event));
            else button.disabled = true;
            if (item.href) {
                const link = html('a', 'mx-live-rank-link', '↗', li);
                link.href = item.href;
                link.title = 'Abrir';
                link.setAttribute('aria-label', `Abrir ${item.label}`);
            }
            return li;
        }
        return {
            update(items, selected) {
                const picked = chosen(selected);
                const shown = items.slice(0, limit);
                empty.hidden = shown.length > 0;
                const max = Math.max(...shown.map((item) => Math.abs(item.value) || 0), 1);
                const first = new Map();
                nodes.forEach((node, key) => first.set(key, node.getBoundingClientRect().top));
                const keep = new Set();
                shown.forEach((item, index) => {
                    let node = nodes.get(item.key);
                    if (!node) { node = build(item); nodes.set(item.key, node); }
                    keep.add(item.key);
                    node.style.setProperty('--mx-rank-color', paint(item.color || options.color || '--mx-chart-1'));
                    node.querySelector('.mx-live-rank-label').textContent = item.label;
                    node.querySelector('.mx-live-rank-sub').textContent = item.sub || '';
                    node.querySelector('.mx-live-rank-button').setAttribute('aria-label', `${index + 1}º ${item.label}: ${format(item.value)}${item.sub ? `, ${item.sub}` : ''}`);
                    const isPicked = picked.includes(String(item.key));
                    node.classList.toggle('is-selected', isPicked);
                    node.classList.toggle('is-dimmed', picked.length > 0 && !isPicked);
                    if (options.onSelect) node.querySelector('.mx-live-rank-button').setAttribute('aria-pressed', isPicked ? 'true' : 'false');
                    node.classList.toggle('is-negative', item.value < 0);
                    list.appendChild(node);
                    const fill = node.querySelector('.mx-live-rank-fill');
                    const width = `${Math.max(Math.abs(item.value) / max * 100, 0.6)}%`;
                    if (document.hidden || reduceMotion) fill.style.width = width;
                    else requestAnimationFrame(() => { fill.style.width = width; });
                    count(node.querySelector('.mx-live-rank-value'), item.value, format);
                });
                nodes.forEach((node, key) => { if (!keep.has(key)) { node.remove(); nodes.delete(key); } });
                if (reduceMotion) return;
                nodes.forEach((node, key) => {
                    const before = first.get(key);
                    if (before == null) { node.classList.add('is-entering'); requestAnimationFrame(() => node.classList.remove('is-entering')); return; }
                    const delta = before - node.getBoundingClientRect().top;
                    if (!delta) return;
                    node.style.transition = 'none';
                    node.style.transform = `translateY(${delta}px)`;
                    requestAnimationFrame(() => { node.style.transition = ''; node.style.transform = ''; });
                });
            },
        };
    }

    // ------------------------------------------------------------------ colunas e linhas
    function chartShell(container) {
        container.classList.add('mx-live-chart');
        container.replaceChildren();
        const plot = html('div', 'mx-live-plot', null, container);
        return { plot, tip: tooltip(container) };
    }

    function frame(container, height) {
        container.replaceChildren();
        const width = Math.max(container.clientWidth || 600, 260);
        const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, class: 'mx-live-svg', role: 'img' }, container);
        return { svg, width, height, pad: { top: 14, right: 12, bottom: 26, left: 52 } };
    }

    function niceMax(value) {
        if (!value || value <= 0) return 1;
        const power = Math.pow(10, Math.floor(Math.log10(value)));
        const scaled = value / power;
        return ([1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((step) => step >= scaled) || 10) * power;
    }

    // Largura dos rótulos do eixo: medida uma vez por texto (o gráfico redesenha a cada quadro
    // da animação) e estimada enquanto ele ainda não aparece na tela.
    const textWidths = new Map();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => textWidths.clear());
    function textWidth(node) {
        const text = node.textContent;
        if (textWidths.has(text)) return textWidths.get(text);
        let width = 0;
        try { width = node.getComputedTextLength(); } catch (error) { width = 0; }
        if (width > 0) { textWidths.set(text, width); return width; }
        return text.length * 5.8;
    }

    // Encurta com "…" até caber em `room`; o texto inteiro fica na dica do navegador.
    function fitText(node, room) {
        const full = node.textContent;
        let width = textWidth(node);
        if (width <= room) return width;
        let size = Math.max(Math.floor((full.length * room) / width), 1);
        do {
            node.textContent = `${full.slice(0, size).trimEnd()}…`;
            width = textWidth(node);
            size -= 1;
        } while (width > room && size > 0);
        el('title', {}, node).textContent = full;
        return width;
    }

    function axis(f, max, format, labels, x, every, min = 0) {
        const plotH = f.height - f.pad.top - f.pad.bottom;
        [0, 0.25, 0.5, 0.75, 1].forEach((ratio) => {
            const y = f.pad.top + plotH - ratio * plotH;
            el('line', { x1: f.pad.left, x2: f.width - f.pad.right, y1: y, y2: y, class: 'mx-live-grid' }, f.svg);
            const label = el('text', { x: f.pad.left - 6, y: y + 3, 'text-anchor': 'end', class: 'mx-live-axis' }, f.svg);
            label.textContent = format(min + (max - min) * ratio, true);
        });
        // Rótulos das categorias: espaçados pela largura real do texto, encurtados com "…"
        // quando não cabem e sempre dentro do gráfico (o da ponta encosta na borda em vez de
        // vazar do card); quem encostaria no vizinho não é desenhado.
        const count = labels.length;
        if (!count) return;
        const GAP = 8;
        const nodes = labels.map((label) => {
            const text = el('text', { x: 0, y: f.height - 8, 'text-anchor': 'middle', class: 'mx-live-axis' }, f.svg);
            text.textContent = label;
            return text;
        });
        const step = count > 1 ? Math.abs(x(1) - x(0)) || 1 : f.width;
        const widest = Math.min(Math.max(...nodes.map((node) => textWidth(node))), 120);
        const stride = Math.max(every || 1, Math.ceil((widest + GAP) / step), 1);
        const shown = [];
        for (let index = 0; index < count; index += stride) shown.push(index);
        if (shown[shown.length - 1] !== count - 1) {
            // O último rótulo sempre aparece; o anterior sai se ficaria colado nele ("29 30").
            if (shown.length > 1 && count - 1 - shown[shown.length - 1] < stride * 0.6) shown.pop();
            shown.push(count - 1);
        }
        const room = Math.max(Math.min(step * stride - GAP, f.width - 2), 24);
        const boxes = shown.map((index) => {
            const half = fitText(nodes[index], room) / 2;
            const center = Math.min(Math.max(x(index), half + 1), f.width - half - 1);
            nodes[index].setAttribute('x', center);
            return { index, left: center - half, right: center + half };
        });
        const keep = new Set();
        let limit = Infinity;
        for (let k = boxes.length - 1; k >= 0; k -= 1) {
            if (boxes[k].right + GAP / 2 <= limit) { keep.add(boxes[k].index); limit = boxes[k].left; }
        }
        nodes.forEach((node, index) => { if (!keep.has(index)) node.remove(); });
    }

    function columns(container, options = {}) {
        const height = options.height || 240;
        const format = options.format || fmt.money;
        const { plot, tip } = chartShell(container);
        let current = null;
        let stop = () => {};
        function draw(data, values) {
            const f = frame(plot, height);
            const labels = data.labels;
            const plotW = f.width - f.pad.left - f.pad.right;
            const plotH = f.height - f.pad.top - f.pad.bottom;
            const slot = plotW / Math.max(labels.length, 1);
            const x = (index) => f.pad.left + slot * index + slot / 2;
            const totals = labels.map((_, index) => (options.stacked
                ? values.reduce((acc, series) => acc + Math.max(series[index] || 0, 0), 0)
                : Math.max(...values.map((series) => series[index] || 0), 0)));
            const targetMax = data.target ? Math.max(...data.target.values.map((value) => value || 0), 0) : 0;
            const max = niceMax(Math.max(...totals, targetMax, data.minMax || 0));
            const y = (value) => f.pad.top + plotH - (value / max) * plotH;
            axis(f, max, format, labels, x, Math.max(Math.ceil(labels.length / Math.max(Math.floor(plotW / 54), 2)), 1));
            const barWidth = Math.min(slot * (options.stacked ? 0.64 : 0.74), options.maxBar || 38);
            // `highlight`: um índice ou uma lista (as colunas escolhidas no filtro); as outras ficam apagadas.
            const marked = (data.highlight == null ? [] : [].concat(data.highlight)).filter((index) => index != null && index >= 0);
            labels.forEach((_, index) => {
                let base = 0;
                data.series.forEach((series, seriesIndex) => {
                    const value = Math.max(values[seriesIndex][index] || 0, 0);
                    const width = options.stacked ? barWidth : barWidth / data.series.length;
                    const left = options.stacked ? x(index) - barWidth / 2 : x(index) - barWidth / 2 + width * seriesIndex;
                    const top = y(base + value);
                    const h = Math.max(y(base) - top, value ? 1 : 0);
                    const highlighted = !marked.length || marked.includes(index);
                    el('rect', {
                        x: left, y: top, width: Math.max(width - (options.stacked ? 0 : 1.5), 1), height: h, rx: 3,
                        fill: paint((data.colors && data.colors[index]) || series.color || '--mx-chart-1'),
                        class: `mx-live-bar${highlighted ? '' : ' is-dimmed'}`,
                    }, f.svg);
                    if (options.stacked) base += value;
                });
            });
            if (data.target) {
                data.target.values.forEach((value, index) => {
                    if (!value) return;
                    const yy = y(value);
                    el('line', { x1: x(index) - barWidth / 2 - 4, x2: x(index) + barWidth / 2 + 4, y1: yy, y2: yy, class: 'mx-live-target' }, f.svg);
                });
            }
            labels.forEach((label, index) => {
                const hit = el('rect', { x: x(index) - slot / 2, y: f.pad.top, width: slot, height: plotH, fill: 'transparent', class: 'mx-live-hit', tabindex: 0 }, f.svg);
                const show = (event) => {
                    const lines = [{ text: label, strong: true }];
                    data.series.forEach((series, seriesIndex) => lines.push({ text: `${series.name}: ${format(data.series[seriesIndex].values[index] || 0)}`, swatch: series.color }));
                    if (data.target && data.target.values[index]) lines.push({ text: `${data.target.name}: ${format(data.target.values[index])}` });
                    if (data.extra) data.extra(index).forEach((text) => lines.push({ text }));
                    const box = f.svg.getBoundingClientRect();
                    const scale = box.width / f.width;
                    tip.show(lines, x(index) * scale, (f.pad.top + 46) * scale);
                    if (event && event.type === 'focus') hit.classList.add('is-focus');
                };
                hit.addEventListener('pointerenter', show);
                hit.addEventListener('focus', show);
                hit.addEventListener('pointerleave', () => tip.hide());
                hit.addEventListener('blur', () => { tip.hide(); hit.classList.remove('is-focus'); });
                if (options.onSelect) {
                    hit.style.cursor = 'pointer';
                    hit.addEventListener('click', (event) => options.onSelect(data.keys ? data.keys[index] : index, index, event));
                    hit.addEventListener('keydown', (event) => { if (event.key === 'Enter') options.onSelect(data.keys ? data.keys[index] : index, index, event); });
                }
            });
        }
        const api = {
            update(data) {
                const target = data.series.map((series) => series.values.map((value) => value || 0));
                const sameShape = current && current.length === target.length && current.every((series, index) => series.length === target[index].length);
                const from = sameShape ? current : target.map((series) => series.map(() => 0));
                stop();
                stop = tween(from, target, DURATION, (values) => draw(data, values));
                current = target;
                api.data = data;
            },
        };
        if (window.ResizeObserver) {
            let last = container.clientWidth;
            new ResizeObserver(() => { if (Math.abs(container.clientWidth - last) > 8 && api.data) { last = container.clientWidth; draw(api.data, current); } }).observe(container);
        }
        document.addEventListener('mx:theme', () => { if (api.data) draw(api.data, current); });
        return api;
    }

    function line(container, options = {}) {
        const height = options.height || 240;
        const format = options.format || fmt.money;
        const { plot, tip } = chartShell(container);
        let current = null;
        let stop = () => {};
        let drawnOnce = false;
        function draw(data, values, animatePath) {
            const f = frame(plot, height);
            const labels = data.labels;
            const plotW = f.width - f.pad.left - f.pad.right;
            const plotH = f.height - f.pad.top - f.pad.bottom;
            const step = plotW / Math.max(labels.length - 1, 1);
            const x = (index) => f.pad.left + (labels.length === 1 ? plotW / 2 : step * index);
            const all = values.flat().filter((value) => value != null);
            const minValue = data.min != null ? data.min : options.min != null ? options.min : Math.min(0, ...all);
            const max = data.max != null ? data.max : options.max != null ? options.max : niceMax(Math.max(...all, data.minMax || 0));
            const y = (value) => f.pad.top + plotH - ((value - minValue) / ((max - minValue) || 1)) * plotH;
            axis(f, max, format, labels, x, Math.max(Math.ceil(labels.length / Math.max(Math.floor(plotW / 54), 2)), 1), minValue);
            // `marked`: índices escolhidos no filtro (ex.: meses), com uma faixa de fundo.
            const band = labels.length > 1 ? step : plotW;
            (data.marked || []).forEach((index) => {
                if (index == null || index < 0 || index >= labels.length) return;
                const left = Math.max(x(index) - band / 2, f.pad.left);
                const right = Math.min(x(index) + band / 2, f.width - f.pad.right);
                el('rect', { x: left, y: f.pad.top, width: Math.max(right - left, 1), height: plotH, rx: 4, class: 'mx-live-band' }, f.svg);
            });
            if (data.reference != null) {
                const yy = y(data.reference);
                el('line', { x1: f.pad.left, x2: f.width - f.pad.right, y1: yy, y2: yy, class: 'mx-live-reference' }, f.svg);
                if (data.referenceLabel) {
                    const text = el('text', { x: f.width - f.pad.right, y: yy - 5, 'text-anchor': 'end', class: 'mx-live-reference-label' }, f.svg);
                    text.textContent = data.referenceLabel;
                }
            }
            data.series.forEach((series, seriesIndex) => {
                const points = values[seriesIndex].map((value, index) => (value == null ? null : [x(index), y(value)]));
                let d = '';
                points.forEach((point, index) => { if (point) d += `${d && points[index - 1] ? 'L' : 'M'}${point[0].toFixed(1)},${point[1].toFixed(1)}`; });
                if (series.area && points.length && points.every(Boolean)) {
                    el('path', { d: `${d}L${points[points.length - 1][0]},${y(minValue)}L${points[0][0]},${y(minValue)}Z`, fill: paint(series.color), class: 'mx-live-area' }, f.svg);
                }
                const path = el('path', { d, fill: 'none', stroke: paint(series.color), class: `mx-live-line${series.dashed ? ' is-dashed' : ''}${animatePath && !series.dashed ? ' is-drawing' : ''}` }, f.svg);
                if (animatePath && !series.dashed && !document.hidden && !reduceMotion && path.getTotalLength) {
                    const length = path.getTotalLength();
                    path.style.strokeDasharray = `${length}`;
                    path.style.strokeDashoffset = `${length}`;
                    path.getBoundingClientRect();
                    path.style.transition = `stroke-dashoffset ${reduceMotion ? 0 : 1100}ms cubic-bezier(.3,.7,.2,1)`;
                    requestAnimationFrame(() => { path.style.strokeDashoffset = '0'; });
                }
                if (series.markers !== false) points.forEach((point) => { if (point) el('circle', { cx: point[0], cy: point[1], r: 2.4, fill: paint(series.color), class: 'mx-live-dot' }, f.svg); });
            });
            const cross = el('line', { x1: 0, x2: 0, y1: f.pad.top, y2: f.pad.top + plotH, class: 'mx-live-cross' }, f.svg);
            cross.style.opacity = 0;
            const overlay = el('rect', { x: f.pad.left - step / 2, y: f.pad.top, width: plotW + step, height: plotH, fill: 'transparent', tabindex: 0, class: 'mx-live-hit' }, f.svg);
            const marks = data.series.map((series) => el('circle', { r: 4.5, fill: paint(series.color), class: 'mx-live-mark' }, f.svg));
            marks.forEach((mark) => { mark.style.opacity = 0; });
            let focusIndex = labels.length - 1;
            const show = (index) => {
                index = Math.max(0, Math.min(labels.length - 1, index));
                focusIndex = index;
                cross.setAttribute('x1', x(index));
                cross.setAttribute('x2', x(index));
                cross.style.opacity = 1;
                const lines = [{ text: labels[index], strong: true }];
                data.series.forEach((series, seriesIndex) => {
                    const value = data.series[seriesIndex].values[index];
                    const mark = marks[seriesIndex];
                    if (value == null) { mark.style.opacity = 0; return; }
                    mark.setAttribute('cx', x(index));
                    mark.setAttribute('cy', y(values[seriesIndex][index]));
                    mark.style.opacity = 1;
                    lines.push({ text: `${series.name}: ${format(value)}`, swatch: series.color });
                });
                if (data.extra) data.extra(index).forEach((text) => lines.push({ text }));
                const box = f.svg.getBoundingClientRect();
                const scale = box.width / f.width;
                tip.show(lines, x(index) * scale, (f.pad.top + 46) * scale);
            };
            const hide = () => { cross.style.opacity = 0; marks.forEach((mark) => { mark.style.opacity = 0; }); tip.hide(); };
            const indexAt = (event) => {
                const box = f.svg.getBoundingClientRect();
                const px = (event.clientX - box.left) * (f.width / box.width);
                return Math.max(0, Math.min(labels.length - 1, Math.round((px - f.pad.left) / (step || 1))));
            };
            const select = (index, event) => options.onSelect(data.keys ? data.keys[index] : index, index, event);
            overlay.addEventListener('pointermove', (event) => show(indexAt(event)));
            overlay.addEventListener('pointerleave', hide);
            overlay.addEventListener('focus', () => show(focusIndex));
            overlay.addEventListener('blur', hide);
            overlay.addEventListener('keydown', (event) => {
                if (event.key === 'ArrowRight') { event.preventDefault(); show(focusIndex + 1); }
                if (event.key === 'ArrowLeft') { event.preventDefault(); show(focusIndex - 1); }
                if (event.key === 'Enter' && options.onSelect) select(focusIndex, event);
            });
            if (options.onSelect) {
                overlay.style.cursor = 'pointer';
                overlay.addEventListener('click', (event) => select(indexAt(event), event));
            }
        }
        const api = {
            update(data) {
                const target = data.series.map((series) => series.values.map((value) => (value == null ? null : value)));
                const sameShape = current && current.length === target.length && current.every((series, index) => series.length === target[index].length);
                stop();
                if (!drawnOnce || !sameShape) {
                    draw(data, target, !drawnOnce);
                    drawnOnce = true;
                } else {
                    const from = current.map((series) => series.map((value) => value || 0));
                    stop = tween(from, target.map((series) => series.map((value) => value || 0)), DURATION, (values) => draw(data, values.map((series, s) => series.map((value, i) => (target[s][i] == null ? null : value))), false));
                }
                current = target;
                api.data = data;
            },
        };
        if (window.ResizeObserver) {
            let last = container.clientWidth;
            new ResizeObserver(() => { if (Math.abs(container.clientWidth - last) > 8 && api.data) { last = container.clientWidth; draw(api.data, current, false); } }).observe(container);
        }
        document.addEventListener('mx:theme', () => { if (api.data) draw(api.data, current, false); });
        return api;
    }

    // ------------------------------------------------------------------ rosca
    function donut(container, options = {}) {
        const size = options.size || 200;
        const format = options.format || fmt.money;
        const wrap = html('div', 'mx-live-donut', null, container);
        const svg = el('svg', { viewBox: `0 0 ${size} ${size}`, class: 'mx-live-donut-svg', role: 'img', 'aria-label': options.label || '' }, wrap);
        const center = html('div', 'mx-live-donut-center', null, wrap);
        const total = html('strong', null, '–', center);
        html('span', null, options.centerLabel || 'total', center);
        const legend = html('ul', 'mx-live-legend', null, container);
        const tip = tooltip(wrap);
        let current = [];
        let stop = () => {};
        const radius = size / 2 - 14;
        const thickness = options.thickness || 26;
        function arc(start, end) {
            const large = end - start > Math.PI ? 1 : 0;
            const p = (angle) => [size / 2 + radius * Math.cos(angle - Math.PI / 2), size / 2 + radius * Math.sin(angle - Math.PI / 2)];
            const [x1, y1] = p(start);
            const [x2, y2] = p(Math.max(end - 0.0001, start));
            return `M${x1.toFixed(2)},${y1.toFixed(2)}A${radius},${radius} 0 ${large} 1 ${x2.toFixed(2)},${y2.toFixed(2)}`;
        }
        function draw(items, values, selection) {
            const picked = chosen(selection);
            svg.replaceChildren();
            el('circle', { cx: size / 2, cy: size / 2, r: radius, fill: 'none', class: 'mx-live-donut-track', 'stroke-width': thickness }, svg);
            const sumValues = values.reduce((acc, value) => acc + Math.max(value, 0), 0) || 1;
            let angle = 0;
            const gap = items.length > 1 ? 0.012 : 0;
            items.forEach((item, index) => {
                const share = Math.max(values[index], 0) / sumValues;
                const start = angle + gap;
                const end = angle + share * Math.PI * 2 - gap;
                angle += share * Math.PI * 2;
                if (end <= start) return;
                const isSelected = picked.includes(String(item.key));
                const path = el('path', {
                    d: arc(start, end), fill: 'none', stroke: paint(item.color), 'stroke-width': isSelected ? thickness + 8 : thickness,
                    class: `mx-live-donut-arc${picked.length && !isSelected ? ' is-dimmed' : ''}`, tabindex: 0,
                    'aria-label': `${item.label}: ${format(item.value)} (${fmt.pct(item.value / (items.reduce((acc, i) => acc + Math.max(i.value, 0), 0) || 1))})`,
                }, svg);
                const show = () => {
                    const mid = (start + end) / 2;
                    const box = svg.getBoundingClientRect();
                    const scale = box.width / size;
                    tip.show([{ text: item.label, strong: true }, { text: format(item.value), swatch: item.color }, { text: `${fmt.pct(share)} do total` }],
                        (size / 2 + radius * Math.cos(mid - Math.PI / 2)) * scale, (size / 2 + radius * Math.sin(mid - Math.PI / 2)) * scale);
                    path.classList.add('is-hover');
                };
                const hide = () => { tip.hide(); path.classList.remove('is-hover'); };
                path.addEventListener('pointerenter', show);
                path.addEventListener('pointerleave', hide);
                path.addEventListener('focus', show);
                path.addEventListener('blur', hide);
                if (options.onSelect) {
                    path.style.cursor = 'pointer';
                    path.addEventListener('click', (event) => options.onSelect(item.key, item, event));
                    path.addEventListener('keydown', (event) => { if (event.key === 'Enter') options.onSelect(item.key, item, event); });
                }
            });
        }
        return {
            update(items, selected) {
                const target = items.map((item) => Math.max(item.value || 0, 0));
                const from = current.length === target.length ? current : target.map(() => 0);
                stop();
                stop = tween(from, target, DURATION + 150, (values) => draw(items, values, selected));
                current = target;
                const totalValue = items.reduce((acc, item) => acc + Math.max(item.value || 0, 0), 0);
                count(total, totalValue, options.centerFormat || format);
                legend.replaceChildren();
                const picked = chosen(selected);
                items.forEach((item) => {
                    const isPicked = picked.includes(String(item.key));
                    const li = html('li', picked.length && !isPicked ? 'is-dimmed' : isPicked ? 'is-selected' : '', null, legend);
                    const button = html('button', null, null, li);
                    button.type = 'button';
                    const swatch = html('i', null, null, button);
                    swatch.style.background = paint(item.color);
                    html('span', 'mx-live-legend-label', item.label, button);
                    html('b', null, fmt.pct(totalValue ? Math.max(item.value, 0) / totalValue : 0, 0), button);
                    if (options.onSelect) {
                        button.setAttribute('aria-pressed', isPicked ? 'true' : 'false');
                        button.addEventListener('click', (event) => options.onSelect(item.key, item, event));
                    } else button.disabled = true;
                });
            },
        };
    }

    // ------------------------------------------------------------------ medidor (meia-lua)
    function gauge(container, options = {}) {
        const format = options.format || ((value) => fmt.pct(value, 0));
        const width = 220;
        const height = 130;
        const wrap = html('div', 'mx-live-gauge', null, container);
        const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': options.label || '' }, wrap);
        const value = html('strong', 'mx-live-gauge-value', '–', wrap);
        const caption = html('span', 'mx-live-gauge-caption', options.caption || '', wrap);
        const radius = 88;
        const cx = width / 2;
        const cy = 112;
        let current = 0;
        let stop = () => {};
        const point = (ratio) => {
            const angle = Math.PI * (1 - ratio);
            return [cx + radius * Math.cos(angle), cy - radius * Math.sin(angle)];
        };
        const arc = (from, to) => {
            const [x1, y1] = point(from);
            const [x2, y2] = point(to);
            return `M${x1.toFixed(2)},${y1.toFixed(2)}A${radius},${radius} 0 0 1 ${x2.toFixed(2)},${y2.toFixed(2)}`;
        };
        function tone(ratio) {
            const good = options.good != null ? options.good : 0.95;
            const warn = options.warn != null ? options.warn : 0.85;
            if (options.lowerIsBetter) return ratio <= good ? '--mx-good' : ratio <= warn ? '--mx-warn' : '--mx-bad';
            return ratio >= good ? '--mx-good' : ratio >= warn ? '--mx-warn' : '--mx-bad';
        }
        let marker = options.target;
        function draw(ratio, toneRatio) {
            svg.replaceChildren();
            const scale = options.max || 1;
            el('path', { d: arc(0, 1), class: 'mx-live-gauge-track', fill: 'none' }, svg);
            const shown = Math.max(Math.min(ratio / scale, 1), 0);
            if (shown > 0.002) el('path', { d: arc(0, shown), class: 'mx-live-gauge-fill', fill: 'none', stroke: paint(tone(toneRatio)) }, svg);
            if (marker != null) {
                const position = Math.min(marker / scale, 1);
                const [tx, ty] = point(position);
                const angle = Math.PI * (1 - position);
                el('line', { x1: cx + (radius - 16) * Math.cos(angle), y1: cy - (radius - 16) * Math.sin(angle), x2: tx + (tx - cx) * 0.12, y2: ty + (ty - cy) * 0.12, class: 'mx-live-gauge-target' }, svg);
            }
        }
        return {
            // `extra.target` move a marca (ex.: o ritmo esperado do mês); `extra.toneRatio`
            // define a cor (ex.: realizado ÷ ritmo esperado, em vez de realizado ÷ meta).
            update(ratio, text, extra = {}) {
                stop();
                if (extra.target !== undefined) marker = extra.target;
                const target = ratio == null ? 0 : ratio;
                const toneRatio = extra.toneRatio != null ? extra.toneRatio : target;
                stop = tween(current, target, DURATION + 350, (valueNow) => draw(valueNow, toneRatio));
                current = target;
                if (ratio == null) value.textContent = '–';
                else count(value, ratio, format);
                if (text != null) caption.textContent = text;
                wrap.dataset.tone = ratio == null ? '' : tone(toneRatio).replace('--mx-', '');
            },
        };
    }

    // ------------------------------------------------------------------ calendário de calor
    function calendar(container, options = {}) {
        const format = options.format || fmt.money;
        const wrap = html('div', 'mx-live-calendar', null, container);
        const tip = tooltip(container);
        return {
            update(days) {
                wrap.replaceChildren();
                if (!days.length) return;
                const values = days.map((day) => day.value).filter((value) => value > 0).sort((a, b) => a - b);
                const breaks = [0.2, 0.4, 0.6, 0.8].map((q) => values[Math.min(values.length - 1, Math.floor(q * values.length))] || 0);
                const first = new Date(`${days[0].date}T12:00:00`);
                const offset = (first.getDay() + 6) % 7; // segunda = 0
                ['S', 'T', 'Q', 'Q', 'S', 'S', 'D'].forEach((label, index) => {
                    const cell = html('span', 'mx-live-calendar-weekday', label, wrap);
                    cell.style.gridRow = String(index + 1);
                    cell.style.gridColumn = '1';
                });
                days.forEach((day, index) => {
                    const position = index + offset;
                    const cell = html('button', 'mx-live-calendar-day', null, wrap);
                    cell.type = 'button';
                    cell.style.gridRow = String((position % 7) + 1);
                    cell.style.gridColumn = String(Math.floor(position / 7) + 2);
                    const level = day.value > 0 ? 1 + breaks.filter((edge) => day.value > edge).length : 0;
                    cell.dataset.level = String(level);
                    cell.style.animationDelay = reduceMotion ? '0ms' : `${Math.min(index * 6, 900)}ms`;
                    const date = new Date(`${day.date}T12:00:00`);
                    const label = date.toLocaleDateString('pt-BR', { weekday: 'short', day: '2-digit', month: '2-digit' });
                    cell.setAttribute('aria-label', `${label}: ${format(day.value)}`);
                    const show = () => {
                        const box = cell.getBoundingClientRect();
                        const parent = container.getBoundingClientRect();
                        tip.show([{ text: label, strong: true }, { text: format(day.value) }].concat(day.extra ? [{ text: day.extra }] : []), box.left - parent.left + box.width / 2, box.top - parent.top);
                    };
                    cell.addEventListener('pointerenter', show);
                    cell.addEventListener('focus', show);
                    cell.addEventListener('pointerleave', () => tip.hide());
                    cell.addEventListener('blur', () => tip.hide());
                    if (options.onSelect) cell.addEventListener('click', (event) => options.onSelect(day.date, day, event));
                });
            },
        };
    }

    // ------------------------------------------------------------------ meses (botões)
    // Meses em linhas por ano. Clique escolhe o mês (de novo: tira); Ctrl (⌘) + clique soma
    // ou tira meses; Shift + clique escolhe o intervalo desde o último mês clicado.
    // `months`: [{ key }] com key = ano × 12 + mês (0 a 11); só esses meses ficam ativos.
    // Com `filter` + `dimension` aplica na hora, no filtro cruzado. Com `selected` + `onApply`
    // (página que recarrega) junta os meses enquanto Ctrl/Shift estão apertados e aplica ao
    // soltar a tecla ou no botão "Aplicar".
    const MONTHS_SHORT = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
    const monthLabel = (key) => `${MONTHS_SHORT[((Number(key) % 12) + 12) % 12]}/${String(Math.floor(Number(key) / 12)).slice(2)}`;
    function monthPicker(container, options) {
        const keys = options.months.map((month) => Number(typeof month === 'object' ? month.key : month)).sort((a, b) => a - b);
        const available = new Set(keys);
        const years = [...new Set(keys.map((key) => Math.floor(key / 12)))].sort((a, b) => b - a);
        const root = html('div', 'mx-month-picker', null, container);
        root.setAttribute('role', 'group');
        root.setAttribute('aria-label', options.title || 'Meses');
        html('span', 'mx-month-picker-title', options.title || 'Meses', root);
        const grid = html('div', 'mx-month-picker-years', null, root);
        const buttons = new Map();
        years.forEach((year) => {
            const row = html('div', 'mx-month-row', null, grid);
            html('span', 'mx-month-year', String(year), row);
            MONTHS_SHORT.forEach((name, index) => {
                const key = year * 12 + index;
                const button = html('button', 'mx-month-btn', name, row);
                button.type = 'button';
                button.title = `${name}/${year}`;
                if (!available.has(key)) { button.disabled = true; return; }
                button.addEventListener('click', (event) => choose(key, event));
                buttons.set(key, button);
            });
        });
        const side = html('div', 'mx-month-picker-side', null, root);
        const allButton = options.allLabel === false ? null : html('button', 'mx-chip mx-month-all', options.allLabel || 'Todos', side);
        if (allButton) { allButton.type = 'button'; allButton.addEventListener('click', () => apply([])); }
        const applyButton = options.onApply ? html('button', 'mx-btn mx-month-apply', 'Aplicar', side) : null;
        if (applyButton) { applyButton.type = 'button'; applyButton.hidden = true; applyButton.addEventListener('click', () => commit()); }
        html('span', 'mx-month-picker-hint', options.hint || 'Ctrl + clique soma meses · Shift + clique escolhe um intervalo', side);

        let anchor = null;
        let current = (options.filter ? options.filter.values(options.dimension) : options.selected || []).map(Number);
        let pending = null;
        const sameSet = (a, b) => a.length === b.length && a.every((key) => b.includes(key));
        function paintButtons() {
            const shown = pending || current;
            buttons.forEach((button, key) => {
                const on = shown.includes(key);
                button.classList.toggle('is-active', on);
                button.classList.toggle('is-pending', !!pending && on !== current.includes(key));
                button.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
            if (allButton) allButton.classList.toggle('is-active', !shown.length);
            if (applyButton) {
                applyButton.hidden = !pending || !pending.length || sameSet(pending, current);
                if (pending) applyButton.textContent = `Aplicar ${pending.length} ${pending.length === 1 ? 'mês' : 'meses'}`;
            }
        }
        function apply(list) {
            const next = [...new Set(list)].sort((a, b) => a - b);
            if (options.filter) { options.filter.set(options.dimension, next.map(String)); return; }
            pending = null;
            current = next;
            paintButtons();
            options.onApply(next);
        }
        function commit() {
            if (!pending) return;
            const next = pending;
            pending = null;
            if (!next.length || sameSet(next, current)) { paintButtons(); return; }
            apply(next);
        }
        function choose(key, event) {
            const base = pending || current;
            const ranged = !!(event && event.shiftKey && anchor != null);
            let next;
            if (ranged) {
                const [from, to] = anchor < key ? [anchor, key] : [key, anchor];
                const range = keys.filter((item) => item >= from && item <= to);
                next = additive(event) ? [...new Set([...base, ...range])] : range;
            } else if (additive(event)) {
                next = base.includes(key) ? base.filter((item) => item !== key) : [...base, key];
                anchor = key;
            } else {
                next = base.length === 1 && base[0] === key ? [] : [key];
                anchor = key;
            }
            if (options.onApply && (ranged || additive(event))) {
                pending = next;
                paintButtons();
                return;
            }
            if (options.onApply && !next.length) { paintButtons(); return; }
            apply(next);
        }
        if (options.onApply) {
            window.addEventListener('keyup', (event) => { if (['Control', 'Meta', 'Shift'].includes(event.key)) commit(); });
            window.addEventListener('blur', () => commit());
        }
        if (options.filter) options.filter.subscribe((state) => { current = chosen(state[options.dimension]).map(Number); paintButtons(); });
        paintButtons();
        // `choose` deixa outro gráfico (ex.: colunas por mês) usar as mesmas regras de clique.
        return { refresh: paintButtons, choose: (key, event) => { if (available.has(Number(key))) choose(Number(key), event); } };
    }

    // ------------------------------------------------------------------ minigráfico
    function sparkline(container, values, color) {
        container.replaceChildren();
        const width = 120;
        const height = 34;
        const clean = values.map((value) => Number(value) || 0);
        const max = Math.max(...clean, 1);
        const min = Math.min(...clean, 0);
        const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, class: 'mx-live-spark', 'aria-hidden': 'true' }, container);
        const step = width / Math.max(clean.length - 1, 1);
        const d = clean.map((value, index) => `${index ? 'L' : 'M'}${(index * step).toFixed(1)},${(height - 3 - ((value - min) / ((max - min) || 1)) * (height - 6)).toFixed(1)}`).join('');
        el('path', { d: `${d}L${width},${height}L0,${height}Z`, fill: paint(color || '--mx-chart-1'), class: 'mx-live-spark-area' }, svg);
        const path = el('path', { d, fill: 'none', stroke: paint(color || '--mx-chart-1'), class: 'mx-live-spark-line' }, svg);
        if (!reduceMotion && !document.hidden && path.getTotalLength) {
            const length = path.getTotalLength();
            path.style.strokeDasharray = `${length}`;
            path.style.strokeDashoffset = `${length}`;
            path.getBoundingClientRect();
            path.style.transition = 'stroke-dashoffset 1200ms cubic-bezier(.3,.7,.2,1)';
            requestAnimationFrame(() => { path.style.strokeDashoffset = '0'; });
        }
    }

    // ------------------------------------------------------------------ "ao vivo"
    function live(options) {
        // Recarrega os dados da página a cada `every` ms enquanto a aba está visível.
        let timer = null;
        const toggle = options.toggle ? document.querySelector(options.toggle) : null;
        const stamp = options.stamp ? document.querySelector(options.stamp) : null;
        const tick = async () => {
            if (document.hidden || (toggle && !toggle.checked)) return;
            try {
                const response = await fetch(options.url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
                if (!response.ok) throw new Error(String(response.status));
                const data = await response.json();
                options.onData(data);
                if (stamp) stamp.textContent = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            } catch (error) {
                if (stamp) stamp.textContent = 'sem conexão, tentando de novo';
            }
        };
        const start = () => { if (!timer) timer = setInterval(tick, options.every || 180000); };
        start();
        document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });
        return { tick, stop: () => { clearInterval(timer); timer = null; } };
    }

    window.MXLive = {
        fmt, count, tween, whenVisible, crossFilter, group, sum, categorical, paint, pass, passAll, chosen, additive,
        ranking, columns, line, donut, gauge, calendar, sparkline, live, monthPicker, monthLabel, MONTHS_SHORT, reduceMotion,
    };
})();
