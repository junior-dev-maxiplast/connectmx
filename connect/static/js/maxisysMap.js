/* Mapa coroplético do padrão MaxiSys: malha do IBGE (GeoJSON) desenhada em SVG.

   Brasil por UF e, ao clicar numa UF, os municípios dela. As cores vêm das
   variáveis CSS (--mx-seq-* para magnitude, --mx-div-* para acima/abaixo de uma
   referência), então o tema claro/escuro vale sem recarregar.

   MXMap.mount(container, {geoUrl, data, metric, metrics, onSelect})
     geoUrl(key)  -> URL da malha ("BR" ou o código IBGE da UF)
     data         -> {states: {PR: {...}}, cities: {"4106902": {...}}}
     metrics      -> {net: {label, kind: "seq"|"div", value(area), format(v), reference?}}
*/
(function () {
    const NS = 'http://www.w3.org/2000/svg';
    const WIDTH = 1000;
    const PAD = 12;
    const geoCache = new Map();

    const el = (tag, attrs, parent) => {
        const node = document.createElementNS(NS, tag);
        Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
        if (parent) parent.appendChild(node);
        return node;
    };

    function loadGeo(url) {
        if (!geoCache.has(url)) {
            geoCache.set(url, fetch(url, { credentials: 'same-origin' }).then(async (response) => {
                const body = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(body.error || 'Não foi possível carregar o desenho do mapa.');
                return body;
            }).catch((error) => { geoCache.delete(url); throw error; }));
        }
        return geoCache.get(url);
    }

    function eachPoint(geometry, callback) {
        const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates;
        polygons.forEach((polygon) => polygon.forEach((ring) => ring.forEach((point) => callback(point))));
    }

    function projection(features, width) {
        let minLon = Infinity; let minLat = Infinity; let maxLon = -Infinity; let maxLat = -Infinity;
        features.forEach((feature) => eachPoint(feature.geometry, ([lon, lat]) => {
            if (lon < minLon) minLon = lon;
            if (lon > maxLon) maxLon = lon;
            if (lat < minLat) minLat = lat;
            if (lat > maxLat) maxLat = lat;
        }));
        // Equirretangular com correção do cosseno da latitude média: boa o bastante
        // na escala de um país ou de uma UF, e sem biblioteca.
        const kx = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);
        const geoWidth = Math.max((maxLon - minLon) * kx, 1e-6);
        const geoHeight = Math.max(maxLat - minLat, 1e-6);
        const scale = (width - 2 * PAD) / geoWidth;
        const height = Math.round(geoHeight * scale + 2 * PAD);
        const project = (lon, lat) => [PAD + (lon - minLon) * kx * scale, PAD + (maxLat - lat) * scale];
        return { project, height };
    }

    function pathData(geometry, project) {
        const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates;
        return polygons.map((polygon) => polygon.map((ring) => ring.map((point, index) => {
            const [x, y] = project(point[0], point[1]);
            return `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
        }).join('') + 'Z').join('')).join('');
    }

    function quantileBreaks(values, classes) {
        const sorted = values.filter((value) => value != null && isFinite(value)).sort((a, b) => a - b);
        if (!sorted.length) return [];
        const breaks = [];
        for (let index = 1; index < classes; index += 1) {
            breaks.push(sorted[Math.min(sorted.length - 1, Math.floor((index * sorted.length) / classes))]);
        }
        return breaks;
    }

    function classOf(value, breaks) {
        let index = 0;
        while (index < breaks.length && value > breaks[index]) index += 1;
        return index;
    }

    function scaleFor(metric, areas) {
        const values = areas.map((area) => metric.value(area)).filter((value) => value != null);
        if (metric.kind === 'div') {
            const reference = metric.reference || 0;
            const step = metric.step || 0.05;
            const breaks = [reference - 2 * step, reference - step / 2, reference + step / 2, reference + 2 * step];
            const fills = ['--mx-div-neg-2', '--mx-div-neg-1', '--mx-div-mid', '--mx-div-pos-1', '--mx-div-pos-2'];
            return {
                fill: (value) => (value == null ? null : `var(${fills[classOf(value, breaks)]})`),
                legend: fills.map((fill, index) => ({
                    fill: `var(${fill})`,
                    label: index === 0 ? `até ${metric.format(breaks[0])}`
                        : index === fills.length - 1 ? `acima de ${metric.format(breaks[3])}`
                            : index === 2 ? `perto de ${metric.format(reference)}`
                                : `${metric.format(breaks[index - 1])} a ${metric.format(breaks[index])}`,
                })),
            };
        }
        const positive = values.filter((value) => value > 0);
        const breaks = quantileBreaks(positive, 5);
        const fills = ['--mx-seq-1', '--mx-seq-2', '--mx-seq-3', '--mx-seq-4', '--mx-seq-5'];
        const max = positive.length ? Math.max(...positive) : 0;
        const min = positive.length ? Math.min(...positive) : 0;
        const edges = [min, ...breaks, max];
        return {
            fill: (value) => (value == null || value <= 0 ? null : `var(${fills[classOf(value, breaks)]})`),
            legend: positive.length ? fills.map((fill, index) => ({
                fill: `var(${fill})`,
                label: index === 0 ? `até ${metric.format(edges[1])}` : index === fills.length - 1 ? `acima de ${metric.format(edges[4])}` : `${metric.format(edges[index])} a ${metric.format(edges[index + 1])}`,
            })).filter((item, index, list) => index === 0 || item.label !== list[index - 1].label) : [],
        };
    }

    function mount(container, options) {
        const state = { level: 'country', stateSigla: null, metric: options.metric || 'net', selectedCode: null };
        const listeners = [];
        const svgBox = container.querySelector('[data-mx-map-canvas]') || container;
        const legendBox = container.querySelector('[data-mx-map-legend]');
        const statusBox = container.querySelector('[data-mx-map-status]');
        const tooltip = document.createElement('div');
        tooltip.className = 'mx-tooltip mx-map-tooltip';
        tooltip.hidden = true;
        container.appendChild(tooltip);

        const emit = (detail) => listeners.forEach((listener) => listener(detail));
        const status = (text) => { if (statusBox) { statusBox.textContent = text || ''; statusBox.hidden = !text; } };

        const areaFor = (code) => {
            if (state.level === 'country') {
                const sigla = Object.keys(options.data.states).find((key) => String(options.data.states[key].ibge) === String(code));
                return sigla ? { key: sigla, area: options.data.states[sigla] } : { key: null, area: null };
            }
            return { key: String(code), area: options.data.cities[String(code)] || null };
        };

        function showTooltip(event, html) {
            tooltip.replaceChildren();
            html.forEach(([text, strong], index) => {
                if (index) tooltip.appendChild(document.createElement('br'));
                const node = document.createElement(strong ? 'strong' : 'span');
                node.textContent = text;
                tooltip.appendChild(node);
            });
            tooltip.hidden = false;
            const box = container.getBoundingClientRect();
            const x = (event.clientX || box.left + box.width / 2) - box.left;
            const y = (event.clientY || box.top + 40) - box.top;
            tooltip.style.left = `${Math.min(Math.max(x + 14, 8), box.width - 220)}px`;
            tooltip.style.top = `${Math.max(y - 10, 8)}px`;
        }

        function describe(name, area) {
            const metric = options.metrics[state.metric];
            if (!area) return [[name, true], ['Sem cliente no período', false]];
            const lines = [[name, true], [`${metric.label}: ${metric.format(metric.value(area))}`, false]];
            if (state.metric !== 'net') lines.push([`Faturamento: ${options.metrics.net.format(area.net)}`, false]);
            if (state.metric !== 'customers') lines.push([`Clientes: ${area.customers}`, false]);
            if (state.metric !== 'margin' && area.margin != null) lines.push([`Margem: ${options.metrics.margin.format(area.margin)}`, false]);
            if (state.metric !== 'growth' && area.growth != null) lines.push([`Crescimento: ${options.metrics.growth.format(area.growth)}`, false]);
            if (!area.customers) lines.push(['Perdemos os clientes daqui', false]);
            return lines;
        }

        async function draw() {
            const key = state.level === 'country' ? 'BR' : String(options.data.states[state.stateSigla].ibge);
            status('Carregando o mapa…');
            let geo;
            try {
                geo = await loadGeo(options.geoUrl(key));
            } catch (error) {
                status(error.message);
                return;
            }
            status('');
            const metric = options.metrics[state.metric];
            const features = geo.features || [];
            const { project, height } = projection(features, WIDTH);
            const areas = features.map((feature) => areaFor(feature.properties.code).area).filter(Boolean);
            const scale = scaleFor(metric, areas);
            svgBox.replaceChildren();
            const svg = el('svg', { viewBox: `0 0 ${WIDTH} ${height}`, class: 'mx-map-svg', role: 'group', 'aria-label': state.level === 'country' ? 'Mapa do Brasil por UF' : `Municípios de ${options.data.states[state.stateSigla].name}` }, svgBox);
            features.forEach((feature) => {
                const code = feature.properties.code;
                const { key: areaKey, area } = areaFor(code);
                const value = area ? metric.value(area) : null;
                const fill = area ? scale.fill(value) : null;
                const name = state.level === 'country'
                    ? (area ? area.name : (options.stateNames || {})[code] || code)
                    : (area ? `${area.name}/${area.state}` : `${feature.properties.name || 'Município'}/${state.stateSigla}`);
                const path = el('path', {
                    d: pathData(feature.geometry, project),
                    class: `mx-map-area${area ? ' has-data' : ''}${area && !area.customers ? ' is-lost' : ''}${areaKey && areaKey === state.selectedCode ? ' is-selected' : ''}`,
                }, svg);
                if (fill) path.style.fill = fill;
                const clickable = Boolean(area);
                if (clickable) {
                    path.setAttribute('tabindex', '0');
                    path.setAttribute('role', 'button');
                    path.setAttribute('aria-label', describe(name, area).map((line) => line[0]).join('. '));
                }
                const enter = (event) => { path.classList.add('is-hover'); showTooltip(event, describe(name, area)); };
                const leave = () => { path.classList.remove('is-hover'); tooltip.hidden = true; };
                path.addEventListener('pointermove', enter);
                path.addEventListener('pointerleave', leave);
                path.addEventListener('focus', (event) => { const box = path.getBoundingClientRect(); enter({ clientX: box.left + box.width / 2, clientY: box.top + box.height / 2, ...event }); });
                path.addEventListener('blur', leave);
                const select = () => {
                    if (!clickable) return;
                    if (state.level === 'country') {
                        showState(areaKey);
                    } else {
                        state.selectedCode = areaKey;
                        svg.querySelectorAll('.is-selected').forEach((node) => node.classList.remove('is-selected'));
                        path.classList.add('is-selected');
                        emit({ level: 'city', state: state.stateSigla, key: areaKey });
                    }
                };
                path.addEventListener('click', select);
                path.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } });
            });
            if (legendBox) {
                legendBox.replaceChildren();
                const title = document.createElement('span');
                title.className = 'mx-map-legend-title';
                title.textContent = metric.label;
                legendBox.appendChild(title);
                scale.legend.forEach((item) => {
                    const entry = document.createElement('span');
                    const swatch = document.createElement('i');
                    swatch.style.background = item.fill;
                    entry.append(swatch, document.createTextNode(item.label));
                    legendBox.appendChild(entry);
                });
                const empty = document.createElement('span');
                const swatch = document.createElement('i');
                swatch.className = 'is-empty';
                empty.append(swatch, document.createTextNode('sem cliente'));
                legendBox.appendChild(empty);
            }
        }

        function showCountry() {
            state.level = 'country';
            state.stateSigla = null;
            state.selectedCode = null;
            emit({ level: 'country' });
            return draw();
        }

        function showState(sigla) {
            if (!options.data.states[sigla]) return showCountry();
            state.level = 'state';
            state.stateSigla = sigla;
            state.selectedCode = null;
            emit({ level: 'state', state: sigla });
            return draw();
        }

        function setMetric(metric) {
            if (!options.metrics[metric]) return;
            state.metric = metric;
            draw();
        }

        let lastWidth = container.clientWidth;
        if (window.ResizeObserver) {
            new ResizeObserver(() => {
                if (Math.abs(container.clientWidth - lastWidth) > 8) { lastWidth = container.clientWidth; tooltip.hidden = true; }
            }).observe(container);
        }
        document.addEventListener('mx:theme', () => draw());

        return {
            showCountry,
            showState,
            setMetric,
            onSelect: (listener) => listeners.push(listener),
            state: () => ({ ...state }),
        };
    }

    window.MXMap = { mount };
})();
