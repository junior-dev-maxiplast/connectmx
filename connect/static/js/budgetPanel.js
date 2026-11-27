/* Painel "Orçamento" do Dashes (tela tiqueue/maxisys/panel_budget.html; dados de tiqueue/budget_panel.py).

   Refaz as contas do PBIX "2. ORÇAMENTO 2026": meta × faturado × previsão (faturado + a faturar) por
   unidade de orçamento, família da meta, mês e representante. O servidor manda fatos pequenos; a página
   filtra e soma no navegador (MXLive.crossFilter) e cada visual abre os registros por trás do ponto
   (MXDrill: botão direito, toque longo, ⋯ ou o selo "Expandir dados"), das somas até a nota fiscal. */
(() => {
    'use strict';

    const L = window.MXLive;
    const D = window.MXDrill;
    if (!L || !D) return;
    const fmt = L.fmt;
    const read = (id) => JSON.parse(document.getElementById(id).textContent);
    const data = read('bgData');
    const config = read('bgConfig');
    const year = data.year;
    const MONTHS = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];
    const SHORT = L.MONTHS_SHORT;
    const ALL_MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
    const range = (from, to) => { const list = []; for (let value = from; value <= to; value += 1) list.push(value); return list; };
    const todayTime = new Date(`${data.today}T12:00:00`).getTime();

    // Preferências de quem vê (base do valor e medida dos gráficos): só conveniência, pode faltar.
    const store = {
        get(key) { try { return window.localStorage.getItem(key); } catch (error) { return null; } },
        set(key, value) { try { window.localStorage.setItem(key, value); } catch (error) { /* navegador sem armazenamento */ } },
    };

    // ------------------------------------------------------------------ formatos
    const numberFormat = (digits) => new Intl.NumberFormat('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    const N0 = numberFormat(0);
    const N1 = numberFormat(1);
    const N2 = numberFormat(2);
    const finite = (value) => value != null && Number.isFinite(value);
    const tons = (kg) => {
        if (!finite(kg)) return '–';
        const abs = Math.abs(kg);
        if (abs >= 100000) return `${N0.format(kg / 1000)} t`;
        if (abs >= 1000) return `${N1.format(kg / 1000)} t`;
        return `${N0.format(kg)} kg`;
    };
    const kgFull = (kg) => (finite(kg) ? `${N0.format(kg)} kg` : '–');
    const money = (value) => (finite(value) ? fmt.money(value) : '–');
    const moneyFull = (value) => (finite(value) ? fmt.moneyFull(value) : '–');
    const pct = (value, digits = 0) => (finite(value) ? fmt.pct(value, digits) : '–');
    const signedPct = (value) => (finite(value) ? fmt.signedPct(value) : '–');
    const signed = (value, format) => (finite(value) ? `${value > 0 ? '+' : value < 0 ? '−' : ''}${format(Math.abs(value))}` : '–');
    const ratio = (top, bottom) => (bottom ? top / bottom : null);
    const perKg = (value, kg) => (kg ? `R$ ${N2.format(value / kg)}/kg` : '–');
    const dateText = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : '–');
    const daysLate = (iso) => (iso ? Math.max(Math.round((todayTime - new Date(`${iso}T12:00:00`).getTime()) / 86400000), 0) : null);

    // ------------------------------------------------------------------ nomes
    const unitName = (code) => data.names.units[code] || `Unidade ${code}`;
    const repName = (code) => (code == null || code === '' || code === 'null' ? 'Sem representante' : data.names.reps[code] || `Representante ${code}`);
    const customerName = (code) => (data.names.customers[code] || {}).name || `Cliente ${code}`;
    const customerPlace = (code) => (data.names.customers[code] || {}).place || '';
    const branchName = (key) => data.names.branches[key] || `Filial ${key}`;
    const familyInfo = (code) => data.families[code] || { label: String(code), groups: [], name: '' };
    const familyLabel = (code) => familyInfo(code).label || String(code);
    const familyShort = (code) => {
        const info = familyInfo(code);
        return (info.groups && info.groups.length ? info.groups.join(' + ') : info.name) || String(code);
    };
    const profileUrl = (code) => config.profile_url_template.replace('__code__', code);
    const branchUnit = (key) => Number(String(key || '').split('-')[1]);
    const NAMES = { m: (value) => MONTHS[Number(value) - 1] || value, u: unitName, f: familyShort, r: repName, c: customerName };

    // ------------------------------------------------------------------ base do valor e medida
    // A meta em R$ vem na base do orçamento (com impostos no PBIX de 2025/2026, sem na projeção de 2027).
    // Na outra base ela é convertida pela parte do valor bruto que sobra sem ICMS, PIS e COFINS, faturada
    // na mesma unidade e família (este ano e o anterior).
    const BASES = { bruto: 'com impostos', sem_impostos: 'sem impostos' };
    const basisKey = `bgBasis:${year}`;
    let basis = BASES[store.get(basisKey)] ? store.get(basisKey) : data.basis;
    let measure = store.get('bgMeasure') === 'value' ? 'value' : 'kg';
    const isKg = () => measure === 'kg';
    const showMeasure = (value) => (isKg() ? tons(value) : money(value));

    const taxRatio = (() => {
        const byFamily = new Map();
        const byUnit = new Map();
        const all = [0, 0];
        const add = (map, key, gross, net) => {
            const entry = map.get(key) || [0, 0];
            entry[0] += gross;
            entry[1] += net;
            map.set(key, entry);
        };
        [...data.billing, ...data.previous].forEach((fact) => {
            const gross = fact.n || 0;
            const net = fact.t || 0;
            if (gross <= 0 || net <= 0) return;
            add(byFamily, `${fact.u}|${fact.f}`, gross, net);
            add(byUnit, String(fact.u), gross, net);
            all[0] += gross;
            all[1] += net;
        });
        const of = (entry) => (entry && entry[0] > 0 ? entry[1] / entry[0] : null);
        const overall = of(all) || 1;
        return (unit, family) => (family != null && of(byFamily.get(`${unit}|${family}`))) || of(byUnit.get(String(unit))) || overall;
    })();

    // Carteira: o item conta no mês da previsão de entrega; atrasado (ou sem data) conta no mês corrente,
    // que é quando ele ainda pode ser faturado. Entrega fora do ano (inclusive atraso de anos anteriores,
    // como no filtro de ano do PBIX) não entra na previsão; o atraso antigo aparece na lista de atrasos.
    const orderMonth = (fact) => {
        if (fact.mk == null) return data.is_current ? data.current_month : null;
        if (Math.floor(fact.mk / 12) !== year) return null;
        return fact.late ? data.current_month : (fact.mk % 12) + 1;
    };
    const currentMonthKey = data.today.slice(0, 7);
    // "ATRASO" do PBIX: item atrasado com entrega prevista no mês corrente (data relativa "este mês").
    const lateThisMonth = (fact) => !!fact.d && fact.d.slice(0, 7) === currentMonthKey;
    const allOrders = (data.orders || []).map((fact) => ({ ...fact, m: orderMonth(fact) }));
    // A carteira só vem para o ano corrente e os seguintes (ano encerrado não tem o que faturar).
    const hasOrders = !!(data.is_current || data.is_future);

    let billing = [];
    let previous = [];
    let orders = [];
    let lateOrders = [];
    let unitGoals = [];
    let repGoals = [];
    let breakEven = [];
    function rebase() {
        const net = basis === 'sem_impostos';
        const goalValue = (fact) => {
            const value = fact.v || 0;
            if (basis === data.basis) return value;
            const factor = taxRatio(fact.u, fact.f);
            return data.basis === 'bruto' ? value * factor : value / factor;
        };
        billing = data.billing.map((fact) => ({ ...fact, val: (net ? fact.t : fact.n) || 0 }));
        previous = data.previous.map((fact) => ({ ...fact, val: (net ? fact.t : fact.n) || 0 }));
        const priced = allOrders.map((fact) => ({ ...fact, val: (fact.v || 0) * (net ? taxRatio(fact.u, fact.f) : 1) }));
        orders = priced.filter((fact) => fact.m != null);
        lateOrders = data.is_current ? priced.filter((fact) => fact.late) : [];
        unitGoals = data.goals.units.map((fact) => ({ ...fact, val: goalValue(fact) }));
        repGoals = data.goals.reps.map((fact) => ({ ...fact, val: goalValue(fact) }));
        // O ponto de equilíbrio do PBIX é comparado com o valor bruto faturado.
        breakEven = data.goals.break_even.map((fact) => ({ ...fact, val: (fact.v || 0) * (net ? taxRatio(fact.u, null) : 1) }));
    }
    rebase();

    // ------------------------------------------------------------------ filtro cruzado
    const filter = L.crossFilter({
        labels: { m: 'Mês', u: 'Unidade', f: 'Família', r: 'Representante', c: 'Cliente' },
        names: NAMES,
        chips: '#mxFilterBar',
    });
    const without = (state, skip) => {
        const copy = { ...state };
        [].concat(skip || []).forEach((key) => { delete copy[key]; });
        return copy;
    };
    const picked = (state, key) => L.chosen(state[key]);
    const monthsOf = (state) => picked(state, 'm').map(Number);
    const matches = (fact, state) => L.passAll(fact, state);
    const only = (state, key, value) => ({ ...state, [key]: [String(value)] });

    // Meta do recorte: a das unidades (mês, unidade e família); com filtro de representante (ou quando a
    // conta é por representante), a soma das metas deles; com filtro de cliente não há meta.
    function goalFacts(state, byRep) {
        if (picked(state, 'c').length) return null;
        if (byRep || picked(state, 'r').length) return repGoals.filter((fact) => matches(fact, state));
        return unitGoals.filter((fact) => matches(fact, state));
    }

    // Meses comparáveis com o ano anterior: os escolhidos; sem escolha, até o mês corrente (ano em
    // andamento) ou o ano inteiro.
    const comparableMonths = (state) => {
        if (data.is_future) return [];
        const list = monthsOf(state);
        if (list.length) return list;
        return data.is_current ? range(1, data.current_month) : ALL_MONTHS;
    };

    const MEASURES = ['goalKg', 'goalVal', 'kg', 'val', 'openKg', 'openVal', 'ready', 'readyVal', 'lateKg', 'lateVal', 'prevKg', 'prevVal'];
    const blank = (key) => ({ key, hasGoal: false, ...Object.fromEntries(MEASURES.map((name) => [name, 0])) });
    function finish(item) {
        item.fcKg = item.kg + item.openKg;
        item.fcVal = item.val + item.openVal;
        item.attKg = item.hasGoal ? ratio(item.fcKg, item.goalKg) : null;
        item.attVal = item.hasGoal ? ratio(item.fcVal, item.goalVal) : null;
        item.billedKg = item.hasGoal ? ratio(item.kg, item.goalKg) : null;
        item.billedVal = item.hasGoal ? ratio(item.val, item.goalVal) : null;
        return item;
    }
    const repKey = (fact) => (fact.r == null ? 'null' : String(fact.r));

    /* Soma meta, faturado, carteira e ano anterior do recorte, agrupando por uma dimensão (m, u, f, r, c
       ou uma função). `allMonths`: o ano anterior de todos os meses (gráfico mês a mês); senão, só dos
       meses comparáveis. `byRep`: a meta vem das metas por representante. */
    function cube(state, dimension, options = {}) {
        const byRep = options.byRep != null ? options.byRep : dimension === 'r';
        const keyOf = typeof dimension === 'function' ? dimension : dimension === 'r' ? repKey : (fact) => fact[dimension];
        const map = new Map();
        const entry = (fact) => {
            const key = keyOf(fact);
            if (key == null) return null;
            let item = map.get(key);
            if (!item) { item = blank(key); map.set(key, item); }
            return item;
        };
        (goalFacts(state, byRep) || []).forEach((fact) => {
            const item = entry(fact);
            if (!item) return;
            item.goalKg += fact.k || 0;
            item.goalVal += fact.val;
            item.hasGoal = true;
        });
        billing.forEach((fact) => {
            if (!matches(fact, state)) return;
            const item = entry(fact);
            if (!item) return;
            item.kg += fact.k || 0;
            item.val += fact.val;
        });
        orders.forEach((fact) => {
            if (!matches(fact, state)) return;
            const item = entry(fact);
            if (!item) return;
            const kg = fact.k || 0;
            item.openKg += kg;
            item.openVal += fact.val;
            item.ready += fact.p || 0;
            item.readyVal += kg ? (fact.p || 0) * (fact.val / kg) : 0;
            if (fact.late) { item.lateKg += kg; item.lateVal += fact.val; }
        });
        if (!picked(state, 'c').length) {
            const allowed = options.allMonths ? null : comparableMonths(state);
            const scope = options.allMonths ? state : without(state, 'm');
            previous.forEach((fact) => {
                if ((allowed && !allowed.includes(fact.m)) || !matches(fact, scope)) return;
                const item = entry(fact);
                if (!item) return;
                item.prevKg += fact.k || 0;
                item.prevVal += fact.val;
            });
        }
        map.forEach(finish);
        return map;
    }
    const totalOf = (state) => cube(state, () => 'all').get('all') || finish(blank('all'));
    const nonEmpty = (item) => item.goalKg || item.kg || item.openKg || item.goalVal || item.val;
    const sumItems = (items) => finish(items.reduce((acc, item) => {
        MEASURES.forEach((name) => { acc[name] += item[name] || 0; });
        acc.hasGoal = acc.hasGoal || item.hasGoal;
        return acc;
    }, blank('total')));

    function breakEvenFor(state) {
        if (['f', 'r', 'c'].some((key) => picked(state, key).length)) return null;
        const rows = breakEven.filter((fact) => matches(fact, state));
        return rows.length ? rows.reduce((acc, fact) => acc + fact.val, 0) : null;
    }

    // Ritmo esperado: dias úteis já passados ÷ dias úteis dos meses do recorte.
    const business = new Map(data.business.map((item) => [item.m, item]));
    function paceOf(state) {
        const list = monthsOf(state);
        let total = 0;
        let elapsed = 0;
        (list.length ? list : ALL_MONTHS).forEach((month) => {
            const item = business.get(month);
            if (item) { total += item.total; elapsed += item.elapsed; }
        });
        return { total, elapsed, remaining: Math.max(total - elapsed, 0), ratio: total ? elapsed / total : null, closed: total > 0 && elapsed >= total, future: elapsed === 0 };
    }
    // Quanto da meta já devia estar feito: tudo no período encerrado; nada (sem cor) no que não começou.
    const expectedOf = (pace) => (pace.closed ? 1 : pace.future ? null : pace.ratio);
    function toneOf(value, expected) {
        if (!finite(value) || expected == null || expected <= 0) return null;
        const relative = value / expected;
        return relative >= 0.98 ? 'good' : relative >= 0.9 ? 'warn' : 'bad';
    }
    const TONE_COLOR = { good: '--mx-good', warn: '--mx-warn', bad: '--mx-bad' };

    function describe(state) {
        const parts = Object.keys(state).filter((key) => picked(state, key).length).map((key) => {
            const names = picked(state, key).map((value) => NAMES[key](value));
            return names.length > 3 ? `${names.slice(0, 3).join(', ')} +${names.length - 3}` : names.join(', ');
        });
        if (!monthsOf(state).length) parts.unshift(`${year} inteiro`);
        return parts.join(' · ');
    }

    // ------------------------------------------------------------------ DOM
    const NS = 'http://www.w3.org/2000/svg';
    function h(tag, className, text, parent) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        if (parent) parent.appendChild(node);
        return node;
    }
    function icon(name, parent) {
        const svg = document.createElementNS(NS, 'svg');
        svg.setAttribute('class', 'mx-ico');
        svg.setAttribute('aria-hidden', 'true');
        const use = document.createElementNS(NS, 'use');
        use.setAttribute('href', `#mx-i-${name}`);
        svg.appendChild(use);
        if (parent) parent.appendChild(svg);
        return svg;
    }
    const byId = (id) => document.getElementById(id);

    /* Tabela de card. Colunas {label, num}; linhas {cells, selected, className, clickable, index}; cada
       célula é um texto ou {text, sub, strong, bar, tone, title, button}. A linha leva data-mx-index
       (botão direito / toque longo abrem os registros) e, clicável, filtra a página. */
    function drawTable(container, columns, rows, options = {}) {
        container.replaceChildren();
        if (!rows.length) {
            h('p', 'mx-empty-note', options.empty || 'Sem dados para este filtro.', container);
            return;
        }
        const table = h('table', `mx-table mx-table-compact bg-table${options.className ? ` ${options.className}` : ''}`, null, container);
        const head = h('tr', null, null, h('thead', null, null, table));
        columns.forEach((column) => h('th', column.num ? 'is-num' : null, column.label, head));
        const body = h('tbody', null, null, table);
        const cellNode = (parent, column, cell, tag = 'td') => {
            const spec = cell != null && typeof cell === 'object' ? cell : { text: cell };
            const td = h(tag, [column.num ? 'is-num' : '', spec.strong ? 'is-strong' : '', spec.wrap ? 'bg-wrap' : '', spec.className || ''].filter(Boolean).join(' ') || null, null, parent);
            if (spec.button) td.appendChild(spec.button);
            if (spec.bar != null) {
                const bar = h('span', `bg-att${spec.tone ? ` is-${spec.tone}` : ''}`, null, td);
                h('i', null, null, bar).style.width = `${Math.max(Math.min(spec.bar, 1), 0) * 100}%`;
            }
            td.appendChild(document.createTextNode(spec.text == null ? '–' : String(spec.text)));
            if (spec.sub) h('span', 'mx-cell-sub', spec.sub, td);
            if (spec.title) td.title = spec.title;
            return td;
        };
        rows.forEach((row, index) => {
            const clickable = row.clickable !== false && !!options.clickable;
            const classes = [row.className, row.selected ? 'is-selected' : '', clickable ? 'is-clickable' : ''].filter(Boolean).join(' ');
            const tr = h('tr', classes || null, null, body);
            tr.setAttribute('data-mx-index', row.index != null ? row.index : index);
            if (clickable) tr.tabIndex = 0;
            if (row.selected) tr.setAttribute('aria-selected', 'true');
            row.cells.forEach((cell, column) => cellNode(tr, columns[column], cell));
        });
        if (options.footer) {
            const foot = h('tr', null, null, h('tfoot', null, null, table));
            options.footer.forEach((cell, column) => cellNode(foot, columns[column], cell));
        }
        if (options.more) h('p', 'bg-more', options.more, container);
    }

    // Clique (e Enter) numa linha clicável de uma tabela de card.
    function onRow(container, handler) {
        const run = (event) => {
            if (event.target.closest('a, button')) return;
            const tr = event.target.closest('tr[data-mx-index]');
            if (!tr || !container.contains(tr) || !tr.classList.contains('is-clickable')) return;
            if (event.type === 'keydown') event.preventDefault();
            handler(Number(tr.getAttribute('data-mx-index')), event);
        };
        container.addEventListener('click', run);
        container.addEventListener('keydown', (event) => { if (event.key === 'Enter') run(event); });
    }

    // ------------------------------------------------------------------ "Expandir dados"
    const rowFromItem = (item) => ({
        goalKg: item.hasGoal ? item.goalKg : null,
        kg: item.kg,
        openKg: item.openKg,
        ready: item.ready,
        fcKg: item.fcKg,
        att: item.attKg,
        gapKg: item.hasGoal ? Math.max(item.goalKg - item.fcKg, 0) : null,
        goalVal: item.hasGoal ? item.goalVal : null,
        val: item.val,
        openVal: item.openVal,
        fcVal: item.fcVal,
        price: item.kg ? item.val / item.kg : null,
        prevKg: item.prevKg || null,
        prevVal: item.prevVal || null,
    });
    const breakdownColumns = (first, expected) => [
        first,
        { key: 'goalKg', label: 'Meta (kg)', type: 'kg', total: true },
        { key: 'kg', label: 'Faturado (kg)', type: 'kg', total: true, strong: true },
        { key: 'openKg', label: 'A faturar (kg)', type: 'kg', total: true },
        { key: 'fcKg', label: 'Previsão (kg)', type: 'kg', total: true },
        { key: 'att', label: 'Previsão ÷ meta', type: 'pct', digits: 0, ratio: ['fcKg', 'goalKg'] },
        { key: 'gapKg', label: 'Falta (kg)', type: 'kg', total: true },
        { key: 'goalVal', label: 'Meta (R$)', type: 'money', total: true },
        { key: 'val', label: 'Faturado (R$)', type: 'money', total: true },
        { key: 'fcVal', label: 'Previsão (R$)', type: 'money', total: true },
        { key: 'price', label: 'R$/kg faturado', type: 'dec', digits: 2, ratio: ['val', 'kg'] },
        { key: 'prevKg', label: `${year - 1} (kg)`, type: 'kg', total: true },
    ].map((column) => (expected != null && column.key === 'att' ? { ...column, label: `Previsão ÷ meta (esperado ${pct(expected)})` } : column));
    const customerColumns = [
        { key: 'label', label: 'Cliente', href: 'href', sub: 'sub' },
        { key: 'kg', label: 'Faturado (kg)', type: 'kg', total: true, strong: true },
        { key: 'openKg', label: 'A faturar (kg)', type: 'kg', total: true },
        { key: 'fcKg', label: 'Previsão (kg)', type: 'kg', total: true },
        { key: 'val', label: 'Faturado (R$)', type: 'money', total: true },
        { key: 'openVal', label: 'A faturar (R$)', type: 'money', total: true },
        { key: 'price', label: 'R$/kg faturado', type: 'dec', digits: 2, ratio: ['val', 'kg'] },
        { key: 'prevKg', label: `${year - 1} (kg)`, type: 'kg', total: true },
    ];
    const orderColumns = [
        { key: 'order', label: 'Pedido', nowrap: true },
        { key: 'customer', label: 'Cliente', href: 'href', sub: 'rep' },
        { key: 'unit', label: 'Unidade' },
        { key: 'family', label: 'Família' },
        { key: 'issued', label: 'Emissão', type: 'date', nowrap: true },
        { key: 'delivery', label: 'Entrega', type: 'date', nowrap: true },
        { key: 'late', label: 'Atraso (dias)', type: 'int' },
        { key: 'kg', label: 'A faturar (kg)', type: 'kg', total: true, strong: true },
        { key: 'val', label: 'A faturar (R$)', type: 'money', total: true },
        { key: 'ready', label: 'Pronto (kg)', type: 'kg', total: true },
    ];
    const orderRow = (fact) => ({
        order: `${fact.o.split('-').pop()}/${fact.s}`,
        customer: customerName(fact.c),
        href: profileUrl(fact.c),
        rep: repName(fact.r),
        unit: unitName(fact.u),
        family: familyShort(fact.f),
        issued: fact.e,
        delivery: fact.d,
        late: fact.late ? daysLate(fact.d) : null,
        kg: fact.k,
        val: fact.val,
        ready: fact.p || null,
        __tone: fact.late ? 'warn' : null,
    });
    const DIMENSIONS = [
        { key: 'f', label: 'Por família', column: 'Família', name: familyLabel },
        { key: 'u', label: 'Por unidade', column: 'Unidade', name: unitName },
        { key: 'm', label: 'Por mês', column: 'Mês', name: (month) => `${MONTHS[month - 1]} de ${year}` },
        { key: 'r', label: 'Por representante', column: 'Representante', name: repName },
    ];

    function notesUrl(state, title) {
        const params = new URLSearchParams();
        params.set('ano', year);
        ['m', 'u', 'f', 'c'].forEach((key) => { const list = picked(state, key); if (list.length) params.set(key, list.join(',')); });
        const reps = picked(state, 'r');
        if (reps.length && !reps.includes('null')) params.set('r', reps.join(','));
        params.set('base', basis);
        if (title) params.set('titulo', String(title).slice(0, 120));
        return `${config.detail_url}?${params.toString()}`;
    }
    const noticeSection = (key, label, text) => ({ key, label, columns: [{ key: 'text', label: 'Aviso', wrap: true }], rows: [{ text }] });

    // Notas fiscais do recorte (servidor): a última camada do "Expandir dados", como no PBIX.
    function withNotes(result, state, options = {}) {
        if (options.notes === false) return result;
        if (data.is_future) {
            result.sections.push(noticeSection('notas', 'Notas fiscais', `${year} ainda não tem notas fiscais.`));
            return result;
        }
        const reps = picked(state, 'r');
        const repNames = reps.includes('null') ? new Set(reps.map(repName)) : null;
        return D.fetchJSON(notesUrl(state, result.title)).then((server) => {
            const section = (server.sections || [])[0];
            if (section) {
                if (repNames) section.rows = (section.rows || []).filter((row) => repNames.has(row.rep));
                if (options.keep) section.rows = (section.rows || []).filter(options.keep);
                const position = options.first === 'notas' ? 0 : result.sections.length;
                result.sections.splice(position, 0, section);
            }
            return result;
        }, (error) => {
            result.sections.push(noticeSection('notas', 'Notas fiscais', `As notas fiscais não vieram do ERP: ${error.message}`));
            return result;
        });
    }

    /* Resultado padrão do "Expandir dados": meta, faturado, carteira e ano anterior do recorte, aberto
       por família, unidade, mês, representante e cliente, mais os itens da carteira e as notas fiscais. */
    function breakdown(state, title, options = {}) {
        const totals = totalOf(state);
        const pace = paceOf(state);
        const expected = expectedOf(pace);
        const goalsAvailable = !picked(state, 'c').length;
        const summary = [
            { label: 'Meta', value: totals.hasGoal ? tons(totals.goalKg) : 'sem meta', note: totals.hasGoal ? money(totals.goalVal) : (goalsAvailable ? 'nada cadastrado para este recorte' : 'o filtro de cliente não tem meta') },
            { label: 'Faturado', value: tons(totals.kg), note: `${money(totals.val)}${totals.hasGoal ? ` · ${pct(totals.billedKg)} da meta` : ''}`, tone: toneOf(totals.billedKg, expected) },
            { label: 'A faturar', value: tons(totals.openKg), note: `${money(totals.openVal)} · ${tons(totals.ready)} pronto` },
            { label: 'Previsão', value: tons(totals.fcKg), note: totals.hasGoal ? `${pct(totals.attKg)} da meta${expected != null && !pace.closed ? ` · esperado ${pct(expected)}` : ''}` : money(totals.fcVal), tone: toneOf(totals.attKg, expected) },
            { label: String(year - 1), value: totals.prevKg ? tons(totals.prevKg) : '–', note: totals.prevKg ? `${signedPct(totals.kg / totals.prevKg - 1)} no faturado` : 'sem base' },
        ];
        const sections = [];
        DIMENSIONS.forEach((dimension) => {
            if (picked(state, dimension.key).length === 1) return;
            const items = [...cube(state, dimension.key).values()].filter(nonEmpty);
            if (!items.length) return;
            const rows = items.map((item) => ({ label: dimension.name(item.key), order: dimension.key === 'm' ? Number(item.key) : null, ...rowFromItem(item), __tone: toneOf(item.attKg, expected) === 'bad' ? 'bad' : null }));
            sections.push({
                key: dimension.key,
                label: dimension.label,
                columns: breakdownColumns({ key: 'label', label: dimension.column }, expected),
                rows: dimension.key === 'm' ? rows.sort((a, b) => a.order - b.order) : rows,
                sort: dimension.key === 'm' ? null : { key: totals.hasGoal ? 'goalKg' : 'kg', dir: 'desc' },
                empty: 'Nada neste recorte.',
            });
        });
        if (picked(state, 'c').length !== 1) {
            const customers = [...cube(state, 'c').values()].filter((item) => item.kg || item.openKg);
            if (customers.length) {
                sections.push({
                    key: 'c', label: 'Por cliente', columns: customerColumns,
                    rows: customers.map((item) => ({ label: customerName(item.key), sub: [item.key, customerPlace(item.key)].filter(Boolean).join(' · '), href: profileUrl(item.key), ...rowFromItem(item) })),
                    sort: { key: 'fcKg', dir: 'desc' }, empty: 'Nenhum cliente neste recorte.',
                });
            }
        }
        const open = orders.filter((fact) => matches(fact, state));
        if (open.length) {
            const section = { key: 'carteira', label: 'Carteira a faturar', columns: orderColumns, rows: open.map(orderRow), sort: { key: 'delivery', dir: 'asc' }, groupBy: ['customer', 'family', 'unit'], empty: 'Nada a faturar.' };
            if (options.first === 'carteira') sections.unshift(section);
            else sections.push(section);
        }
        if (options.first && options.first !== 'carteira' && options.first !== 'notas') {
            const index = sections.findIndex((section) => section.key === options.first);
            if (index > 0) sections.unshift(...sections.splice(index, 1));
        }
        const result = {
            title,
            subtitle: `${options.kind ? `${options.kind} · ` : ''}${describe(state)} · R$ ${BASES[basis]}`,
            summary,
            sections,
        };
        return withNotes(result, state, options);
    }

    // ------------------------------------------------------------------ toolbar: meses, unidades, base e medida
    const chip = (parent, text, title) => {
        const button = h('button', 'mx-chip', text, parent);
        button.type = 'button';
        if (title) button.title = title;
        return button;
    };
    const monthsNode = byId('bgMonths');
    h('span', 'mx-toolbar-label', 'Meses', monthsNode);
    const yearChip = chip(monthsNode, 'Ano', `${year} inteiro`);
    yearChip.addEventListener('click', () => filter.set('m', null));
    let ytdChip = null;
    if (data.is_current && data.current_month > 1) {
        ytdChip = chip(monthsNode, `Até ${SHORT[data.current_month - 1]}`, `De janeiro até ${MONTHS[data.current_month - 1]} (o mês corrente)`);
        ytdChip.addEventListener('click', () => filter.set('m', range(1, data.current_month).map(String)));
    }
    let monthAnchor = null;
    function chooseMonth(month, event) {
        if (event && event.shiftKey && monthAnchor != null) {
            const [from, to] = monthAnchor < month ? [monthAnchor, month] : [month, monthAnchor];
            const list = range(from, to).map(String);
            filter.set('m', L.additive(event) ? [...new Set([...filter.values('m'), ...list])] : list);
            return;
        }
        monthAnchor = month;
        filter.toggle('m', month, event);
    }
    const monthChips = ALL_MONTHS.map((month) => {
        const button = chip(monthsNode, SHORT[month - 1], `${MONTHS[month - 1]} de ${year} · Ctrl + clique soma meses · Shift + clique faz um intervalo`);
        if (data.is_current && month === data.current_month) button.classList.add('bg-now');
        button.addEventListener('click', (event) => chooseMonth(month, event));
        return button;
    });

    const unitsNode = byId('bgUnits');
    h('span', 'mx-toolbar-label', 'Unidade', unitsNode);
    const unitCodes = [...new Set([...data.goals.units.map((fact) => fact.u), ...data.billing.map((fact) => fact.u), ...allOrders.filter((fact) => fact.m != null).map((fact) => fact.u)])].sort((a, b) => a - b);
    const allUnitsChip = chip(unitsNode, 'Todas', 'Todas as unidades de orçamento');
    allUnitsChip.addEventListener('click', () => filter.set('u', null));
    const unitChips = unitCodes.map((code) => {
        const button = chip(unitsNode, unitName(code), `${unitName(code)} · Ctrl + clique soma unidades`);
        button.addEventListener('click', (event) => filter.toggle('u', code, event));
        return [code, button];
    });

    const basisChips = [...document.querySelectorAll('[data-bg-basis]')];
    basisChips.forEach((button) => button.addEventListener('click', () => {
        if (button.dataset.bgBasis === basis) return;
        basis = button.dataset.bgBasis;
        store.set(basisKey, basis);
        rebase();
        render();
    }));
    const measureChips = [...document.querySelectorAll('[data-bg-measure]')];
    measureChips.forEach((button) => button.addEventListener('click', () => {
        if (button.dataset.bgMeasure === measure) return;
        measure = button.dataset.bgMeasure === 'value' ? 'value' : 'kg';
        store.set('bgMeasure', measure);
        render();
    }));

    function paintToolbar(state) {
        const months = monthsOf(state);
        const pressed = (button, on) => { button.classList.toggle('is-active', on); button.setAttribute('aria-pressed', on ? 'true' : 'false'); };
        pressed(yearChip, !months.length);
        if (ytdChip) pressed(ytdChip, months.length === data.current_month && months.every((month) => month <= data.current_month));
        monthChips.forEach((button, index) => pressed(button, months.includes(index + 1)));
        const units = picked(state, 'u');
        pressed(allUnitsChip, !units.length);
        unitChips.forEach(([code, button]) => pressed(button, units.includes(String(code))));
        basisChips.forEach((button) => pressed(button, button.dataset.bgBasis === basis));
        measureChips.forEach((button) => pressed(button, button.dataset.bgMeasure === measure));
    }

    const SOURCE_UNITS = {
        erp: 'USU_TORCMET (tela Metas de Vendas)',
        orcamento: `orçamento importado (PBIX "2. ORÇAMENTO 2026" / projeção), porque ${year} ainda não está na USU_TORCMET`,
        nenhuma: 'nenhuma cadastrada',
    };
    const SOURCE_REPS = {
        erp: 'USU_TORCGER (tela Metas de Vendas)',
        pbix: 'as metas por representante do PBIX',
        nenhuma: 'nenhuma cadastrada',
    };
    function paintSource() {
        const parts = [
            `Meta por unidade: ${SOURCE_UNITS[data.sources.units] || data.sources.units}.`,
            `Meta por representante: ${SOURCE_REPS[data.sources.reps] || data.sources.reps}.`,
            `Meta em R$ ${BASES[data.basis]}${basis !== data.basis ? `, convertida para ${BASES[basis]} pela proporção de impostos faturada em cada unidade e família` : ''}.`,
        ];
        if (data.is_future) parts.push(`${year} ainda não começou: o painel mostra a meta e a carteira com entrega no ano.`);
        else if (!data.is_current) parts.push(`${year} encerrado: sem carteira a faturar.`);
        byId('bgSourceText').textContent = parts.join(' ');
        byId('bgSource').hidden = false;
    }

    // ------------------------------------------------------------------ indicadores
    const kpiNodes = (id) => {
        const card = byId(id);
        return { card, value: card.querySelector('[data-bg-value]'), delta: card.querySelector('[data-bg-delta]'), foot: card.querySelector('[data-bg-foot]') };
    };
    const kpis = { kg: kpiNodes('kpiKg'), value: kpiNodes('kpiValue'), forecast: kpiNodes('kpiForecast'), backlog: kpiNodes('kpiBacklog'), late: kpiNodes('kpiLate'), pace: kpiNodes('kpiPace') };
    function setDelta(node, text, tone) {
        if (!node) return;
        node.textContent = text;
        node.classList.remove('is-up', 'is-down', 'is-neutral');
        node.classList.add(tone === 'good' ? 'is-up' : tone === 'bad' ? 'is-down' : 'is-neutral');
    }
    const setValue = (node, value, format) => {
        if (!finite(value)) { node.textContent = '–'; return; }
        L.count(node, value, format);
    };
    const lateOf = (state) => lateOrders.filter((fact) => matches(fact, without(state, 'm')));

    function renderKpis(state, totals, pace) {
        const expected = expectedOf(pace);
        setValue(kpis.kg.value, totals.kg, tons);
        kpis.kg.value.title = kgFull(totals.kg);
        setDelta(kpis.kg.delta, totals.hasGoal ? pct(totals.billedKg) : '–', toneOf(totals.billedKg, expected));
        kpis.kg.foot.textContent = totals.hasGoal ? `da meta de ${tons(totals.goalKg)}` : 'sem meta para este filtro';

        setValue(kpis.value.value, totals.val, money);
        kpis.value.value.title = moneyFull(totals.val);
        setDelta(kpis.value.delta, totals.hasGoal ? pct(totals.billedVal) : '–', toneOf(totals.billedVal, expected));
        kpis.value.foot.textContent = totals.hasGoal ? `da meta de ${money(totals.goalVal)} · ${BASES[basis]}` : BASES[basis];

        setValue(kpis.forecast.value, totals.fcKg, tons);
        kpis.forecast.value.title = kgFull(totals.fcKg);
        setDelta(kpis.forecast.delta, totals.hasGoal ? pct(totals.attKg) : '–', toneOf(totals.attKg, expected));
        kpis.forecast.foot.textContent = `${totals.hasGoal ? 'da meta · ' : ''}${money(totals.fcVal)} (faturado + a faturar)`;

        if (!hasOrders) {
            kpis.backlog.value.textContent = '–';
            kpis.backlog.foot.textContent = 'ano encerrado: sem carteira';
        } else {
            setValue(kpis.backlog.value, totals.openKg, tons);
            kpis.backlog.value.title = kgFull(totals.openKg);
            kpis.backlog.foot.textContent = `${money(totals.openVal)} · ${tons(totals.ready)} pronto em estoque (${pct(ratio(totals.ready, totals.openKg))})`;
        }

        if (!data.is_current) {
            kpis.late.value.textContent = '–';
            kpis.late.foot.textContent = 'só no ano corrente';
        } else {
            const late = lateOf(state);
            const month = late.filter(lateThisMonth);
            const older = late.filter((fact) => !lateThisMonth(fact));
            const sumKg = (list) => list.reduce((acc, fact) => acc + (fact.k || 0), 0);
            const kg = sumKg(month);
            setValue(kpis.late.value, kg, tons);
            kpis.late.value.title = `${kgFull(kg)} com entrega vencida neste mês`;
            const parts = [];
            if (month.length) parts.push(`${money(month.reduce((acc, fact) => acc + fact.val, 0))} · ${N0.format(month.length)} ite${month.length === 1 ? 'm' : 'ns'} no mês`);
            else parts.push('nada vencido neste mês');
            if (older.length) parts.push(`+${tons(sumKg(older))} de meses anteriores (${N0.format(older.length)})`);
            kpis.late.foot.textContent = parts.join(' · ');
        }

        const paceNode = kpis.pace;
        if (!totals.hasGoal) {
            paceNode.value.textContent = '–';
            paceNode.foot.textContent = 'sem meta para este filtro';
        } else if (pace.closed) {
            paceNode.value.textContent = pct(totals.billedKg);
            paceNode.foot.textContent = `da meta faturado · período encerrado (${signed(totals.kg - totals.goalKg, tons)})`;
        } else if (pace.future) {
            paceNode.value.textContent = `${tons(totals.goalKg / (pace.total || 1))}/dia`;
            paceNode.foot.textContent = `meta média por dia útil · ${N0.format(pace.total)} dias úteis no período`;
        } else {
            const needed = Math.max(totals.goalKg - totals.fcKg, 0);
            const current = pace.elapsed ? totals.kg / pace.elapsed : null;
            if (!needed) {
                paceNode.value.textContent = 'Meta coberta';
                paceNode.foot.textContent = `faturado + carteira já passam da meta (${signed(totals.fcKg - totals.goalKg, tons)})`;
            } else {
                paceNode.value.textContent = `${tons(needed / Math.max(pace.remaining, 1))}/dia`;
                paceNode.foot.textContent = `além da carteira, em ${N0.format(pace.remaining)} dias úteis · hoje ${current ? `${tons(current)}/dia` : '–'}`;
            }
        }
    }

    D.kpi(kpis.kg.card, { drill: () => breakdown(filter.get(), 'Volume faturado', { first: 'notas', kind: 'Faturado e meta' }) });
    D.kpi(kpis.value.card, { drill: () => breakdown(filter.get(), 'Valor faturado', { first: 'notas', kind: 'Faturado e meta em R$' }) });
    D.kpi(kpis.forecast.card, { drill: () => breakdown(filter.get(), 'Previsão de faturamento', { first: 'f', kind: 'Faturado + a faturar' }) });
    D.kpi(kpis.backlog.card, { drill: () => breakdown(filter.get(), 'A faturar (carteira)', { first: 'carteira', notes: false, kind: 'Itens em aberto' }), canDrill: () => orders.length > 0 });
    D.kpi(kpis.late.card, { drill: () => lateResult(filter.get()), canDrill: () => lateOrders.length > 0 });
    D.kpi(kpis.pace.card, { drill: () => paceResult(filter.get()) });

    function lateResult(state) {
        const late = lateOf(state);
        const byCustomer = new Map();
        late.forEach((fact) => {
            const entry = byCustomer.get(fact.c) || { label: customerName(fact.c), href: profileUrl(fact.c), sub: repName(fact.r), kg: 0, val: 0, ready: 0, items: 0, oldest: fact.d };
            entry.kg += fact.k || 0;
            entry.val += fact.val;
            entry.ready += fact.p || 0;
            entry.items += 1;
            if (fact.d && (!entry.oldest || fact.d < entry.oldest)) entry.oldest = fact.d;
            byCustomer.set(fact.c, entry);
        });
        const sumKg = (list) => list.reduce((acc, fact) => acc + (fact.k || 0), 0);
        const month = late.filter(lateThisMonth);
        const older = late.filter((fact) => !lateThisMonth(fact));
        const oldest = older.reduce((acc, fact) => (fact.d && (!acc || fact.d < acc) ? fact.d : acc), null);
        const lateColumns = [...orderColumns.slice(0, 6), { key: 'dueMonth', label: 'Mês da entrega', nowrap: true }, ...orderColumns.slice(6)];
        return {
            title: 'Pedidos em atraso',
            subtitle: `Itens em aberto com entrega antes de hoje · o card "Atraso no mês" conta, como o PBIX, só os de entrega neste mês · ${describe(without(state, 'm'))}`,
            summary: [
                { label: 'Atraso no mês', value: tons(sumKg(month)), note: money(month.reduce((acc, fact) => acc + fact.val, 0)), tone: month.length ? 'bad' : 'good' },
                { label: 'Meses anteriores', value: tons(sumKg(older)), note: older.length ? `${N0.format(older.length)} ite${older.length === 1 ? 'm' : 'ns'} · o mais antigo de ${dateText(oldest)}` : 'nenhum', tone: older.length ? 'warn' : null },
                { label: 'Pedidos', value: N0.format(new Set(late.map((fact) => fact.o)).size), note: `${N0.format(late.length)} ite${late.length === 1 ? 'm' : 'ns'}` },
                { label: 'Pronto em estoque', value: tons(late.reduce((acc, fact) => acc + (fact.p || 0), 0)), note: 'já dá para faturar' },
                { label: 'Clientes', value: N0.format(byCustomer.size) },
            ],
            sections: [
                { key: 'itens', label: 'Itens', columns: lateColumns, rows: late.map((fact) => ({ ...orderRow(fact), dueMonth: fact.d ? `${fact.d.slice(5, 7)}/${fact.d.slice(0, 4)}` : '–' })), sort: { key: 'delivery', dir: 'asc' }, groupBy: ['dueMonth', 'customer', 'family', 'unit'], empty: 'Nenhum item em atraso.' },
                {
                    key: 'clientes', label: 'Por cliente',
                    columns: [
                        { key: 'label', label: 'Cliente', href: 'href', sub: 'sub' },
                        { key: 'oldest', label: 'Entrega mais antiga', type: 'date', nowrap: true },
                        { key: 'items', label: 'Itens', type: 'int', total: true },
                        { key: 'kg', label: 'Em atraso (kg)', type: 'kg', total: true, strong: true },
                        { key: 'val', label: 'Em atraso (R$)', type: 'money', total: true },
                        { key: 'ready', label: 'Pronto (kg)', type: 'kg', total: true },
                    ],
                    rows: [...byCustomer.values()], sort: { key: 'kg', dir: 'desc' }, empty: 'Nenhum cliente com atraso.',
                },
            ],
        };
    }

    function paceResult(state) {
        const scope = without(state, 'm');
        const map = cube(scope, 'm', { allMonths: true });
        const list = monthsOf(state).length ? monthsOf(state) : ALL_MONTHS;
        const rows = list.map((month) => {
            const item = map.get(month) || finish(blank(month));
            const days = business.get(month) || { total: 0, elapsed: 0 };
            const remaining = Math.max(days.total - days.elapsed, 0);
            return {
                month: `${MONTHS[month - 1]} de ${year}`, order: month, total: days.total, elapsed: days.elapsed, remaining,
                goalKg: item.hasGoal ? item.goalKg : null, kg: item.kg, fcKg: item.fcKg,
                perDay: days.elapsed ? item.kg / days.elapsed : null,
                goalPerDay: item.hasGoal && days.total ? item.goalKg / days.total : null,
                needed: item.hasGoal && remaining ? Math.max(item.goalKg - item.fcKg, 0) / remaining : null,
                __tone: item.hasGoal && days.elapsed && days.total ? (toneOf(ratio(item.fcKg, item.goalKg), days.elapsed / days.total) === 'bad' ? 'bad' : null) : null,
            };
        });
        const totals = totalOf(state);
        const pace = paceOf(state);
        return {
            title: 'Ritmo para bater a meta',
            subtitle: `Dias úteis (segunda a sexta), faturado por dia e o que falta por dia · ${describe(state)}`,
            summary: [
                { label: 'Dias úteis', value: N0.format(pace.total), note: `${N0.format(pace.elapsed)} passados, ${N0.format(pace.remaining)} restantes` },
                { label: 'Ritmo esperado', value: pct(pace.ratio), note: 'dias passados ÷ dias do período' },
                { label: 'Faturado por dia', value: pace.elapsed ? tons(totals.kg / pace.elapsed) : '–' },
                { label: 'Falta por dia', value: pace.remaining && totals.hasGoal ? tons(Math.max(totals.goalKg - totals.fcKg, 0) / pace.remaining) : '–', note: 'além da carteira' },
            ],
            sections: [{
                key: 'meses', label: 'Por mês',
                columns: [
                    { key: 'month', label: 'Mês' },
                    { key: 'total', label: 'Dias úteis', type: 'int', total: true },
                    { key: 'elapsed', label: 'Passados', type: 'int', total: true },
                    { key: 'remaining', label: 'Restantes', type: 'int', total: true },
                    { key: 'goalKg', label: 'Meta (kg)', type: 'kg', total: true },
                    { key: 'kg', label: 'Faturado (kg)', type: 'kg', total: true, strong: true },
                    { key: 'fcKg', label: 'Previsão (kg)', type: 'kg', total: true },
                    { key: 'goalPerDay', label: 'Meta por dia útil (kg)', type: 'kg' },
                    { key: 'perDay', label: 'Faturado por dia útil (kg)', type: 'kg' },
                    { key: 'needed', label: 'Falta por dia útil (kg)', type: 'kg' },
                ],
                rows, empty: 'Sem meses.',
            }],
        };
    }

    // ------------------------------------------------------------------ medidores
    const valueDrill = () => breakdown(filter.get(), 'Valor faturado × meta', { first: 'f', kind: 'Faturado e meta em R$' });
    const volumeDrill = () => breakdown(filter.get(), 'Volume faturado × meta', { first: 'f', kind: 'Faturado e meta em kg' });
    const gaugeValue = L.gauge(byId('gaugeValue'), { label: 'Valor faturado ÷ meta em R$', pointLabel: 'Valor faturado × meta', good: 0.98, warn: 0.9, drill: valueDrill });
    const gaugeVolume = L.gauge(byId('gaugeVolume'), { label: 'Volume faturado ÷ meta em kg', pointLabel: 'Volume faturado × meta', good: 0.98, warn: 0.9, drill: volumeDrill });
    function setFact(id, text, tone) {
        const node = byId(id);
        node.textContent = text;
        node.classList.remove('is-up', 'is-down');
        if (tone) node.classList.add(tone === 'good' ? 'is-up' : 'is-down');
    }
    function renderGauges(state, totals, pace) {
        const expected = expectedOf(pace);
        const running = !pace.closed && !pace.future;
        const be = breakEvenFor(state);
        const valueRatio = totals.hasGoal ? ratio(totals.val, totals.goalVal) : null;
        const valueMarker = be && totals.goalVal ? be / totals.goalVal : running ? pace.ratio : null;
        gaugeValue.update(valueRatio, totals.hasGoal ? `${money(totals.val)} de ${money(totals.goalVal)}` : 'sem meta para este filtro', {
            target: valueMarker, toneRatio: valueRatio == null ? null : expected ? valueRatio / expected : 1,
        });
        byId('gaugeValueSub').textContent = `Faturado ÷ meta em R$ (${BASES[basis]})${be ? ' · marca = ponto de equilíbrio' : running ? ` · marca = ritmo esperado (${pct(pace.ratio)})` : ''}`;
        const valueGap = totals.hasGoal ? totals.val - totals.goalVal : null;
        setFact('factValuePct', totals.hasGoal ? signedPct(ratio(valueGap, totals.goalVal)) : '–', valueGap == null ? null : valueGap >= 0 ? 'good' : 'bad');
        setFact('factValueDiff', signed(valueGap, moneyFull), valueGap == null ? null : valueGap >= 0 ? 'good' : 'bad');
        setFact('factValueGoal', totals.hasGoal ? moneyFull(totals.goalVal) : '–');
        setFact('factBreakEven', be ? `${moneyFull(be)} · ${pct(ratio(totals.val, be))} coberto` : `não cadastrado para ${year}`, be ? (totals.val >= be ? 'good' : 'bad') : null);

        const kgRatio = totals.hasGoal ? ratio(totals.kg, totals.goalKg) : null;
        gaugeVolume.update(kgRatio, totals.hasGoal ? `${tons(totals.kg)} de ${tons(totals.goalKg)}` : 'sem meta para este filtro', {
            target: running ? pace.ratio : null, toneRatio: kgRatio == null ? null : expected ? kgRatio / expected : 1,
        });
        byId('gaugeVolumeSub').textContent = `Faturado ÷ meta em kg${running ? ` · marca = ritmo esperado (${pct(pace.ratio)} dos dias úteis)` : ''}`;
        const kgGap = totals.hasGoal ? totals.kg - totals.goalKg : null;
        setFact('factKgPct', totals.hasGoal ? signedPct(ratio(kgGap, totals.goalKg)) : '–', kgGap == null ? null : kgGap >= 0 ? 'good' : 'bad');
        setFact('factKgDiff', signed(kgGap, kgFull), kgGap == null ? null : kgGap >= 0 ? 'good' : 'bad');
        setFact('factKgGoal', totals.hasGoal ? kgFull(totals.goalKg) : '–');
        const change = totals.prevKg ? totals.kg / totals.prevKg - 1 : null;
        setFact('factKgPrevious', change == null ? 'sem base' : `${signedPct(change)} (${tons(totals.prevKg)} em ${year - 1})`, change == null ? null : change >= 0 ? 'good' : 'bad');
    }

    // ------------------------------------------------------------------ famílias (colunas)
    const pickOf = (item, kgKey, valueKey) => (isKg() ? item[kgKey] : item[valueKey]);
    const familyOptions = {
        height: 300, labelName: 'Família', valueType: 'kg', format: showMeasure,
        onSelect: (key, index, event) => filter.toggle('f', key, event),
        drill: ({ key }) => breakdown(only(filter.get(), 'f', key), familyLabel(key), { first: 'u', kind: 'Família da meta' }),
    };
    const familyChart = L.columns(byId('chartFamilies'), familyOptions);
    function renderFamilies(state, expected) {
        const scope = without(state, 'f');
        const items = [...cube(scope, 'f').values()].filter(nonEmpty).sort((a, b) => b.goalKg - a.goalKg || b.fcKg - a.fcKg);
        const chosen = picked(state, 'f');
        familyOptions.valueType = isKg() ? 'kg' : 'money';
        const hasGoals = items.some((item) => item.hasGoal);
        familyChart.update({
            labels: items.map((item) => familyShort(item.key)),
            keys: items.map((item) => item.key),
            series: [
                { name: 'Faturado', values: items.map((item) => pickOf(item, 'kg', 'val')), color: '--mx-chart-1' },
                { name: 'A faturar', values: items.map((item) => pickOf(item, 'openKg', 'openVal')), color: '--mx-cat-4' },
                { name: 'Pronto em estoque', values: items.map((item) => pickOf(item, 'ready', 'readyVal')), color: '--mx-cat-7' },
                { name: 'Previsão', values: items.map((item) => pickOf(item, 'fcKg', 'fcVal')), color: '--mx-cat-3' },
            ],
            target: hasGoals ? { name: 'Meta', values: items.map((item) => (item.hasGoal ? pickOf(item, 'goalKg', 'goalVal') : null)) } : undefined,
            highlight: chosen.length ? items.map((item, index) => (chosen.includes(String(item.key)) ? index : null)).filter((index) => index != null) : null,
            extra: (index) => {
                const item = items[index];
                const att = isKg() ? item.attKg : item.attVal;
                const gap = item.hasGoal ? pickOf(item, 'fcKg', 'fcVal') - pickOf(item, 'goalKg', 'goalVal') : null;
                const lines = [familyLabel(item.key)];
                if (item.hasGoal) {
                    const tone = toneOf(att, expected);
                    lines.push(`% meta: ${pct(Math.min(att || 0, 1))}${att > 1 ? ` (previsão em ${pct(att)})` : ''}${tone === 'bad' ? ' · abaixo do esperado' : ''}`,
                        gap >= 0 ? `Acima da meta: ${showMeasure(gap)}` : `Falta: ${showMeasure(-gap)}`);
                }
                else lines.push('Sem meta no orçamento');
                if (item.prevKg) lines.push(`${year - 1} (mesmos meses): ${showMeasure(pickOf(item, 'prevKg', 'prevVal'))}`);
                return lines;
            },
        });
    }

    // ------------------------------------------------------------------ mês a mês (colunas empilhadas)
    const monthOptions = {
        height: 260, stacked: true, labelName: 'Mês', valueType: 'kg', format: showMeasure,
        onSelect: (key, index, event) => chooseMonth(Number(key), event),
        drill: ({ key }) => breakdown(only(filter.get(), 'm', key), `${MONTHS[key - 1]} de ${year}`, { first: 'f', kind: 'Mês' }),
        canDrill: ({ index, data: chart }) => chart.series.some((series) => series.values[index]) || !!(chart.target && chart.target.values[index]),
    };
    const monthChart = L.columns(byId('chartMonths'), monthOptions);
    function renderMonths(state) {
        const scope = without(state, 'm');
        const map = cube(scope, 'm', { allMonths: true });
        const items = ALL_MONTHS.map((month) => map.get(month) || finish(blank(month)));
        const chosen = monthsOf(state);
        monthOptions.valueType = isKg() ? 'kg' : 'money';
        const hasGoals = items.some((item) => item.hasGoal);
        monthChart.update({
            labels: SHORT.slice(),
            keys: ALL_MONTHS.slice(),
            series: [
                { name: 'Faturado', values: items.map((item) => pickOf(item, 'kg', 'val')), color: '--mx-chart-1' },
                { name: 'A faturar', values: items.map((item) => pickOf(item, 'openKg', 'openVal')), color: '--mx-cat-4' },
            ],
            target: hasGoals ? { name: 'Meta', values: items.map((item) => (item.hasGoal ? pickOf(item, 'goalKg', 'goalVal') : null)) } : undefined,
            highlight: chosen.length ? chosen.map((month) => month - 1) : null,
            extra: (index) => {
                const item = items[index];
                const fc = pickOf(item, 'fcKg', 'fcVal');
                const prev = pickOf(item, 'prevKg', 'prevVal');
                const att = isKg() ? item.attKg : item.attVal;
                const lines = [`Previsão: ${showMeasure(fc)}${item.hasGoal ? ` · ${pct(att)} da meta` : ''}`];
                if (item.lateKg && isKg()) lines.push(`Inclui ${tons(item.lateKg)} em atraso`);
                lines.push(`${SHORT[index]}/${String(year - 1).slice(2)}: ${prev ? `${showMeasure(prev)} (${signedPct(ratio(pickOf(item, 'kg', 'val'), prev) - 1)} no faturado)` : 'sem faturamento'}`);
                return lines;
            },
        });
        const goal = items.reduce((acc, item) => acc + pickOf(item, 'goalKg', 'goalVal'), 0);
        byId('monthsNote').textContent = hasGoals ? `Meta ${year}: ${showMeasure(goal)}${data.is_current ? ' · atrasados contam no mês corrente' : ''}` : '';
    }

    // ------------------------------------------------------------------ acumulado no ano (linhas)
    const cumulativeOptions = {
        height: 240, labelName: 'Mês', valueType: 'kg', format: showMeasure,
        onSelect: (key) => filter.set('m', range(1, Number(key)).map(String)),
        drill: ({ key }) => breakdown({ ...filter.get(), m: range(1, Number(key)).map(String) }, `Acumulado até ${MONTHS[key - 1]}`, { first: 'm', kind: 'Acumulado no ano' }),
    };
    const cumulativeChart = L.line(byId('chartCumulative'), cumulativeOptions);
    function renderCumulative(state) {
        const scope = without(state, 'm');
        const map = cube(scope, 'm', { allMonths: true });
        cumulativeOptions.valueType = isKg() ? 'kg' : 'money';
        const lastBilled = data.is_future ? 0 : data.is_current ? data.current_month : 12;
        let goal = 0;
        let billed = 0;
        let forecast = 0;
        let before = 0;
        const series = { goal: [], billed: [], forecast: [], before: [] };
        let hasGoals = false;
        ALL_MONTHS.forEach((month) => {
            const item = map.get(month) || finish(blank(month));
            hasGoals = hasGoals || item.hasGoal;
            goal += pickOf(item, 'goalKg', 'goalVal');
            billed += pickOf(item, 'kg', 'val');
            forecast += pickOf(item, 'fcKg', 'fcVal');
            before += pickOf(item, 'prevKg', 'prevVal');
            series.goal.push(goal);
            series.billed.push(month <= lastBilled ? billed : null);
            series.forecast.push(month >= Math.max(lastBilled, 1) ? forecast : null);
            series.before.push(before);
        });
        const lines = [];
        if (hasGoals) lines.push({ name: 'Meta acumulada', values: series.goal, color: '--mx-text', dashed: true, markers: false });
        lines.push({ name: 'Faturado acumulado', values: series.billed, color: '--mx-chart-1' });
        if (hasOrders) lines.push({ name: 'Previsão acumulada', values: series.forecast, color: '--mx-cat-3', dashed: true });
        lines.push({ name: `${year - 1}`, values: series.before, color: '--mx-faint', markers: false });
        cumulativeChart.update({
            labels: SHORT.slice(),
            keys: ALL_MONTHS.slice(),
            series: lines,
            marked: monthsOf(state).map((month) => month - 1),
            extra: (index) => {
                const goalNow = series.goal[index];
                const done = series.billed[index] != null ? series.billed[index] : series.forecast[index];
                return hasGoals && goalNow ? [`Contra a meta acumulada: ${pct(ratio(done, goalNow))}`] : [];
            },
        });
    }

    // ------------------------------------------------------------------ representantes (tabela e ranking)
    const repsNode = byId('tableReps');
    let repItems = [];
    function repCells(item, expected) {
        const goal = pickOf(item, 'goalKg', 'goalVal');
        const done = pickOf(item, 'kg', 'val');
        const fc = pickOf(item, 'fcKg', 'fcVal');
        const att = item.hasGoal && goal ? fc / goal : null;
        const tone = toneOf(att, expected);
        return {
            att, tone,
            cells: [
                { text: item.key === 'total' ? 'Total' : repName(item.key), sub: item.key !== 'total' && !item.hasGoal ? 'sem meta' : null },
                item.hasGoal ? showMeasure(goal) : '–',
                showMeasure(done),
                showMeasure(pickOf(item, 'openKg', 'openVal')),
                { text: showMeasure(fc), strong: true },
                att == null ? '–' : { text: pct(att), bar: att, tone },
                item.hasGoal ? (goal - fc > 0 ? showMeasure(goal - fc) : 'batida') : '–',
            ],
        };
    }
    function renderReps(state, expected) {
        const scope = without(state, 'r');
        const chosen = picked(state, 'r');
        repItems = [...cube(scope, 'r').values()].filter(nonEmpty).sort((a, b) => b.goalKg - a.goalKg || b.fcKg - a.fcKg);
        const columns = [
            { label: 'Representante' }, { label: 'Meta', num: true }, { label: 'Faturado', num: true }, { label: 'A faturar', num: true },
            { label: isKg() ? 'Peso' : 'Valor', num: true }, { label: '% atingido', num: true }, { label: 'Falta', num: true },
        ];
        const rows = repItems.map((item) => {
            const view = repCells(item, expected);
            return { cells: view.cells, selected: chosen.includes(String(item.key)), className: view.tone === 'bad' ? 'bg-row-bad' : null };
        });
        const total = repCells(sumItems(repItems), expected);
        drawTable(repsNode, columns, rows, { clickable: true, footer: total.cells, empty: 'Nenhum representante com meta ou venda neste filtro.', className: 'bg-table-reps' });
    }
    onRow(repsNode, (index, event) => { const item = repItems[index]; if (item) filter.toggle('r', item.key, event); });
    const repDrill = (key) => breakdown(only(filter.get(), 'r', key), repName(key), { first: 'f', kind: 'Representante' });
    D.attach(repsNode, {
        label: (index) => (repItems[index] ? repName(repItems[index].key) : ''),
        table: () => ({
            columns: breakdownColumns({ key: 'label', label: 'Representante' }),
            rows: repItems.map((item, index) => ({ label: repName(item.key), ...rowFromItem(item), __index: index })),
        }),
        drill: (index) => repDrill(repItems[index].key),
        canDrill: (index) => !!repItems[index],
    });

    const rankOptions = {
        format: (value) => pct(value), limit: 15, labelName: 'Representante', valueName: 'Previsão ÷ meta', valueType: 'pct',
        emptyText: 'Nenhum representante com meta neste filtro.',
        onSelect: (key, item, event) => filter.toggle('r', key, event),
        drill: ({ key }) => repDrill(key),
    };
    const rank = L.ranking(byId('rankReps'), rankOptions);
    function renderRanking(state, expected) {
        const items = repItems.filter((item) => item.hasGoal && pickOf(item, 'goalKg', 'goalVal') > 0).map((item) => {
            const att = isKg() ? item.attKg : item.attVal;
            const tone = toneOf(att, expected);
            return {
                key: item.key, label: repName(item.key), value: att,
                sub: `${showMeasure(pickOf(item, 'fcKg', 'fcVal'))} de ${showMeasure(pickOf(item, 'goalKg', 'goalVal'))}${tone === 'bad' ? ' · abaixo do esperado' : ''}`,
                color: TONE_COLOR[tone] || '--mx-chart-1',
            };
        }).sort((a, b) => b.value - a.value);
        rank.update(items, picked(state, 'r'));
    }

    // ------------------------------------------------------------------ família × mês (calor)
    const heatNode = byId('heatFamilies');
    let heatCells = [];
    let heatFamilies = [];
    const heatClass = (value) => (value < 0.75 ? 'is-div-neg-2' : value < 0.95 ? 'is-div-neg-1' : value < 1.05 ? 'is-div-mid' : value < 1.2 ? 'is-div-pos-1' : 'is-div-pos-2');
    function renderHeat(state) {
        const scope = without(state, ['f', 'm']);
        const pairs = cube(scope, (fact) => `${fact.f}|${fact.m}`, { allMonths: true });
        const families = [...cube(scope, 'f', { allMonths: true }).values()].filter((item) => item.hasGoal).sort((a, b) => b.goalKg - a.goalKg);
        const months = [...cube(scope, 'm', { allMonths: true }).values()];
        const monthTotal = new Map(months.map((item) => [Number(item.key), item]));
        const chosenFamilies = picked(state, 'f');
        const chosenMonths = monthsOf(state);
        const lastClosed = data.is_future ? 0 : data.is_current ? data.current_month - 1 : 12;
        heatCells = [];
        heatFamilies = families;
        heatNode.replaceChildren();
        if (!families.length) {
            h('p', 'mx-empty-note', 'Sem meta por família neste filtro.', heatNode);
            return;
        }
        const table = h('table', `mx-heat bg-heat${chosenFamilies.length || chosenMonths.length ? ' has-selection' : ''}`, null, heatNode);
        const head = h('tr', null, null, h('thead', null, null, table));
        h('th', 'is-row', 'Família', head);
        ALL_MONTHS.forEach((month) => {
            const th = h('th', `bg-heat-col${chosenMonths.includes(month) ? ' is-selected' : ''}${data.is_current && month === data.current_month ? ' bg-now' : ''}`, SHORT[month - 1], head);
            th.dataset.month = month;
            th.title = `${MONTHS[month - 1]} · clique filtra o mês`;
        });
        h('th', null, 'Ano', head);
        const cell = (parent, item, family, month) => {
            const att = item ? (isKg() ? item.attKg : item.attVal) : null;
            const index = heatCells.push({ family, month, item }) - 1;
            const future = month != null && month > lastClosed + (data.is_current ? 1 : 0);
            const td = h('td', null, att == null ? (item && (item.kg || item.openKg) ? 's/ meta' : '·') : pct(att), parent);
            td.setAttribute('data-mx-index', index);
            if (att != null) td.classList.add(future ? 'is-partial' : heatClass(att));
            const familyOk = chosenFamilies.length ? family != null && chosenFamilies.includes(String(family)) : true;
            const monthOk = chosenMonths.length ? month != null && chosenMonths.includes(month) : true;
            if ((chosenFamilies.length || chosenMonths.length) && familyOk && monthOk) td.classList.add('is-selected');
            const label = `${family != null ? familyShort(family) : 'Todas as famílias'} · ${month != null ? MONTHS[month - 1] : year}`;
            td.title = item ? `${label}\nMeta: ${showMeasure(pickOf(item, 'goalKg', 'goalVal'))} · Previsão: ${showMeasure(pickOf(item, 'fcKg', 'fcVal'))}${future ? '\nMês ainda por vir: só a carteira' : ''}\n${D.hint}` : label;
            return td;
        };
        const body = h('tbody', null, null, table);
        families.forEach((family) => {
            const tr = h('tr', chosenFamilies.includes(String(family.key)) ? 'is-selected' : null, null, body);
            const th = h('th', 'is-row bg-heat-row', familyShort(family.key), tr);
            th.dataset.family = family.key;
            th.title = `${familyLabel(family.key)} · clique filtra a família`;
            ALL_MONTHS.forEach((month) => cell(tr, pairs.get(`${family.key}|${month}`), family.key, month));
            cell(tr, family, family.key, null);
        });
        const foot = h('tr', 'bg-heat-total', null, h('tfoot', null, null, table));
        h('th', 'is-row', 'Total', foot);
        ALL_MONTHS.forEach((month) => cell(foot, monthTotal.get(month), null, month));
        cell(foot, sumItems(months), null, null);
    }
    heatNode.addEventListener('click', (event) => {
        const head = event.target.closest('th[data-month], th[data-family]');
        if (head) {
            if (head.dataset.month) chooseMonth(Number(head.dataset.month), event);
            else filter.toggle('f', head.dataset.family, event);
            return;
        }
        const td = event.target.closest('td[data-mx-index]');
        if (!td) return;
        const target = heatCells[Number(td.getAttribute('data-mx-index'))];
        if (!target) return;
        const state = filter.get();
        const sameFamily = target.family == null || (picked(state, 'f').length === 1 && picked(state, 'f')[0] === String(target.family));
        const sameMonth = target.month == null || (monthsOf(state).length === 1 && monthsOf(state)[0] === target.month);
        if (target.family != null) filter.set('f', sameFamily && sameMonth ? null : target.family);
        if (target.month != null) filter.set('m', sameFamily && sameMonth ? null : target.month);
    });
    D.attach(heatNode, {
        label: (index) => {
            const target = heatCells[index];
            if (!target) return '';
            return `${target.family != null ? familyShort(target.family) : 'Todas as famílias'} · ${target.month != null ? MONTHS[target.month - 1] : year}`;
        },
        table: () => {
            const rowsOf = (item, family, label) => {
                const row = { label, __index: heatCells.findIndex((target) => target.family === family && target.month == null) };
                ALL_MONTHS.forEach((month) => {
                    const target = heatCells.find((entry) => entry.family === family && entry.month === month);
                    row[`m${month}`] = target && target.item ? (isKg() ? target.item.attKg : target.item.attVal) : null;
                });
                row.year = item ? (isKg() ? item.attKg : item.attVal) : null;
                return row;
            };
            return {
                columns: [{ key: 'label', label: 'Família' }, ...ALL_MONTHS.map((month) => ({ key: `m${month}`, label: SHORT[month - 1], type: 'pct', digits: 0 })), { key: 'year', label: 'Ano', type: 'pct', digits: 0 }],
                rows: heatFamilies.map((family) => rowsOf(family, family.key, familyLabel(family.key))),
            };
        },
        drill: (index) => {
            const target = heatCells[index];
            let state = filter.get();
            if (target.family != null) state = only(state, 'f', target.family);
            if (target.month != null) state = only(state, 'm', target.month);
            const title = `${target.family != null ? familyShort(target.family) : 'Todas as famílias'} · ${target.month != null ? `${MONTHS[target.month - 1]} de ${year}` : year}`;
            return breakdown(state, title, { first: target.family != null ? 'u' : 'f', kind: 'Atingimento por família e mês' });
        },
        canDrill: (index) => !!heatCells[index] && !!heatCells[index].item,
    });

    // ------------------------------------------------------------------ previsão de faturamento (unidade → representante)
    const forecastNode = byId('tableForecast');
    const expandedUnits = new Set();
    let forecastRows = [];
    function forecastCells(item, label, extra = {}) {
        return [
            { text: label, sub: extra.sub || null, strong: extra.strong, button: extra.button || null, className: extra.child ? 'bg-child' : null },
            tons(item.openKg), money(item.openVal), tons(item.kg), money(item.val),
            { text: tons(item.fcKg), strong: true }, { text: money(item.fcVal), strong: true },
            item.hasGoal ? { text: pct(item.attKg), bar: item.attKg, tone: extra.tone } : '–',
        ];
    }
    function renderForecast(state, expected) {
        const scope = without(state, ['u', 'r']);
        const units = [...cube(scope, 'u').values()].filter(nonEmpty).sort((a, b) => a.key - b.key);
        const reps = cube(scope, (fact) => `${fact.u}|${repKey(fact)}`, { byRep: true });
        const chosenUnits = picked(state, 'u');
        const chosenReps = picked(state, 'r');
        chosenUnits.forEach((code) => expandedUnits.add(code));
        forecastRows = [];
        const rows = [];
        units.forEach((unit) => {
            const open = expandedUnits.has(String(unit.key));
            const button = h('button', 'bg-expand', open ? '▾' : '▸');
            button.type = 'button';
            button.dataset.unit = unit.key;
            button.setAttribute('aria-expanded', open ? 'true' : 'false');
            button.setAttribute('aria-label', `${open ? 'Fechar' : 'Abrir'} os representantes de ${unitName(unit.key)}`);
            forecastRows.push({ kind: 'u', key: unit.key, item: unit });
            rows.push({ cells: forecastCells(unit, unitName(unit.key), { strong: true, button, tone: toneOf(unit.attKg, expected) }), selected: chosenUnits.includes(String(unit.key)), className: 'bg-row-group', index: forecastRows.length - 1 });
            if (!open) return;
            [...reps.values()].filter((item) => item.key.startsWith(`${unit.key}|`) && nonEmpty(item)).sort((a, b) => b.fcKg - a.fcKg).forEach((item) => {
                const rep = item.key.split('|')[1];
                forecastRows.push({ kind: 'r', key: rep, unit: unit.key, item });
                rows.push({ cells: forecastCells(item, repName(rep), { child: true, sub: item.hasGoal ? null : 'sem meta', tone: toneOf(item.attKg, expected) }), selected: chosenReps.includes(rep), index: forecastRows.length - 1 });
            });
        });
        const columns = [
            { label: 'Unidade / representante' }, { label: 'Em aberto (kg)', num: true }, { label: 'Em aberto (R$)', num: true },
            { label: 'Faturado (kg)', num: true }, { label: 'Faturado (R$)', num: true }, { label: 'Total (kg)', num: true }, { label: 'Total (R$)', num: true },
            { label: 'Total ÷ meta', num: true },
        ];
        const total = sumItems(units);
        drawTable(forecastNode, columns, rows, { clickable: true, footer: forecastCells(total, 'Total', { strong: true, tone: toneOf(total.attKg, expected) }), empty: 'Sem faturamento nem carteira neste filtro.', className: 'bg-table-forecast' });
    }
    forecastNode.addEventListener('click', (event) => {
        const button = event.target.closest('button.bg-expand');
        if (!button) return;
        const code = String(button.dataset.unit);
        if (expandedUnits.has(code)) expandedUnits.delete(code);
        else expandedUnits.add(code);
        const state = filter.get();
        renderForecast(state, expectedOf(paceOf(state)));
        const again = forecastNode.querySelector(`button.bg-expand[data-unit="${code}"]`);
        if (again) again.focus();
    });
    onRow(forecastNode, (index, event) => {
        const row = forecastRows[index];
        if (!row) return;
        if (row.kind === 'u') filter.toggle('u', row.key, event);
        else filter.toggle('r', row.key, event);
    });
    D.attach(forecastNode, {
        label: (index) => { const row = forecastRows[index]; return row ? (row.kind === 'u' ? unitName(row.key) : `${repName(row.key)} · ${unitName(row.unit)}`) : ''; },
        table: () => ({
            columns: [
                { key: 'label', label: 'Unidade / representante', sub: 'sub' },
                { key: 'openKg', label: 'Em aberto (kg)', type: 'kg' }, { key: 'openVal', label: 'Em aberto (R$)', type: 'money' },
                { key: 'kg', label: 'Faturado (kg)', type: 'kg' }, { key: 'val', label: 'Faturado (R$)', type: 'money' },
                { key: 'fcKg', label: 'Total (kg)', type: 'kg', strong: true }, { key: 'fcVal', label: 'Total (R$)', type: 'money' },
                { key: 'att', label: 'Total ÷ meta', type: 'pct', digits: 0 },
            ],
            rows: forecastRows.map((row, index) => ({
                label: row.kind === 'u' ? unitName(row.key) : repName(row.key), sub: row.kind === 'u' ? 'unidade' : unitName(row.unit),
                ...rowFromItem(row.item), __index: index,
            })),
        }),
        drill: (index) => {
            const row = forecastRows[index];
            let state = only(filter.get(), 'u', row.kind === 'u' ? row.key : row.unit);
            if (row.kind === 'r') state = only(state, 'r', row.key);
            return breakdown(state, row.kind === 'u' ? unitName(row.key) : `${repName(row.key)} · ${unitName(row.unit)}`, { first: 'carteira', kind: 'Previsão de faturamento' });
        },
        canDrill: (index) => !!forecastRows[index],
    });

    // ------------------------------------------------------------------ faturamento por família (unidade → família)
    const familyTableNode = byId('tableFamilies');
    let familyRows = [];
    function familyCells(item, label, extra = {}) {
        const change = item.prevKg ? item.kg / item.prevKg - 1 : null;
        return [
            { text: label, strong: extra.strong, className: extra.child ? 'bg-child' : null, sub: extra.sub || null },
            tons(item.kg), money(item.val), item.kg ? `${N2.format(item.val / item.kg)}` : '–',
            item.hasGoal ? tons(item.goalKg) : '–',
            item.hasGoal ? { text: pct(item.billedKg), bar: item.billedKg, tone: extra.tone } : '–',
            item.prevKg ? tons(item.prevKg) : '–',
            { text: change == null ? '–' : signedPct(change), className: change == null ? null : change >= 0 ? 'bg-up' : 'bg-down' },
        ];
    }
    function renderFamilyTable(state, expected) {
        const scope = without(state, 'f');
        const units = [...cube(scope, 'u').values()].filter((item) => item.kg || item.goalKg).sort((a, b) => a.key - b.key);
        const pairs = cube(scope, (fact) => `${fact.u}|${fact.f}`);
        const chosen = picked(state, 'f');
        familyRows = [];
        const rows = [];
        units.forEach((unit) => {
            familyRows.push({ kind: 'u', key: unit.key, item: unit });
            rows.push({ cells: familyCells(unit, unitName(unit.key), { strong: true, tone: toneOf(unit.billedKg, expected) }), className: 'bg-row-group', selected: picked(state, 'u').includes(String(unit.key)), index: familyRows.length - 1 });
            [...pairs.values()].filter((item) => item.key.startsWith(`${unit.key}|`) && (item.kg || item.goalKg)).sort((a, b) => b.kg - a.kg).forEach((item) => {
                const family = item.key.split('|').slice(1).join('|');
                familyRows.push({ kind: 'f', key: family, unit: unit.key, item });
                rows.push({ cells: familyCells(item, familyShort(family), { child: true, sub: family, tone: toneOf(item.billedKg, expected) }), selected: chosen.includes(family), index: familyRows.length - 1 });
            });
        });
        const columns = [
            { label: 'Unidade / família' }, { label: 'Peso', num: true }, { label: 'Valor', num: true }, { label: 'R$/kg', num: true },
            { label: 'Meta (kg)', num: true }, { label: 'Faturado ÷ meta', num: true }, { label: String(year - 1), num: true }, { label: `vs ${year - 1}`, num: true },
        ];
        drawTable(familyTableNode, columns, rows, { clickable: true, footer: familyCells(sumItems(units), 'Total', { strong: true }), empty: 'Sem faturamento neste filtro.', className: 'bg-table-families' });
    }
    onRow(familyTableNode, (index, event) => {
        const row = familyRows[index];
        if (!row) return;
        if (row.kind === 'u') filter.toggle('u', row.key, event);
        else filter.toggle('f', row.key, event);
    });
    D.attach(familyTableNode, {
        label: (index) => { const row = familyRows[index]; return row ? (row.kind === 'u' ? unitName(row.key) : `${familyShort(row.key)} · ${unitName(row.unit)}`) : ''; },
        table: () => ({
            columns: [
                { key: 'label', label: 'Unidade / família', sub: 'sub' },
                { key: 'kg', label: 'Peso (kg)', type: 'kg' }, { key: 'val', label: 'Valor (R$)', type: 'money' },
                { key: 'price', label: 'R$/kg', type: 'dec', digits: 2 }, { key: 'goalKg', label: 'Meta (kg)', type: 'kg' },
                { key: 'billed', label: 'Faturado ÷ meta', type: 'pct', digits: 0 }, { key: 'prevKg', label: `${year - 1} (kg)`, type: 'kg' },
            ],
            rows: familyRows.map((row, index) => ({
                label: row.kind === 'u' ? unitName(row.key) : familyLabel(row.key), sub: row.kind === 'u' ? 'unidade' : unitName(row.unit),
                ...rowFromItem(row.item), billed: row.item.billedKg, __index: index,
            })),
        }),
        drill: (index) => {
            const row = familyRows[index];
            let state = only(filter.get(), 'u', row.kind === 'u' ? row.key : row.unit);
            if (row.kind === 'f') state = only(state, 'f', row.key);
            return breakdown(state, row.kind === 'u' ? unitName(row.key) : `${familyShort(row.key)} · ${unitName(row.unit)}`, { first: 'notas', kind: 'Faturamento por família' });
        },
        canDrill: (index) => !!familyRows[index],
    });

    // ------------------------------------------------------------------ pedidos em atraso
    const lateNode = byId('tableLate');
    let lateRows = [];
    const LATE_SHOWN = 12;
    function renderLate(state) {
        if (!data.is_current) {
            lateRows = [];
            drawTable(lateNode, [], [], { empty: `Carteira em atraso só no ano corrente (${new Date(todayTime).getFullYear()}).` });
            return;
        }
        const byOrder = new Map();
        lateOf(state).forEach((fact) => {
            let entry = byOrder.get(fact.o);
            if (!entry) { entry = { o: fact.o, c: fact.c, r: fact.r, d: fact.d, kg: 0, val: 0, ready: 0, items: [], families: new Set() }; byOrder.set(fact.o, entry); }
            entry.kg += fact.k || 0;
            entry.val += fact.val;
            entry.ready += fact.p || 0;
            entry.items.push(fact);
            entry.families.add(fact.f);
            if (fact.d && (!entry.d || fact.d < entry.d)) entry.d = fact.d;
        });
        lateRows = [...byOrder.values()].sort((a, b) => String(a.d || '').localeCompare(String(b.d || '')) || b.kg - a.kg);
        const chosen = picked(state, 'c');
        const rows = lateRows.slice(0, LATE_SHOWN).map((entry) => ({
            selected: chosen.includes(String(entry.c)),
            cells: [
                { text: entry.o.split('-').pop(), sub: `${entry.items.length} ite${entry.items.length === 1 ? 'm' : 'ns'}` },
                { text: customerName(entry.c), sub: repName(entry.r), wrap: true },
                dateText(entry.d),
                { text: `${N0.format(daysLate(entry.d) || 0)} d`, strong: true },
                tons(entry.kg), money(entry.val),
                entry.kg ? pct(entry.ready / entry.kg) : '–',
            ],
        }));
        const columns = [{ label: 'Pedido' }, { label: 'Cliente' }, { label: 'Entrega' }, { label: 'Atraso', num: true }, { label: 'Kg', num: true }, { label: 'R$', num: true }, { label: 'Pronto', num: true }];
        drawTable(lateNode, columns, rows, {
            clickable: true, empty: 'Nenhum pedido com entrega vencida neste filtro.', className: 'bg-table-late',
            more: lateRows.length > LATE_SHOWN ? `Mais ${N0.format(lateRows.length - LATE_SHOWN)} pedido(s): ⋯ › Mostrar como tabela, ou o selo "Expandir dados".` : null,
        });
    }
    onRow(lateNode, (index, event) => { const entry = lateRows[index]; if (entry) filter.toggle('c', entry.c, event); });
    D.attach(lateNode, {
        label: (index) => (lateRows[index] ? `Pedido ${lateRows[index].o.split('-').pop()} · ${customerName(lateRows[index].c)}` : ''),
        table: () => ({
            columns: [
                { key: 'order', label: 'Pedido' }, { key: 'customer', label: 'Cliente', href: 'href', sub: 'rep' }, { key: 'delivery', label: 'Entrega', type: 'date' },
                { key: 'late', label: 'Atraso (dias)', type: 'int' }, { key: 'kg', label: 'Kg', type: 'kg', total: true }, { key: 'val', label: 'R$', type: 'money', total: true },
                { key: 'ready', label: 'Pronto (kg)', type: 'kg', total: true },
            ],
            rows: lateRows.map((entry, index) => ({
                order: entry.o.split('-').pop(), customer: customerName(entry.c), href: profileUrl(entry.c), rep: repName(entry.r), delivery: entry.d,
                late: daysLate(entry.d), kg: entry.kg, val: entry.val, ready: entry.ready, __index: index,
            })),
        }),
        drill: (index) => {
            const entry = lateRows[index];
            const customerItems = orders.filter((fact) => fact.c === entry.c);
            return {
                title: `Pedido ${entry.o.split('-').pop()} · ${customerName(entry.c)}`,
                subtitle: `Entrega prevista em ${dateText(entry.d)} · ${N0.format(daysLate(entry.d) || 0)} dia(s) de atraso · ${repName(entry.r)}`,
                summary: [
                    { label: 'Em atraso', value: tons(entry.kg), note: money(entry.val), tone: 'bad' },
                    { label: 'Pronto em estoque', value: tons(entry.ready), note: entry.kg ? `${pct(entry.ready / entry.kg)} do pedido` : '' },
                    { label: 'Carteira do cliente', value: tons(customerItems.reduce((acc, fact) => acc + (fact.k || 0), 0)), note: `${N0.format(customerItems.length)} item(ns) em aberto` },
                ],
                sections: [
                    { key: 'itens', label: 'Itens do pedido', columns: orderColumns, rows: entry.items.map(orderRow), sort: { key: 'kg', dir: 'desc' }, empty: 'Sem itens.' },
                    { key: 'cliente', label: 'Carteira do cliente', columns: orderColumns, rows: customerItems.map(orderRow), sort: { key: 'delivery', dir: 'asc' }, groupBy: ['family', 'unit'], empty: 'Sem carteira.' },
                ],
            };
        },
        canDrill: (index) => !!lateRows[index],
        actions: (index) => (lateRows[index] ? [{ label: 'Abrir a ficha do cliente', href: profileUrl(lateRows[index].c) }] : []),
    });

    // ------------------------------------------------------------------ faturamento contado em outra unidade
    // A nota sai de uma filial e conta em outra unidade do orçamento (o card "laminado transferido da
    // Matriz para a Serafina" do PBIX, para todas as unidades).
    const transfersNode = byId('tableTransfers');
    let transferRows = [];
    function renderTransfers(state) {
        const map = new Map();
        billing.forEach((fact) => {
            if (!matches(fact, state) || branchUnit(fact.b) === fact.u) return;
            const key = `${fact.b}>${fact.u}`;
            const entry = map.get(key) || { key, b: fact.b, u: fact.u, kg: 0, val: 0, families: new Map() };
            entry.kg += fact.k || 0;
            entry.val += fact.val;
            entry.families.set(fact.f, (entry.families.get(fact.f) || 0) + (fact.k || 0));
            map.set(key, entry);
        });
        transferRows = [...map.values()].sort((a, b) => b.kg - a.kg);
        const rows = transferRows.map((entry) => ({
            cells: [
                { text: branchName(entry.b), sub: `conta em ${unitName(entry.u)}` },
                { text: [...entry.families.entries()].sort((a, b) => b[1] - a[1]).slice(0, 2).map(([family]) => familyShort(family)).join(', '), wrap: true },
                tons(entry.kg), { text: money(entry.val), title: entry.kg ? perKg(entry.val, entry.kg) : null },
            ],
            selected: picked(state, 'u').includes(String(entry.u)),
        }));
        const total = transferRows.reduce((acc, entry) => ({ kg: acc.kg + entry.kg, val: acc.val + entry.val }), { kg: 0, val: 0 });
        drawTable(transfersNode, [{ label: 'Faturado em' }, { label: 'Famílias' }, { label: 'Kg', num: true }, { label: 'R$', num: true }], rows, {
            clickable: true, empty: 'Todo o faturamento deste filtro conta na própria filial da nota.', className: 'bg-table-transfers',
            footer: rows.length > 1 ? [{ text: 'Total', strong: true }, '', { text: tons(total.kg), strong: true }, { text: money(total.val), strong: true }] : null,
        });
    }
    onRow(transfersNode, (index, event) => { const entry = transferRows[index]; if (entry) filter.toggle('u', entry.u, event); });
    D.attach(transfersNode, {
        label: (index) => (transferRows[index] ? `${branchName(transferRows[index].b)} → ${unitName(transferRows[index].u)}` : ''),
        table: () => ({
            columns: [{ key: 'branch', label: 'Faturado em' }, { key: 'unit', label: 'Conta em' }, { key: 'kg', label: 'Kg', type: 'kg', total: true }, { key: 'val', label: 'R$', type: 'money', total: true }],
            rows: transferRows.map((entry, index) => ({ branch: branchName(entry.b), unit: unitName(entry.u), kg: entry.kg, val: entry.val, __index: index })),
        }),
        drill: (index) => {
            const entry = transferRows[index];
            const state = only(filter.get(), 'u', entry.u);
            const facts = billing.filter((fact) => matches(fact, state) && fact.b === entry.b);
            const families = new Map();
            facts.forEach((fact) => {
                const row = families.get(fact.f) || { label: familyLabel(fact.f), kg: 0, val: 0 };
                row.kg += fact.k || 0;
                row.val += fact.val;
                families.set(fact.f, row);
            });
            const result = {
                title: `${branchName(entry.b)} → ${unitName(entry.u)}`,
                subtitle: `Notas da filial ${branchName(entry.b)} que contam na unidade ${unitName(entry.u)} · ${describe(filter.get())}`,
                summary: [{ label: 'Volume', value: tons(entry.kg) }, { label: 'Valor', value: money(entry.val), note: BASES[basis] }, { label: 'R$/kg', value: perKg(entry.val, entry.kg) }],
                sections: [{
                    key: 'familias', label: 'Por família',
                    columns: [{ key: 'label', label: 'Família' }, { key: 'kg', label: 'Kg', type: 'kg', total: true, strong: true }, { key: 'val', label: 'R$', type: 'money', total: true }, { key: 'price', label: 'R$/kg', type: 'dec', digits: 2, ratio: ['val', 'kg'] }],
                    rows: [...families.values()].map((row) => ({ ...row, price: row.kg ? row.val / row.kg : null })), sort: { key: 'kg', dir: 'desc' },
                }],
            };
            const branch = branchName(entry.b);
            return withNotes(result, state, { keep: (row) => row.branch === branch });
        },
        canDrill: (index) => !!transferRows[index],
    });

    // ------------------------------------------------------------------ destaques
    const highlightsNode = byId('listHighlights');
    let highlightItems = [];
    function buildHighlights(state, totals, pace) {
        const expected = expectedOf(pace);
        const items = [];
        if (totals.hasGoal) {
            items.push({
                icon: 'target', tone: toneOf(totals.attKg, expected), title: `Previsão em ${pct(totals.attKg)} da meta de volume`,
                detail: `${tons(totals.fcKg)} de ${tons(totals.goalKg)}${expected != null && !pace.closed ? ` · ritmo esperado ${pct(expected)}` : ''}`,
                value: signed(totals.fcKg - totals.goalKg, tons), state,
            });
        }
        const families = [...cube(state, 'f').values()].filter((item) => item.hasGoal && item.goalKg > 0);
        families.filter((item) => toneOf(item.attKg, expected) === 'bad').sort((a, b) => (b.goalKg - b.fcKg) - (a.goalKg - a.fcKg)).slice(0, 3).forEach((item) => {
            items.push({ icon: 'alert', tone: 'bad', title: familyShort(item.key), detail: `previsão em ${pct(item.attKg)} da meta · faltam ${tons(item.goalKg - item.fcKg)}`, value: pct(item.attKg), filter: ['f', item.key], state: only(state, 'f', item.key) });
        });
        families.filter((item) => item.attKg >= 1.05).sort((a, b) => (b.fcKg - b.goalKg) - (a.fcKg - a.goalKg)).slice(0, 2).forEach((item) => {
            items.push({ icon: 'star', tone: 'good', title: familyShort(item.key), detail: `${signed(item.fcKg - item.goalKg, tons)} acima da meta`, value: pct(item.attKg), filter: ['f', item.key], state: only(state, 'f', item.key) });
        });
        if (!picked(state, 'r').length) {
            const reps = repItems.filter((item) => item.hasGoal && item.goalKg > 0);
            const behind = reps.filter((item) => toneOf(item.attKg, expected) === 'bad').sort((a, b) => (b.goalKg - b.fcKg) - (a.goalKg - a.fcKg));
            if (reps.length && expected != null) {
                items.push({
                    icon: 'users', tone: behind.length ? 'warn' : 'good',
                    title: behind.length ? `${N0.format(behind.length)} de ${N0.format(reps.length)} representantes abaixo de 90% do esperado` : 'Todos os representantes no ritmo da meta',
                    detail: behind.slice(0, 3).map((item) => `${repName(item.key)} (${pct(item.attKg)})`).join(' · '),
                    value: '', filter: behind.length ? ['r', behind.map((item) => item.key)] : null,
                });
            }
        }
        if (totals.kg && totals.hasGoal && totals.goalKg) {
            const billedPrice = totals.val / totals.kg;
            const goalPrice = totals.goalVal / totals.goalKg;
            items.push({
                icon: 'coin', tone: billedPrice >= goalPrice ? 'good' : 'warn', title: `Preço médio faturado ${perKg(totals.val, totals.kg)}`,
                detail: `orçamento ${perKg(totals.goalVal, totals.goalKg)} (${signedPct(billedPrice / goalPrice - 1)}) · ${BASES[basis]}`, value: '', state,
            });
        }
        if (totals.prevKg) {
            const change = totals.kg / totals.prevKg - 1;
            items.push({ icon: 'history', tone: change >= 0 ? 'good' : 'bad', title: `${signedPct(change)} de volume contra ${year - 1}`, detail: `${tons(totals.kg)} contra ${tons(totals.prevKg)} nos mesmos meses${data.is_current && (!monthsOf(state).length || monthsOf(state).includes(data.current_month)) ? ' (o mês corrente inteiro no ano anterior)' : ''}`, value: '', state });
        }
        if (totals.ready > 0) {
            items.push({ icon: 'box', tone: null, title: `${tons(totals.ready)} prontos em estoque`, detail: `${pct(ratio(totals.ready, totals.openKg))} da carteira a faturar já produzida · ${money(totals.readyVal)}`, value: '', state, first: 'carteira' });
        }
        if (!Object.keys(state).length && data.outside && data.outside.k) {
            items.push({ icon: 'info', tone: null, title: `${tons(data.outside.k)} faturados fora do orçamento`, detail: `${money(basis === 'sem_impostos' ? data.outside.t : data.outside.n)} em grupos sem meta (aparas, subprodutos) e em outras empresas`, value: '' });
        }
        return items;
    }
    function renderHighlights(state, totals, pace) {
        highlightItems = buildHighlights(state, totals, pace);
        highlightsNode.replaceChildren();
        if (!highlightItems.length) {
            h('li', 'mx-empty-note', 'Nada a destacar neste filtro.', highlightsNode);
            return;
        }
        highlightItems.forEach((item, index) => {
            const li = h('li', `bg-highlight${item.tone ? ` is-${item.tone}` : ''}${item.filter ? ' is-clickable' : ''}`, null, highlightsNode);
            li.setAttribute('data-mx-index', index);
            if (item.filter) { li.tabIndex = 0; li.setAttribute('role', 'button'); li.title = 'Clique filtra a página'; }
            icon(item.icon, li);
            const main = h('div', 'mx-list-main', null, li);
            h('strong', null, item.title, main);
            if (item.detail) h('small', null, item.detail, main);
            if (item.value) h('span', 'mx-list-value', item.value, li);
        });
    }
    const applyHighlight = (index, event) => {
        const item = highlightItems[index];
        if (!item || !item.filter) return;
        const [key, value] = item.filter;
        if (Array.isArray(value)) filter.set(key, value);
        else filter.toggle(key, value, event);
    };
    highlightsNode.addEventListener('click', (event) => { const li = event.target.closest('li[data-mx-index]'); if (li) applyHighlight(Number(li.getAttribute('data-mx-index')), event); });
    highlightsNode.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        const li = event.target.closest('li[data-mx-index]');
        if (li) { event.preventDefault(); applyHighlight(Number(li.getAttribute('data-mx-index')), event); }
    });
    D.attach(highlightsNode, {
        label: (index) => (highlightItems[index] ? highlightItems[index].title : ''),
        table: () => ({
            columns: [{ key: 'title', label: 'Destaque' }, { key: 'detail', label: 'Detalhe', wrap: true }, { key: 'value', label: 'Valor' }],
            rows: highlightItems.map((item, index) => ({ title: item.title, detail: item.detail, value: item.value, __index: index, __tone: item.tone === 'bad' ? 'bad' : item.tone === 'warn' ? 'warn' : null })),
        }),
        drill: (index) => {
            const item = highlightItems[index];
            return breakdown(item.state, item.title, { first: item.first || 'f', kind: 'Destaque', notes: item.first !== 'carteira' });
        },
        canDrill: (index) => !!highlightItems[index] && !!highlightItems[index].state,
    });

    // ------------------------------------------------------------------ desenho
    function render() {
        const state = filter.get();
        const totals = totalOf(state);
        const pace = paceOf(state);
        const expected = expectedOf(pace);
        paintToolbar(state);
        paintSource();
        renderKpis(state, totals, pace);
        renderGauges(state, totals, pace);
        renderFamilies(state, expected);
        renderMonths(state);
        renderCumulative(state);
        renderReps(state, expected);
        renderRanking(state, expected);
        renderHeat(state);
        renderForecast(state, expected);
        renderFamilyTable(state, expected);
        renderLate(state);
        renderTransfers(state);
        renderHighlights(state, totals, pace);
    }
    // Vários filtros no mesmo clique (célula do calor: família e mês) desenham uma vez só.
    let scheduled = false;
    filter.subscribe(() => {
        if (scheduled) return;
        scheduled = true;
        queueMicrotask(() => { scheduled = false; render(); });
    });
    render();
})();
