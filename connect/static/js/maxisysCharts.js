/* Gráficos SVG do padrão MaxiSys: barras (agrupadas ou empilhadas, com meta) e linhas.
   As cores vêm das variáveis CSS (--mx-chart-*), então o tema claro/escuro vale sem recarregar. */
(function () {
    const NS = 'http://www.w3.org/2000/svg';
    const registry = new Set();

    const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
    const color = (value) => (value && value.startsWith('--') ? cssVar(value) : value);
    const el = (tag, attrs, parent) => {
        const node = document.createElementNS(NS, tag);
        Object.entries(attrs || {}).forEach(([key, val]) => node.setAttribute(key, val));
        if (parent) parent.appendChild(node);
        return node;
    };
    const compact = (value) => Intl.NumberFormat('pt-BR', { notation: 'compact', maximumFractionDigits: 1 }).format(value);

    function niceMax(value) {
        if (!value || value <= 0) return 1;
        const power = Math.pow(10, Math.floor(Math.log10(value)));
        const steps = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
        const scaled = value / power;
        return (steps.find((step) => step >= scaled) || 10) * power;
    }

    function frame(container, config) {
        container.replaceChildren();
        const width = Math.max(container.clientWidth || 600, 280);
        const height = config.height || 230;
        const pad = { top: 12, right: config.rightAxis ? 46 : 12, bottom: 26, left: 46 };
        const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': config.label || '' }, container);
        const tooltip = document.createElement('div');
        tooltip.className = 'mx-tooltip';
        tooltip.hidden = true;
        container.appendChild(tooltip);
        return { svg, tooltip, width, height, pad, plotW: width - pad.left - pad.right, plotH: height - pad.top - pad.bottom };
    }

    function axis(f, max, format, labels, x, step) {
        [0, 0.25, 0.5, 0.75, 1].forEach((ratio) => {
            const y = f.pad.top + f.plotH - ratio * f.plotH;
            el('line', { x1: f.pad.left, x2: f.width - f.pad.right, y1: y, y2: y, class: 'mx-grid-line' }, f.svg);
            const text = el('text', { x: f.pad.left - 6, y: y + 3, 'text-anchor': 'end', class: 'mx-axis-label' }, f.svg);
            text.textContent = format ? format(max * ratio, true) : compact(max * ratio);
        });
        labels.forEach((label, index) => {
            if (step && index % step !== 0 && index !== labels.length - 1) return;
            const text = el('text', { x: x(index), y: f.height - 8, 'text-anchor': 'middle', class: 'mx-axis-label' }, f.svg);
            text.textContent = label;
        });
    }

    function hover(f, labels, x, slot, describe) {
        labels.forEach((label, index) => {
            const hit = el('rect', { x: x(index) - slot / 2, y: f.pad.top, width: slot, height: f.plotH, fill: 'transparent' }, f.svg);
            hit.addEventListener('mouseenter', () => {
                f.tooltip.innerHTML = describe(index);
                f.tooltip.hidden = false;
                const box = f.svg.getBoundingClientRect();
                const scale = box.width / f.width;
                f.tooltip.style.left = `${x(index) * scale}px`;
                f.tooltip.style.top = `${f.pad.top * scale + 4}px`;
            });
            hit.addEventListener('mouseleave', () => { f.tooltip.hidden = true; });
        });
    }

    function tickStep(count, width) {
        const fit = Math.max(Math.floor(width / 58), 2);
        return Math.max(Math.ceil(count / fit), 1);
    }

    function drawBars(container, config) {
        const f = frame(container, config);
        const { labels, series } = config;
        const stacked = Boolean(config.stacked);
        const totals = labels.map((_, i) => (stacked
            ? series.reduce((sum, s) => sum + (Number(s.values[i]) || 0), 0)
            : Math.max(...series.map((s) => Number(s.values[i]) || 0))));
        const targetMax = config.target ? Math.max(...config.target.values.map((v) => Number(v) || 0)) : 0;
        const max = niceMax(Math.max(...totals, targetMax, 0));
        const slot = f.plotW / Math.max(labels.length, 1);
        const x = (i) => f.pad.left + slot * i + slot / 2;
        const y = (v) => f.pad.top + f.plotH - (v / max) * f.plotH;
        axis(f, max, config.format, labels, x, tickStep(labels.length, f.plotW));
        const barWidth = Math.min(slot * (stacked ? 0.62 : 0.72), 34);
        labels.forEach((_, i) => {
            let base = 0;
            series.forEach((s, si) => {
                const value = Number(s.values[i]) || 0;
                if (value <= 0) return;
                const w = stacked ? barWidth : barWidth / series.length;
                const left = stacked ? x(i) - barWidth / 2 : x(i) - barWidth / 2 + w * si;
                const top = y(base + value);
                const h = Math.max(y(base) - top, 0.5);
                el('rect', { x: left, y: top, width: Math.max(w - (stacked ? 0 : 1), 1), height: h, rx: 2, fill: color(s.color), class: 'mx-bar', opacity: s.opacity || 1 }, f.svg);
                if (stacked) base += value;
            });
        });
        if (config.target) {
            config.target.values.forEach((value, i) => {
                if (!value) return;
                const yy = y(Number(value));
                el('line', { x1: x(i) - barWidth / 2 - 4, x2: x(i) + barWidth / 2 + 4, y1: yy, y2: yy, stroke: color(config.target.color || '--mx-text'), 'stroke-width': 2.5, 'stroke-linecap': 'round' }, f.svg);
            });
        }
        if (config.line) {
            const pts = config.line.values.map((v, i) => (v == null ? null : [x(i), y(Number(v))]));
            const d = pts.reduce((acc, p, i) => (p ? `${acc}${acc && pts[i - 1] ? 'L' : 'M'}${p[0]},${p[1]}` : acc), '');
            el('path', { d, fill: 'none', stroke: color(config.line.color), 'stroke-width': 2, 'stroke-dasharray': config.line.dashed ? '5 4' : '' }, f.svg);
        }
        hover(f, labels, x, slot, (i) => {
            const rows = series.map((s) => `${s.name}: <b>${(config.format || compact)(Number(s.values[i]) || 0)}</b>`);
            if (config.target) rows.push(`${config.target.name}: <b>${(config.format || compact)(Number(config.target.values[i]) || 0)}</b>`);
            if (config.line && config.line.values[i] != null) rows.push(`${config.line.name}: <b>${(config.format || compact)(Number(config.line.values[i]))}</b>`);
            if (config.extra) rows.push(config.extra(i));
            return `<strong>${labels[i]}</strong><br>${rows.join('<br>')}`;
        });
    }

    function drawLine(container, config) {
        const f = frame(container, config);
        const { labels, series } = config;
        const left = series.filter((s) => !s.right);
        const right = series.filter((s) => s.right);
        const maxOf = (list) => niceMax(Math.max(0, ...list.flatMap((s) => s.values.map((v) => Number(v) || 0))));
        const maxLeft = maxOf(left);
        const maxRight = right.length ? maxOf(right) : 1;
        const slot = f.plotW / Math.max(labels.length - 1, 1);
        const x = (i) => f.pad.left + (labels.length === 1 ? f.plotW / 2 : slot * i);
        const yFor = (max) => (v) => f.pad.top + f.plotH - (v / max) * f.plotH;
        axis(f, maxLeft, config.format, labels, x, tickStep(labels.length, f.plotW));
        if (right.length) {
            [0, 0.5, 1].forEach((ratio) => {
                const text = el('text', { x: f.width - f.pad.right + 6, y: f.pad.top + f.plotH - ratio * f.plotH + 3, class: 'mx-axis-label' }, f.svg);
                text.textContent = (config.rightFormat || compact)(maxRight * ratio, true);
            });
        }
        series.forEach((s) => {
            const y = yFor(s.right ? maxRight : maxLeft);
            const pts = s.values.map((v, i) => (v == null ? null : [x(i), y(Number(v))]));
            const d = pts.reduce((acc, p, i) => (p ? `${acc}${acc && pts[i - 1] ? 'L' : 'M'}${p[0]},${p[1]}` : acc), '');
            if (s.area && pts.every(Boolean)) {
                el('path', { d: `${d}L${x(pts.length - 1)},${y(0)}L${x(0)},${y(0)}Z`, fill: color(s.color), opacity: 0.12 }, f.svg);
            }
            el('path', { d, fill: 'none', stroke: color(s.color), 'stroke-width': 2, 'stroke-dasharray': s.dashed ? '5 4' : '', 'stroke-linejoin': 'round' }, f.svg);
            pts.forEach((p) => { if (p) el('circle', { cx: p[0], cy: p[1], r: 2.6, fill: color(s.color), class: 'mx-dot' }, f.svg); });
        });
        hover(f, labels, x, slot || f.plotW, (i) => {
            const rows = series.map((s) => {
                const value = s.values[i];
                const fmt = s.right ? (config.rightFormat || compact) : (config.format || compact);
                return `${s.name}: <b>${value == null ? '–' : fmt(Number(value))}</b>`;
            });
            return `<strong>${labels[i]}</strong><br>${rows.join('<br>')}`;
        });
    }

    /* Rótulo do eixo em até 3 linhas (quebra por palavra). */
    function wrapLabel(parent, text, x, y, width) {
        const words = String(text).split(' ');
        const maxChars = Math.max(Math.floor(width / 6.2), 6);
        const lines = [];
        let line = '';
        words.forEach((word) => {
            const next = line ? `${line} ${word}` : word;
            if (next.length > maxChars && line) { lines.push(line); line = word; } else { line = next; }
        });
        if (line) lines.push(line);
        const node = el('text', { x, y, 'text-anchor': 'middle', class: 'mx-axis-label' }, parent);
        lines.slice(0, 3).forEach((part, index) => {
            const span = el('tspan', { x, dy: index ? 11 : 0 }, node);
            span.textContent = index === 2 && lines.length > 3 ? `${part}…` : part;
        });
    }

    /* Cascata (ponte): total inicial, efeitos (+ azul, − vermelho) e total final, em cinza.
       config = {start: {label, value}, steps: [{label, value, detail}], end: {label, value}, format} */
    function drawWaterfall(container, config) {
        const items = [
            { label: config.start.label, value: config.start.value, total: true },
            ...config.steps.filter((step) => step.value).map((step) => ({ ...step, total: false })),
            { label: config.end.label, value: config.end.value, total: true },
        ];
        const f = frame(container, { ...config, height: config.height || 300 });
        f.pad.bottom = 46;
        f.plotH = f.height - f.pad.top - f.pad.bottom;
        let running = 0;
        const bars = items.map((item) => {
            if (item.total) { running = item.value; return { ...item, from: 0, to: item.value }; }
            const from = running;
            running += item.value;
            return { ...item, from, to: running };
        });
        const top = niceMax(Math.max(...bars.map((bar) => Math.max(bar.from, bar.to)), 0));
        const bottom = Math.min(0, ...bars.map((bar) => Math.min(bar.from, bar.to)));
        const span = top - bottom || 1;
        const slot = f.plotW / bars.length;
        const x = (index) => f.pad.left + slot * index + slot / 2;
        const y = (value) => f.pad.top + f.plotH - ((value - bottom) / span) * f.plotH;
        const format = config.format || ((value) => `R$ ${compact(value)}`);
        [0, 0.25, 0.5, 0.75, 1].forEach((ratio) => {
            const value = bottom + span * ratio;
            const yy = y(value);
            el('line', { x1: f.pad.left, x2: f.width - f.pad.right, y1: yy, y2: yy, class: 'mx-grid-line' }, f.svg);
            const label = el('text', { x: f.pad.left - 6, y: yy + 3, 'text-anchor': 'end', class: 'mx-axis-label' }, f.svg);
            label.textContent = format(value, true);
        });
        const width = Math.min(slot * 0.62, 56);
        bars.forEach((bar, index) => {
            const top1 = y(Math.max(bar.from, bar.to));
            const height = Math.max(Math.abs(y(bar.from) - y(bar.to)), 1.5);
            const fill = bar.total ? '--mx-chart-3' : bar.value >= 0 ? '--mx-div-pos-2' : '--mx-div-neg-2';
            el('rect', { x: x(index) - width / 2, y: top1, width, height, rx: 3, fill: color(fill), class: 'mx-bar' }, f.svg);
            if (index < bars.length - 1) {
                const level = y(bar.to);
                el('line', { x1: x(index) + width / 2, x2: x(index + 1) - width / 2, y1: level, y2: level, class: 'mx-waterfall-link' }, f.svg);
            }
            const above = bar.total || bar.value >= 0;
            const value = el('text', {
                x: x(index), y: above ? top1 - 5 : top1 + height + 11, 'text-anchor': 'middle', class: 'mx-value-label',
            }, f.svg);
            value.textContent = bar.total ? format(bar.value, true) : `${bar.value > 0 ? '+' : '−'}${format(Math.abs(bar.value), true)}`;
            wrapLabel(f.svg, bar.label, x(index), f.height - f.pad.bottom + 14, slot - 4);
        });
        hover(f, bars.map((bar) => bar.label), x, slot, (index) => {
            const bar = bars[index];
            const value = bar.total ? format(bar.value) : `${bar.value > 0 ? '+' : '−'}${format(Math.abs(bar.value))}`;
            return `<strong>${bar.label}</strong><br>${value}${bar.detail ? `<br><span>${bar.detail}</span>` : ''}`;
        });
    }

    /* Bolhas: x em escala log (faturamento), y (margem), área da bolha = lucro.
       config = {points: [{x, y, size, label, sub, href}], refX, refY, xFormat, yFormat, yMin, yMax, quadrants: [tl, tr, bl, br]} */
    function drawScatter(container, config) {
        const f = frame(container, { ...config, height: config.height || 360 });
        const points = config.points.filter((point) => point.x > 0 && point.y != null);
        if (!points.length) return;
        const xs = points.map((point) => Math.log10(point.x));
        const minX = Math.floor(Math.min(...xs));
        const maxX = Math.ceil(Math.max(...xs));
        const yMin = config.yMin != null ? config.yMin : Math.min(...points.map((point) => point.y));
        const yMax = config.yMax != null ? config.yMax : Math.max(...points.map((point) => point.y));
        const x = (value) => f.pad.left + ((Math.log10(value) - minX) / ((maxX - minX) || 1)) * f.plotW;
        const y = (value) => f.pad.top + f.plotH - ((Math.min(Math.max(value, yMin), yMax) - yMin) / ((yMax - yMin) || 1)) * f.plotH;
        const xFormat = config.xFormat || compact;
        const yFormat = config.yFormat || ((value) => `${Math.round(value * 100)}%`);
        for (let power = minX; power <= maxX; power += 1) {
            const xx = x(10 ** power);
            el('line', { x1: xx, x2: xx, y1: f.pad.top, y2: f.pad.top + f.plotH, class: 'mx-grid-line' }, f.svg);
            const label = el('text', { x: xx, y: f.height - 8, 'text-anchor': 'middle', class: 'mx-axis-label' }, f.svg);
            label.textContent = xFormat(10 ** power, true);
        }
        const step = (yMax - yMin) / 4;
        for (let index = 0; index <= 4; index += 1) {
            const value = yMin + step * index;
            const yy = y(value);
            el('line', { x1: f.pad.left, x2: f.width - f.pad.right, y1: yy, y2: yy, class: 'mx-grid-line' }, f.svg);
            const label = el('text', { x: f.pad.left - 6, y: yy + 3, 'text-anchor': 'end', class: 'mx-axis-label' }, f.svg);
            label.textContent = yFormat(value);
        }
        if (config.refX) el('line', { x1: x(config.refX), x2: x(config.refX), y1: f.pad.top, y2: f.pad.top + f.plotH, class: 'mx-ref-line' }, f.svg);
        if (config.refY != null) el('line', { x1: f.pad.left, x2: f.width - f.pad.right, y1: y(config.refY), y2: y(config.refY), class: 'mx-ref-line' }, f.svg);
        (config.quadrants || []).forEach((text, index) => {
            const left = index % 2 === 0;
            const upper = index < 2;
            const label = el('text', {
                x: left ? f.pad.left + 6 : f.width - f.pad.right - 6,
                y: upper ? f.pad.top + 12 : f.pad.top + f.plotH - 6,
                'text-anchor': left ? 'start' : 'end', class: 'mx-quadrant-label',
            }, f.svg);
            label.textContent = text;
        });
        const maxSize = Math.max(...points.map((point) => point.size || 0), 1);
        const ordered = points.slice().sort((a, b) => (b.size || 0) - (a.size || 0));
        ordered.forEach((point) => {
            const radius = 3 + Math.sqrt((point.size || 0) / maxSize) * 13;
            const clipped = point.y < yMin || point.y > yMax;
            const dot = el('circle', {
                cx: x(point.x), cy: y(point.y), r: radius.toFixed(1),
                fill: color('--mx-chart-1'), class: `mx-bubble${clipped ? ' is-clipped' : ''}`, tabindex: point.href ? 0 : -1,
            }, f.svg);
            const show = () => {
                f.tooltip.innerHTML = '';
                const strong = document.createElement('strong');
                strong.textContent = point.label;
                f.tooltip.append(strong, document.createElement('br'), document.createTextNode(`${xFormat(point.x)} · margem ${yFormat(point.y)}${clipped ? ' (fora da escala)' : ''}`));
                if (point.sub) f.tooltip.append(document.createElement('br'), document.createTextNode(point.sub));
                f.tooltip.hidden = false;
                const box = f.svg.getBoundingClientRect();
                const scale = box.width / f.width;
                f.tooltip.style.left = `${x(point.x) * scale}px`;
                f.tooltip.style.top = `${(y(point.y) - radius) * scale}px`;
                dot.classList.add('is-hover');
            };
            const hide = () => { f.tooltip.hidden = true; dot.classList.remove('is-hover'); };
            dot.addEventListener('pointerenter', show);
            dot.addEventListener('pointerleave', hide);
            dot.addEventListener('focus', show);
            dot.addEventListener('blur', hide);
            if (point.href) {
                dot.addEventListener('click', () => { window.location.href = point.href; });
                dot.addEventListener('keydown', (event) => { if (event.key === 'Enter') window.location.href = point.href; });
            }
        });
    }

    const DRAWERS = { bars: drawBars, line: drawLine, waterfall: drawWaterfall, scatter: drawScatter };

    function mount(container, type, config) {
        const draw = () => DRAWERS[type](container, config);
        registry.add(draw);
        draw();
        if (window.ResizeObserver) {
            let last = container.clientWidth;
            new ResizeObserver(() => {
                if (Math.abs(container.clientWidth - last) < 8) return;
                last = container.clientWidth;
                draw();
            }).observe(container);
        }
    }

    document.addEventListener('mx:theme', () => registry.forEach((draw) => draw()));

    window.MXCharts = {
        bars: (container, config) => mount(container, 'bars', config),
        line: (container, config) => mount(container, 'line', config),
        waterfall: (container, config) => mount(container, 'waterfall', config),
        scatter: (container, config) => mount(container, 'scatter', config),
        money: (value, short) => (short
            ? `R$ ${compact(value)}`
            : Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: value >= 1000 ? 0 : 2 }).format(value)),
        kg: (value, short) => (Math.abs(value) >= 1000 ? `${compact(value / 1000)} t` : `${Math.round(value)} kg`),
        compact,
    };
})();
