/* Painel do Helpdesk (tela inicial do Dashes): filtro cruzado sobre a base de
   chamados do ConnectMX, no mesmo padrão dos outros painéis (MXLive).

   Os dados chegam já resumidos por chamado (tiqueue/helpdesk_bi.py). Clicar numa
   barra, fatia, linha de ranking, nota ou dia da semana filtra todos os gráficos;
   Ctrl + clique soma itens; clicar de novo tira o filtro. Os filtros viram
   etiquetas e ficam no endereço (#c=3&st=pending), então o link abre igual. */
(() => {
    const L = window.MXLive;
    const dataNode = document.getElementById('mxPanelData');
    if (!L || !dataNode) return;
    const fmt = L.fmt;
    const el = (id) => document.getElementById(id);

    // Dimensões dos chamados que a página filtra (o mês, `m`, é o período).
    const DIMS = ['c', 's', 'g', 't', 'p', 'st', 'a', 'o', 'sla', 'r', 'w'];
    const RATING_KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10'];
    const STATUS_TONE = { completed: 'is-good', pending: 'is-warn', assumed: 'is-info', waiting_requester: '', cancelled: '' };

    let data = null;
    let rows = [];
    let conversations = [];
    let knowledge = [];
    let openStatuses = new Set();

    // Campos de texto repetido (prioridade, situação, canal, SLA) chegam como índice de `enums`.
    const toObjects = (fields, list, enums) => list.map((values) => Object.fromEntries(fields.map((field, index) => {
        const value = values[index];
        return [field, enums && enums[field] && value != null ? enums[field][value] : value];
    })));
    function load(next) {
        data = next;
        rows = toObjects(data.fields, data.rows, data.enums);
        conversations = toObjects(data.assistant.fields, data.assistant.rows);
        knowledge = toObjects(data.knowledge.fields, data.knowledge.rows);
        openStatuses = new Set(data.open_statuses);
    }
    load(JSON.parse(dataNode.textContent));

    const nameOf = (dim, key) => {
        const names = data.names[dim] || {};
        if (key == null || key === '') return '–';
        return names[String(key)] || String(key);
    };

    // ------------------------------------------------------------------ cálculos
    const median = (values) => {
        const list = values.filter((value) => value != null).sort((a, b) => a - b);
        if (!list.length) return null;
        const middle = Math.floor(list.length / 2);
        return list.length % 2 ? list[middle] : (list[middle - 1] + list[middle]) / 2;
    };
    const average = (values) => {
        const list = values.filter((value) => value != null);
        return list.length ? list.reduce((acc, value) => acc + value, 0) / list.length : null;
    };
    const countBy = (list, key) => {
        const totals = new Map();
        list.forEach((row) => { const id = String(row[key]); totals.set(id, (totals.get(id) || 0) + 1); });
        return totals;
    };
    // Minutos em linguagem de gente: "35 min", "4h 20", "3,5 dias".
    const duration = (minutes) => {
        if (minutes == null || Number.isNaN(minutes)) return '–';
        if (minutes < 60) return `${Math.round(minutes)} min`;
        if (minutes < 60 * 24) {
            const hours = Math.floor(minutes / 60);
            const rest = Math.round(minutes - hours * 60);
            return rest && rest < 60 ? `${hours}h ${String(rest).padStart(2, '0')}` : `${Math.round(minutes / 60)}h`;
        }
        return fmt.days(minutes / 1440);
    };
    const dateOf = (row) => {
        const year = Math.floor(row.m / 12);
        const month = (row.m % 12) + 1;
        return `${String(row.dd).padStart(2, '0')}/${String(month).padStart(2, '0')}/${String(year).slice(2)}`;
    };

    // ------------------------------------------------------------------ filtro cruzado
    const filter = L.crossFilter({
        labels: { m: 'Mês', c: 'Empresa', s: 'Setor', g: 'Área', t: 'Tipo', p: 'Prioridade', st: 'Situação', a: 'Atendente', o: 'Canal', sla: 'SLA', r: 'Nota', w: 'Dia da semana' },
        names: {
            m: (value) => L.monthLabel(value),
            c: (value) => nameOf('c', value), s: (value) => nameOf('s', value), g: (value) => nameOf('g', value),
            t: (value) => nameOf('t', value), p: (value) => nameOf('p', value), st: (value) => nameOf('st', value),
            a: (value) => nameOf('a', value), o: (value) => nameOf('o', value), sla: (value) => nameOf('sla', value),
            r: (value) => `nota ${value}`, w: (value) => nameOf('w', value),
        },
        chips: '#mxFilterBar',
    });
    L.monthPicker(el('mxMonths'), { months: data.months, filter, dimension: 'm', title: 'Meses', allLabel: 'Últimos 12 meses' });

    const monthKeys = () => data.months.map((month) => month.key);
    // Período: os meses escolhidos ou os últimos 12; a comparação é com os mesmos meses do ano anterior.
    function periodOf(state) {
        const picked = L.chosen(state.m).map(Number).sort((a, b) => a - b);
        const list = picked.length ? picked : monthKeys().slice(-12);
        return {
            picked: picked.length > 0,
            list,
            keys: new Set(list),
            previous: new Set(list.map((key) => key - 12)),
            label: !picked.length ? '12 meses' : picked.length === 1 ? L.monthLabel(picked[0]) : `${picked.length} meses escolhidos`,
        };
    }
    const passDims = (row, state, skip) => DIMS.every((key) => key === skip || L.pass(state, key, row[key]));
    const subset = (state, skip) => rows.filter((row) => passDims(row, state, skip));

    // ------------------------------------------------------------------ "Expandir dados"
    // Botão direito (toque longo ou ⋯ do card) num ponto abre os registros por trás dele, com os
    // filtros da página (maxisysDrill.js). Protocolo e assunto vêm do servidor, como na tabela,
    // para os chamados mais recentes de cada lista.
    const DRILL_DETAILS = 300;
    const openedIn = (period, list) => list.filter((row) => period.keys.has(row.m));
    const pageFiltered = () => Object.keys(filter.get()).some((key) => key !== 'm');
    const isoDate = (row) => `${Math.floor(row.m / 12)}-${String((row.m % 12) + 1).padStart(2, '0')}-${String(row.dd).padStart(2, '0')}`;
    const ticketColumns = [
        { key: 'code', label: 'Protocolo', href: 'url', nowrap: true },
        { key: 'title', label: 'Assunto', wrap: true },
        { key: 'company', label: 'Empresa' },
        { key: 'sector', label: 'Setor' },
        { key: 'area', label: 'Área' },
        { key: 'type', label: 'Tipo' },
        { key: 'priority', label: 'Prioridade' },
        { key: 'status', label: 'Situação' },
        { key: 'attendant', label: 'Atendente' },
        { key: 'channel', label: 'Canal' },
        { key: 'sla', label: 'SLA' },
        { key: 'opened', label: 'Abertura', type: 'date', nowrap: true },
        { key: 'first', label: '1ª resposta', type: 'dec', format: (value) => duration(value) },
        { key: 'resolution', label: 'Resolução', type: 'dec', format: (value) => duration(value) },
        { key: 'rating', label: 'Nota', type: 'int', total: 'avg' },
    ];
    const TICKET_GROUPS = ['company', 'sector', 'area', 'type', 'priority', 'status', 'attendant', 'channel', 'sla'];
    const ticketRow = (row) => {
        const info = details.get(String(row.id));
        return {
            code: info && info.code ? info.code : `#${row.id}`,
            url: info && info.code ? data.detail_url.replace('__code__', encodeURIComponent(info.code)) : null,
            title: info ? info.title || '(sem assunto)' : '',
            company: nameOf('c', row.c), sector: nameOf('s', row.s), area: nameOf('g', row.g), type: nameOf('t', row.t),
            priority: nameOf('p', row.p), status: nameOf('st', row.st), attendant: nameOf('a', row.a), channel: nameOf('o', row.o),
            sla: nameOf('sla', row.sla), opened: isoDate(row), first: row.fr, resolution: row.rt, rating: row.r,
            __tone: row.sla === 'late' || row.sla === 'breached' ? 'bad' : null,
        };
    };
    // O endpoint de detalhes responde até 60 chamados por pedido.
    async function withDetails(list) {
        const ids = list.slice(0, DRILL_DETAILS).map((row) => row.id).filter((id) => !details.has(String(id)));
        const chunks = [];
        for (let index = 0; index < ids.length; index += 60) chunks.push(ids.slice(index, index + 60));
        await Promise.all(chunks.map(async (chunk) => {
            try {
                const response = await fetch(`${data.details_url}?ids=${chunk.join(',')}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
                if (!response.ok) return;
                const payload = await response.json();
                Object.entries(payload.tickets || {}).forEach(([id, info]) => details.set(id, info));
            } catch (error) { /* sem protocolo e assunto: a linha fica com o número do chamado */ }
        }));
    }
    async function ticketsResult(title, subtitle, list, more = [], firstLabel = 'Chamados') {
        const lists = [{ key: 'tickets', label: firstLabel, list }, ...more];
        await Promise.all(lists.map((entry) => withDetails(entry.list)));
        const resolved = list.filter((row) => row.cm != null);
        const withSla = list.filter((row) => row.sla === 'met' || row.sla === 'breached');
        const met = withSla.filter((row) => row.sla === 'met').length;
        const ratings = list.map((row) => row.r).filter((value) => value != null);
        const summary = [
            { label: firstLabel, value: fmt.int(list.length), note: `${fmt.int(list.filter((row) => openStatuses.has(row.st)).length)} em aberto` },
            ...more.map((entry) => ({ label: entry.label, value: fmt.int(entry.list.length) })),
            { label: 'SLA cumprido', value: withSla.length ? fmt.pct(met / withSla.length, 0) : '–', note: withSla.length ? `${fmt.int(met)} de ${fmt.int(withSla.length)} com SLA` : 'nenhum com SLA medido', tone: withSla.length ? (met / withSla.length >= 0.9 ? 'good' : 'warn') : null },
            { label: 'Resolução (mediana)', value: duration(median(resolved.map((row) => row.rt))), note: `1ª resposta ${duration(median(list.map((row) => row.fr)))}` },
            { label: 'Nota média', value: ratings.length ? fmt.dec(average(ratings), 1) : '–', note: `${fmt.int(ratings.length)} avaliação(ões)` },
        ];
        return {
            title,
            subtitle: `${subtitle}${pageFiltered() ? ' · com os filtros da página' : ''}`,
            summary,
            sections: lists.map((entry) => ({
                key: entry.key, label: entry.label, columns: ticketColumns, rows: entry.list.map(ticketRow),
                sort: { key: 'opened', dir: 'desc' }, groupBy: TICKET_GROUPS, empty: 'Nenhum chamado neste ponto.',
                note: entry.list.length > DRILL_DETAILS ? `Protocolo e assunto dos ${fmt.int(DRILL_DETAILS)} mais recentes; os outros aparecem pelo número.` : '',
            })),
        };
    }
    // Fatias e rankings: os chamados abertos no período com aquele valor (o próprio filtro fica de fora, como no gráfico).
    const dimDrill = (dim, kind) => ({ key, label }) => {
        const period = periodOf(filter.get());
        return ticketsResult(label, `${kind} · chamados abertos em ${period.label}`,
            openedIn(period, subset(filter.get(), dim)).filter((row) => String(row[dim]) === String(key)));
    };
    const flowDrill = ({ index, label }) => {
        const key = monthKeys()[index];
        const scoped = subset(filter.get());
        return ticketsResult(label, 'Chamados abertos e resolvidos no mês', scoped.filter((row) => row.m === key),
            [{ key: 'resolved', label: 'Resolvidos no mês', list: scoped.filter((row) => row.cm === key) }], 'Abertos no mês');
    };
    const ratingDrill = ({ key }) => {
        const period = periodOf(filter.get());
        return ticketsResult(`Nota ${key}`, `Avaliações dos chamados resolvidos em ${period.label}`,
            subset(filter.get(), 'r').filter((row) => row.r != null && row.cm != null && period.keys.has(row.cm) && String(row.r) === String(key)));
    };
    const talkColumns = [
        { key: 'month', label: 'Mês' }, { key: 'company', label: 'Empresa' }, { key: 'sector', label: 'Setor' },
        { key: 'outcome', label: 'Como terminou' }, { key: 'turns', label: 'Mensagens', type: 'int', total: 'avg' },
        { key: 'suggested', label: 'Sugeriu solução' },
    ];
    const assistantDrill = ({ key, label }) => {
        const state = filter.get();
        const period = periodOf(state);
        const talks = conversations.filter((talk) => period.keys.has(talk.m) && L.pass(state, 'c', talk.c) && L.pass(state, 's', talk.s) && String(talk.outcome) === String(key));
        const suggested = talks.filter((talk) => talk.suggested).length;
        return {
            title: label,
            subtitle: `Conversas com o assistente em ${period.label}${L.chosen(state.c).length || L.chosen(state.s).length ? ' · com o filtro de empresa e setor' : ''}`,
            summary: [
                { label: 'Conversas', value: fmt.int(talks.length) },
                { label: 'Mensagens por conversa', value: talks.length ? fmt.dec(average(talks.map((talk) => talk.turns)), 1) : '–' },
                { label: 'Sugeriu solução', value: fmt.int(suggested), note: talks.length ? `${fmt.pct(suggested / talks.length, 0)} das conversas` : '' },
            ],
            sections: [{
                key: 'talks', label: 'Conversas', columns: talkColumns, groupBy: ['month', 'company', 'sector', 'suggested'], empty: 'Nenhuma conversa.',
                rows: talks.map((talk) => ({
                    month: L.monthLabel(talk.m), company: talk.c ? nameOf('c', talk.c) : '–', sector: talk.s ? nameOf('s', talk.s) : '–',
                    outcome: data.assistant.outcomes[talk.outcome] || talk.outcome, turns: talk.turns, suggested: talk.suggested ? 'Sim' : 'Não',
                })),
            }],
        };
    };
    const knowledgeDrill = ({ key, label }) => {
        const period = periodOf(filter.get());
        const entries = knowledge.filter((entry) => String(entry.category) === String(key));
        return {
            title: label,
            subtitle: 'Registros da base de conhecimento na categoria · a base vale para todos os filtros',
            summary: [
                { label: 'Registros', value: fmt.int(entries.length) },
                { label: 'Resolvidos', value: fmt.int(entries.filter((entry) => entry.resolved).length), note: 'o assistente só usa estes' },
                { label: `Novos em ${period.label}`, value: fmt.int(entries.filter((entry) => period.keys.has(entry.m)).length) },
                { label: 'Vieram de chamados', value: fmt.int(entries.filter((entry) => entry.documented).length) },
            ],
            sections: [{
                key: 'entries', label: 'Registros', groupBy: ['month', 'origin', 'resolved'], empty: 'Nenhum registro.',
                columns: [{ key: 'month', label: 'Incluído em' }, { key: 'origin', label: 'Origem' }, { key: 'resolved', label: 'Situação' }],
                rows: entries.slice().sort((a, b) => b.m - a.m).map((entry) => ({
                    month: L.monthLabel(entry.m), origin: entry.documented ? 'Documentação de chamado' : 'Cadastro direto',
                    resolved: entry.resolved ? 'Resolvido (o assistente usa)' : 'Não resolvido',
                })),
            }],
        };
    };
    const hasValue = ({ index, data: chart }) => chart.series.some((series) => series.values[index]);

    // Mapa de calor (dia × hora) e tabela de chamados: tabelas desenhadas aqui, ligadas ao menu à mão.
    // Pontos do mapa = dias (cabeçalho da linha) e células com chamados; na tabela, cada linha
    // abre os chamados da mesma empresa, e o selo / "Mostrar como tabela" trazem todos os do filtro.
    let heatPoints = [];
    let heatTable = { columns: [], rows: [] };
    let tableTickets = [];
    if (window.MXDrill) {
        const heatWrap = el('heatWeek') && el('heatWeek').closest('.mx-table-wrap');
        if (heatWrap) {
            window.MXDrill.attach(heatWrap, {
                name: 'Quando os chamados chegam',
                label: (index) => (heatPoints[index] ? heatPoints[index].label : ''),
                table: () => heatTable,
                drill: (index) => {
                    const point = heatPoints[index];
                    const period = periodOf(filter.get());
                    return ticketsResult(point.label, `Chamados abertos em ${period.label}`,
                        openedIn(period, subset(filter.get(), 'w')).filter((row) => row.w === point.weekday && (point.hour == null || row.h === point.hour)));
                },
                canDrill: (index) => !!heatPoints[index] && heatPoints[index].count > 0,
            });
        }
        const tableWrap = el('ticketRows') && (el('ticketRows').closest('.mx-table-wrap') || el('ticketRows').closest('table'));
        if (tableWrap) {
            window.MXDrill.attach(tableWrap, {
                name: 'Chamados do filtro',
                tableTitle: 'Chamados do filtro',
                label: (index) => (tableTickets[index] ? nameOf('c', tableTickets[index].c) : ''),
                table: () => ({ columns: ticketColumns, rows: tableTickets.map((row, index) => ({ ...ticketRow(row), __index: index })) }),
                drill: (index) => {
                    const row = tableTickets[index];
                    return ticketsResult(nameOf('c', row.c), `Chamados da empresa abertos em ${periodOf(filter.get()).label}`, tableTickets.filter((other) => other.c === row.c));
                },
                canDrill: (index) => !!tableTickets[index],
                actions: (index) => {
                    const info = tableTickets[index] && details.get(String(tableTickets[index].id));
                    return info && info.code ? [{ label: `Abrir o chamado ${info.code}`, href: data.detail_url.replace('__code__', encodeURIComponent(info.code)) }] : [];
                },
            });
        }
    }
    const drillHint = (window.MXDrill && window.MXDrill.hint) || '';

    // ------------------------------------------------------------------ gráficos
    const toggle = (dim) => (key, item, event) => filter.toggle(dim, key, event);
    const drillOptions = (dim, kind) => ({ valueType: 'int', labelName: kind, valueName: 'Chamados', drill: dimDrill(dim, kind) });
    const flowChart = L.columns(el('chartFlow'), { height: 240, format: fmt.int, maxBar: 14, onSelect: (key, index, event) => filter.toggle('m', key, event), valueType: 'int', labelName: 'Mês', drill: flowDrill, canDrill: hasValue });
    const statusDonut = L.donut(el('donutStatus'), { format: fmt.int, centerLabel: 'chamados', onSelect: toggle('st'), ...drillOptions('st', 'Situação') });
    const channelDonut = L.donut(el('donutChannel'), { format: fmt.int, centerLabel: 'chamados', onSelect: toggle('o'), ...drillOptions('o', 'Canal de abertura') });
    const slaDonut = L.donut(el('donutSla'), { format: fmt.int, centerLabel: 'chamados', onSelect: toggle('sla'), ...drillOptions('sla', 'SLA de resolução') });
    const priorityDonut = L.donut(el('donutPriority'), { format: fmt.int, centerLabel: 'chamados', onSelect: toggle('p'), ...drillOptions('p', 'Prioridade') });
    const companies = L.ranking(el('rankCompanies'), { format: fmt.int, limit: 8, color: '--mx-cat-1', onSelect: toggle('c'), ...drillOptions('c', 'Empresa') });
    const sectors = L.ranking(el('rankSectors'), { format: fmt.int, limit: 10, color: '--mx-cat-7', onSelect: toggle('s'), ...drillOptions('s', 'Setor') });
    const groups = L.ranking(el('rankGroups'), { format: fmt.int, limit: 8, color: '--mx-cat-3', onSelect: toggle('g'), ...drillOptions('g', 'Área atendida') });
    const attendants = L.ranking(el('rankAttendants'), { format: fmt.int, limit: 12, color: '--mx-cat-2', onSelect: toggle('a'), ...drillOptions('a', 'Atendente') });
    const types = L.ranking(el('rankTypes'), { format: fmt.int, limit: 12, color: '--mx-cat-4', onSelect: toggle('t'), ...drillOptions('t', 'Tipo de chamado') });
    const ratingsChart = L.columns(el('chartRatings'), { height: 200, format: fmt.int, maxBar: 28, onSelect: (key, index, event) => filter.toggle('r', key, event), valueType: 'int', labelName: 'Nota', drill: ratingDrill, canDrill: hasValue });
    const assistantDonut = L.donut(el('donutAssistant'), { format: fmt.int, centerLabel: 'conversas', valueType: 'int', labelName: 'Como terminou', valueName: 'Conversas', drill: assistantDrill });
    const knowledgeRank = L.ranking(el('rankKnowledge'), { format: fmt.int, limit: 8, color: '--mx-cat-5', emptyText: 'Nenhum registro na base ainda.', valueType: 'int', labelName: 'Categoria', valueName: 'Registros', drill: knowledgeDrill });

    const donutItems = (dim, list) => {
        const counts = countBy(list, dim);
        const order = (data.order[dim] || [...counts.keys()]).map(String);
        [...counts.keys()].forEach((key) => { if (!order.includes(key)) order.push(key); });
        const colors = data.colors[dim] || {};
        return order.filter((key) => counts.get(key)).map((key, index) => ({
            key, label: nameOf(dim, key), value: counts.get(key), color: colors[key] || L.categorical(index),
        }));
    };
    const rankItems = (dim, list) => [...countBy(list, dim).entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([key, value]) => ({ key, label: nameOf(dim, key), value }));

    function setDelta(node, change) {
        node.textContent = fmt.signedPct(change);
        node.className = `mx-delta${change == null ? '' : ' is-neutral'}`;
    }

    function renderHeat(list, selected) {
        const grid = Array.from({ length: 7 }, () => Array(24).fill(0));
        list.forEach((row) => { if (row.w != null && row.h != null) grid[row.w][row.h] += 1; });
        const busy = [...Array(24).keys()].filter((hour) => grid.some((day) => day[hour] > 0));
        const first = busy.length ? Math.min(...busy, 7) : 7;
        const last = busy.length ? Math.max(...busy, 18) : 18;
        const hours = [];
        for (let hour = first; hour <= last; hour += 1) hours.push(hour);
        const values = grid.flat().filter((value) => value > 0).sort((a, b) => a - b);
        const breaks = [0.2, 0.4, 0.6, 0.8].map((q) => values[Math.min(values.length - 1, Math.floor(q * values.length))] || 0);
        const picked = L.chosen(selected);
        const table = el('heatWeek');
        table.replaceChildren();
        const head = table.createTHead().insertRow();
        const corner = document.createElement('th');
        corner.textContent = 'Dia';
        head.appendChild(corner);
        hours.forEach((hour) => { const th = document.createElement('th'); th.textContent = `${hour}h`; head.appendChild(th); });
        const body = table.createTBody();
        heatPoints = [];
        const heatRows = [];
        grid.forEach((day, weekday) => {
            const tr = body.insertRow();
            const th = document.createElement('th');
            th.className = 'is-row';
            th.scope = 'row';
            const dayTotal = day.reduce((acc, count) => acc + count, 0);
            heatPoints.push({ weekday, hour: null, label: nameOf('w', weekday), count: dayTotal });
            th.setAttribute('data-mx-index', heatPoints.length - 1);
            heatRows.push({ day: nameOf('w', weekday), ...Object.fromEntries(hours.map((hour) => [`h${hour}`, day[hour]])), total: dayTotal, __index: heatPoints.length - 1 });
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `mx-filter-cell${picked.includes(String(weekday)) ? ' is-filter-active' : ''}`;
            button.textContent = nameOf('w', weekday);
            button.title = `Filtrar ${nameOf('w', weekday)}`;
            button.setAttribute('aria-pressed', picked.includes(String(weekday)) ? 'true' : 'false');
            button.addEventListener('click', (event) => filter.toggle('w', String(weekday), event));
            th.appendChild(button);
            tr.appendChild(th);
            if (picked.length && !picked.includes(String(weekday))) tr.classList.add('is-dimmed');
            hours.forEach((hour, index) => {
                const count = day[hour];
                const td = tr.insertCell();
                td.textContent = count || '';
                if (count) td.className = `is-seq-${1 + breaks.filter((edge) => count > edge).length}`;
                td.title = `${nameOf('w', weekday)}, ${hour}h: ${fmt.int(count)} chamado(s)`;
                if (count) {
                    heatPoints.push({ weekday, hour, label: `${nameOf('w', weekday)}, ${hour}h`, count });
                    td.setAttribute('data-mx-index', heatPoints.length - 1);
                    if (drillHint) td.title += `\n${drillHint}`;
                }
                td.style.animation = L.reduceMotion ? 'none' : `mx-pop .45s cubic-bezier(.2,.8,.2,1) ${Math.min((weekday * 12 + index) * 6, 600)}ms both`;
            });
        });
        heatTable = {
            columns: [
                { key: 'day', label: 'Dia' },
                ...hours.map((hour) => ({ key: `h${hour}`, label: `${hour}h`, type: 'int', total: true })),
                { key: 'total', label: 'Total', type: 'int', total: true, strong: true },
            ],
            rows: heatRows,
        };
    }

    // Protocolo e assunto vêm sob demanda (só dos chamados mostrados), guardados aqui.
    const details = new Map();
    let detailsRequest = 0;
    function fillDetails(tr, row) {
        const info = details.get(String(row.id));
        const protocol = tr.cells[0];
        const title = tr.cells[1];
        if (!info) return;
        protocol.replaceChildren();
        if (info.code) {
            const link = document.createElement('a');
            link.href = data.detail_url.replace('__code__', encodeURIComponent(info.code));
            link.textContent = info.code;
            protocol.appendChild(link);
        } else {
            protocol.textContent = `#${row.id}`;
        }
        title.textContent = info.title || '(sem assunto)';
        title.title = info.title || '';
    }
    async function loadDetails(shown, rowsByTr) {
        const missing = shown.filter((row) => !details.has(String(row.id))).map((row) => row.id);
        if (!missing.length) return;
        const request = ++detailsRequest;
        try {
            const response = await fetch(`${data.details_url}?ids=${missing.join(',')}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
            if (!response.ok) throw new Error(String(response.status));
            const payload = await response.json();
            Object.entries(payload.tickets || {}).forEach(([id, info]) => details.set(id, info));
        } catch (error) {
            missing.forEach((id) => { if (!details.has(String(id))) details.set(String(id), { code: '', title: '(não foi possível carregar)' }); });
        }
        if (request !== detailsRequest) return;
        rowsByTr.forEach(([tr, row]) => fillDetails(tr, row));
    }

    function renderTable(list, period) {
        const body = el('ticketRows');
        body.replaceChildren();
        const shown = list.slice(0, 40);
        el('tableSub').textContent = list.length
            ? `${fmt.int(list.length)} chamado(s) abertos em ${period.label} passam pelos filtros · os ${fmt.int(shown.length)} mais recentes`
            : 'Nenhum chamado passa pelos filtros escolhidos.';
        const rowsByTr = [];
        tableTickets = list;
        shown.forEach((row, index) => {
            const tr = body.insertRow();
            tr.setAttribute('data-mx-index', index);
            if (drillHint) tr.title = drillHint;
            rowsByTr.push([tr, row]);
            tr.insertCell().textContent = `#${row.id}`;
            const title = tr.insertCell();
            title.textContent = '…';
            title.className = 'hd-table-title';
            tr.insertCell().textContent = nameOf('c', row.c);
            tr.insertCell().textContent = nameOf('g', row.g);
            const status = tr.insertCell();
            const badge = document.createElement('span');
            badge.className = `mx-badge ${STATUS_TONE[row.st] || ''}`.trim();
            badge.textContent = nameOf('st', row.st);
            status.appendChild(badge);
            tr.insertCell().textContent = nameOf('a', row.a);
            const opened = tr.insertCell();
            opened.className = 'is-num';
            opened.textContent = dateOf(row);
            const rating = tr.insertCell();
            rating.className = 'is-num';
            rating.textContent = row.r == null ? '–' : String(row.r);
            fillDetails(tr, row);
        });
        loadDetails(shown, rowsByTr);
    }

    // ------------------------------------------------------------------ desenho
    function render(state) {
        const period = periodOf(state);
        document.querySelectorAll('[data-period]').forEach((node) => { node.textContent = period.label; });
        el('kpiOpenedCompare').textContent = period.picked ? 'contra os mesmos meses do ano anterior' : 'contra os 12 meses anteriores';

        const scoped = subset(state);
        const inPeriod = (list) => list.filter((row) => period.keys.has(row.m));
        const opened = inPeriod(scoped);
        const openedBefore = scoped.filter((row) => period.previous.has(row.m));
        const resolved = scoped.filter((row) => row.cm != null && period.keys.has(row.cm));
        const backlog = scoped.filter((row) => openStatuses.has(row.st));

        // Indicadores
        L.count(el('kpiOpened'), opened.length, fmt.int);
        setDelta(el('kpiOpenedDelta'), openedBefore.length ? opened.length / openedBefore.length - 1 : null);
        L.count(el('kpiResolved'), resolved.length, fmt.int);
        el('kpiResolvedFoot').textContent = opened.length
            ? `${fmt.pct(resolved.length / opened.length, 0)} do volume aberto no período`
            : 'nenhum chamado aberto no período';
        L.count(el('kpiBacklog'), backlog.length, fmt.int);
        const late = backlog.filter((row) => row.sla === 'late').length;
        el('kpiBacklogFoot').textContent = `${fmt.int(backlog.filter((row) => row.st === 'pending').length)} sem atendente · `
            + `${fmt.int(backlog.filter((row) => row.st === 'waiting_requester').length)} aguardando · ${fmt.int(late)} atrasado(s)`;

        const withSla = resolved.filter((row) => row.sla === 'met' || row.sla === 'breached');
        const met = withSla.filter((row) => row.sla === 'met').length;
        L.count(el('kpiSla'), withSla.length ? met / withSla.length : null, (value) => fmt.pct(value, 0));
        el('kpiSlaFoot').textContent = withSla.length
            ? `${fmt.int(met)} de ${fmt.int(withSla.length)} resolvidos com SLA`
            : 'nenhum chamado com SLA resolvido no período';

        const firstTimes = opened.map((row) => row.fr).filter((value) => value != null);
        L.count(el('kpiFirst'), median(firstTimes), duration);
        el('kpiFirstFoot').textContent = firstTimes.length
            ? `em ${fmt.int(firstTimes.length)} chamado(s) com resposta registrada`
            : 'nenhuma resposta registrada no período';
        const resolutionTimes = resolved.map((row) => row.rt).filter((value) => value != null);
        L.count(el('kpiResolution'), median(resolutionTimes), duration);
        el('kpiResolutionFoot').textContent = resolutionTimes.length ? `média de ${duration(average(resolutionTimes))}` : 'nenhum chamado resolvido no período';

        const rated = resolved.filter((row) => row.r != null);
        const ratable = resolved.filter((row) => row.o !== 'sm');
        L.count(el('kpiRating'), average(rated.map((row) => row.r)), (value) => fmt.dec(value, 1));
        el('kpiRatingFoot').textContent = rated.length
            ? `${fmt.int(rated.length)} avaliação(ões) · ${fmt.pct(ratable.length ? rated.length / ratable.length : 0, 0)} dos resolvidos pelo portal`
            : 'nenhuma avaliação no período';

        const reopened = opened.filter((row) => row.ro).length;
        L.count(el('kpiReopen'), opened.length ? reopened / opened.length : null, (value) => fmt.pct(value, 1));
        el('kpiReopenFoot').textContent = `${fmt.int(reopened)} de ${fmt.int(opened.length)} chamado(s) abertos no período`;

        // Abertos × resolvidos (todas as dimensões, menos o mês)
        const keys = monthKeys();
        const openedByMonth = new Map();
        const resolvedByMonth = new Map();
        scoped.forEach((row) => {
            openedByMonth.set(row.m, (openedByMonth.get(row.m) || 0) + 1);
            if (row.cm != null) resolvedByMonth.set(row.cm, (resolvedByMonth.get(row.cm) || 0) + 1);
        });
        flowChart.update({
            labels: data.months.map((month) => month.label), keys,
            highlight: period.picked ? period.list.map((key) => keys.indexOf(key)).filter((index) => index >= 0) : null,
            series: [
                { name: 'Abertos', color: '--mx-cat-1', values: keys.map((key) => openedByMonth.get(key) || 0) },
                { name: 'Resolvidos', color: '--mx-cat-3', values: keys.map((key) => resolvedByMonth.get(key) || 0) },
            ],
        });

        // Fatias e rankings: cada um ignora o próprio filtro, para mostrar o que foi escolhido em destaque.
        statusDonut.update(donutItems('st', inPeriod(subset(state, 'st'))), state.st);
        channelDonut.update(donutItems('o', inPeriod(subset(state, 'o'))), state.o);
        slaDonut.update(donutItems('sla', inPeriod(subset(state, 'sla'))), state.sla);
        priorityDonut.update(donutItems('p', inPeriod(subset(state, 'p'))), state.p);
        companies.update(rankItems('c', inPeriod(subset(state, 'c'))), state.c);
        sectors.update(rankItems('s', inPeriod(subset(state, 's'))), state.s);
        groups.update(rankItems('g', inPeriod(subset(state, 'g'))), state.g);
        types.update(rankItems('t', inPeriod(subset(state, 't'))), state.t);

        const byAttendant = new Map();
        inPeriod(subset(state, 'a')).forEach((row) => {
            const key = String(row.a);
            const entry = byAttendant.get(key) || { total: 0, done: 0, times: [], ratings: [] };
            entry.total += 1;
            if (row.st === 'completed') {
                entry.done += 1;
                if (row.rt != null) entry.times.push(row.rt);
            }
            if (row.r != null) entry.ratings.push(row.r);
            byAttendant.set(key, entry);
        });
        attendants.update([...byAttendant.entries()].sort((a, b) => b[1].total - a[1].total).map(([key, entry]) => ({
            key, label: nameOf('a', key), value: entry.total,
            sub: `${fmt.int(entry.done)} resolvido(s) · resolução ${duration(median(entry.times))}`
                + (entry.ratings.length ? ` · nota ${fmt.dec(average(entry.ratings), 1)}` : ''),
        })), state.a);

        // Avaliações 1 a 10 (resolvidos no período)
        const ratingRows = subset(state, 'r').filter((row) => row.r != null && row.cm != null && period.keys.has(row.cm));
        const ratingCounts = countBy(ratingRows, 'r');
        const pickedRatings = L.chosen(state.r);
        ratingsChart.update({
            labels: RATING_KEYS, keys: RATING_KEYS,
            highlight: pickedRatings.length ? pickedRatings.map((key) => RATING_KEYS.indexOf(key)) : null,
            colors: RATING_KEYS.map((key) => (Number(key) >= 9 ? '--mx-cat-3' : Number(key) >= 7 ? '--mx-cat-4' : '--mx-cat-8')),
            series: [{ name: 'Avaliações', color: '--mx-cat-3', values: RATING_KEYS.map((key) => ratingCounts.get(key) || 0) }],
        });
        const promoters = ratingRows.filter((row) => row.r >= 9).length;
        const detractors = ratingRows.filter((row) => row.r <= 6).length;
        el('ratingsHint').textContent = ratingRows.length
            ? `Satisfeitos (9 e 10): ${fmt.pct(promoters / ratingRows.length, 0)} · insatisfeitos (1 a 6): ${fmt.pct(detractors / ratingRows.length, 0)} · saldo ${Math.round(((promoters - detractors) / ratingRows.length) * 100)} pontos`
            : 'Nenhuma avaliação no filtro. Concluído o chamado, o solicitante só abre outro depois de avaliar.';

        renderHeat(inPeriod(subset(state, 'w')), state.w);

        // Assistente de IA: período, empresa e setor (as outras dimensões são de chamado).
        const talks = conversations.filter((talk) => period.keys.has(talk.m) && L.pass(state, 'c', talk.c) && L.pass(state, 's', talk.s));
        const outcomes = countBy(talks, 'outcome');
        assistantDonut.update(Object.keys(data.assistant.outcomes).filter((key) => outcomes.get(key)).map((key) => ({
            key, label: data.assistant.outcomes[key], value: outcomes.get(key), color: data.colors.outcome[key] || '--mx-chart-3',
        })), null);
        const solvedByBot = outcomes.get('resolved') || 0;
        el('assistantHint').textContent = talks.length
            ? `${fmt.int(talks.length)} conversa(s) · ${fmt.pct(solvedByBot / talks.length, 0)} resolvidas sem abrir chamado · ${fmt.int(outcomes.get('ticket') || 0)} viraram chamado`
            : 'Nenhuma conversa com o assistente neste filtro. O registro das conversas começa com esta versão.';

        // Base de conhecimento (vale o mês)
        const resolvedEntries = knowledge.filter((entry) => entry.resolved).length;
        L.count(el('kbTotal'), knowledge.length, fmt.int);
        el('kbTotalFoot').textContent = `${fmt.int(resolvedEntries)} marcados como resolvidos (o assistente só usa estes)`;
        const newEntries = knowledge.filter((entry) => period.keys.has(entry.m));
        L.count(el('kbNew'), newEntries.length, fmt.int);
        el('kbNewFoot').textContent = `${fmt.int(newEntries.filter((entry) => entry.documented).length)} vieram de chamados`;
        const documented = resolved.filter((row) => row.kb).length;
        L.count(el('kbDocumented'), documented, fmt.int);
        el('kbDocumentedFoot').textContent = resolved.length ? `${fmt.pct(documented / resolved.length, 1)} dos resolvidos no filtro` : 'nenhum resolvido no filtro';
        L.count(el('kbDeflected'), solvedByBot, fmt.int);
        el('kbDeflectedFoot').textContent = 'conversas que acabaram sem chamado';
        const perCategory = countBy(knowledge, 'category');
        const newPerCategory = countBy(newEntries, 'category');
        knowledgeRank.update([...perCategory.entries()].sort((a, b) => b[1] - a[1]).map(([key, value]) => ({
            key, label: data.knowledge.categories[key] || 'Sem categoria', value,
            sub: newPerCategory.get(key) ? `${fmt.int(newPerCategory.get(key))} novo(s) em ${period.label}` : '',
        })), null);

        renderTable(opened, period);
    }

    // ------------------------------------------------------------------ cards de indicador
    // O card inteiro abre os chamados por trás do número: as mesmas listas que o render conta,
    // com os filtros da página.
    if (window.MXDrill) {
        const lists = () => {
            const state = filter.get();
            const period = periodOf(state);
            const scoped = subset(state);
            return {
                period,
                opened: scoped.filter((row) => period.keys.has(row.m)),
                before: scoped.filter((row) => period.previous.has(row.m)),
                resolved: scoped.filter((row) => row.cm != null && period.keys.has(row.cm)),
                backlog: scoped.filter((row) => openStatuses.has(row.st)),
            };
        };
        const kpi = (id, spec) => { const card = el(id) && el(id).closest('.mx-card'); if (card) window.MXDrill.kpi(card, spec); };
        const lead = (fact) => (result) => { result.summary.unshift(fact); return result; };
        kpi('kpiOpened', {
            drill: () => {
                const { period, opened, before } = lists();
                const change = before.length ? opened.length / before.length - 1 : null;
                return ticketsResult(`Chamados abertos · ${period.label}`, 'Chamados abertos no período e no período de comparação', opened,
                    [{ key: 'before', label: period.picked ? 'Mesmos meses do ano anterior' : '12 meses anteriores', list: before }], 'Abertos no período')
                    .then((result) => { if (change != null) { result.summary[0].note = `${fmt.signedPct(change)} contra ${period.picked ? 'os mesmos meses do ano anterior' : 'os 12 meses anteriores'}`; } return result; });
            },
            canDrill: () => lists().opened.length > 0,
        });
        kpi('kpiResolved', {
            drill: () => { const { period, resolved } = lists(); return ticketsResult(`Resolvidos · ${period.label}`, 'Chamados concluídos no período', resolved, [], 'Resolvidos no período'); },
            canDrill: () => lists().resolved.length > 0,
        });
        kpi('kpiBacklog', {
            drill: () => {
                const { backlog } = lists();
                return ticketsResult('Em aberto agora', 'Chamados ainda não concluídos (vale para qualquer mês de abertura)', backlog, [
                    { key: 'pending', label: 'Sem atendente', list: backlog.filter((row) => row.st === 'pending') },
                    { key: 'late', label: 'Atrasados', list: backlog.filter((row) => row.sla === 'late') },
                ], 'Em aberto');
            },
            canDrill: () => lists().backlog.length > 0,
        });
        kpi('kpiSla', {
            drill: () => {
                const { period, resolved } = lists();
                const withSla = resolved.filter((row) => row.sla === 'met' || row.sla === 'breached');
                return ticketsResult(`SLA de resolução · ${period.label}`, 'Chamados resolvidos no período com SLA medido', withSla,
                    [{ key: 'breached', label: 'Fora do SLA', list: withSla.filter((row) => row.sla === 'breached') }], 'Resolvidos com SLA');
            },
            canDrill: () => lists().resolved.some((row) => row.sla === 'met' || row.sla === 'breached'),
        });
        kpi('kpiFirst', {
            drill: () => {
                const { period, opened } = lists();
                const answered = opened.filter((row) => row.fr != null);
                return ticketsResult(`1ª resposta · ${period.label}`, 'Chamados abertos no período com a primeira resposta registrada', answered, [], 'Com resposta')
                    .then(lead({ label: '1ª resposta (mediana)', value: duration(median(answered.map((row) => row.fr))), note: `média de ${duration(average(answered.map((row) => row.fr)))}` }));
            },
            canDrill: () => lists().opened.some((row) => row.fr != null),
        });
        kpi('kpiResolution', {
            drill: () => {
                const { period, resolved } = lists();
                const timed = resolved.filter((row) => row.rt != null);
                return ticketsResult(`Tempo de resolução · ${period.label}`, 'Chamados resolvidos no período', timed, [], 'Resolvidos')
                    .then(lead({ label: 'Resolução (mediana)', value: duration(median(timed.map((row) => row.rt))), note: `média de ${duration(average(timed.map((row) => row.rt)))}` }));
            },
            canDrill: () => lists().resolved.some((row) => row.rt != null),
        });
        kpi('kpiRating', {
            drill: () => {
                const { period, resolved } = lists();
                const rated = resolved.filter((row) => row.r != null);
                return ticketsResult(`Satisfação · ${period.label}`, 'Chamados resolvidos no período com a nota do solicitante', rated, [
                    { key: 'low', label: 'Notas de 1 a 6', list: rated.filter((row) => row.r <= 6) },
                ], 'Avaliados');
            },
            canDrill: () => lists().resolved.some((row) => row.r != null),
        });
        kpi('kpiReopen', {
            drill: () => {
                const { period, opened } = lists();
                const reopened = opened.filter((row) => row.ro);
                return ticketsResult(`Reabertos · ${period.label}`, 'Chamados abertos no período que voltaram a ser abertos', reopened, [], 'Reabertos')
                    .then(lead({ label: 'Reabertos', value: fmt.pct(opened.length ? reopened.length / opened.length : null, 1), note: `${fmt.int(reopened.length)} de ${fmt.int(opened.length)} abertos no período` }));
            },
            canDrill: () => lists().opened.some((row) => row.ro),
        });

        // Base de conhecimento: cada número do card é um ponto.
        const facts = el('kbTotal') && el('kbTotal').closest('.hd-facts');
        if (facts) {
            const items = [...facts.querySelectorAll('.mx-fact')];
            items.forEach((item, index) => { item.setAttribute('data-mx-index', index); item.title = drillHint; });
            const entriesResult = (title, subtitle, entries) => ({
                title, subtitle,
                summary: [
                    { label: 'Registros', value: fmt.int(entries.length) },
                    { label: 'Resolvidos', value: fmt.int(entries.filter((entry) => entry.resolved).length), note: 'o assistente só usa estes' },
                    { label: 'Vieram de chamados', value: fmt.int(entries.filter((entry) => entry.documented).length) },
                ],
                sections: [{
                    key: 'entries', label: 'Registros', groupBy: ['category', 'month', 'origin', 'resolved'], empty: 'Nenhum registro.',
                    columns: [{ key: 'category', label: 'Categoria' }, { key: 'month', label: 'Incluído em' }, { key: 'origin', label: 'Origem' }, { key: 'resolved', label: 'Situação' }],
                    rows: entries.slice().sort((a, b) => b.m - a.m).map((entry) => ({
                        category: data.knowledge.categories[entry.category] || 'Sem categoria', month: L.monthLabel(entry.m),
                        origin: entry.documented ? 'Documentação de chamado' : 'Cadastro direto', resolved: entry.resolved ? 'Resolvido (o assistente usa)' : 'Não resolvido',
                    })),
                }],
            });
            const points = [
                { label: 'Registros na base', drill: () => entriesResult('Registros na base', 'Toda a base de conhecimento · vale para todos os filtros', knowledge), can: () => knowledge.length > 0 },
                { label: 'Novos no período', drill: () => { const period = periodOf(filter.get()); return entriesResult(`Novos em ${period.label}`, 'Registros incluídos no período', knowledge.filter((entry) => period.keys.has(entry.m))); }, can: () => { const period = periodOf(filter.get()); return knowledge.some((entry) => period.keys.has(entry.m)); } },
                { label: 'Chamados documentados', drill: () => { const { period, resolved } = lists(); return ticketsResult(`Chamados documentados · ${period.label}`, 'Chamados resolvidos no período que viraram registro da base', resolved.filter((row) => row.kb), [], 'Documentados'); }, can: () => lists().resolved.some((row) => row.kb) },
                { label: 'Resolvidos pela conversa', drill: () => assistantDrill({ key: 'resolved', label: 'Resolvidas pela conversa' }), can: () => { const state = filter.get(); const period = periodOf(state); return conversations.some((talk) => talk.outcome === 'resolved' && period.keys.has(talk.m) && L.pass(state, 'c', talk.c) && L.pass(state, 's', talk.s)); } },
            ];
            window.MXDrill.attach(facts, {
                name: 'Base de conhecimento', tableTitle: 'Números da base',
                label: (index) => (points[index] ? points[index].label : ''),
                table: () => ({
                    columns: [{ key: 'label', label: 'Número' }, { key: 'value', label: 'Valor', strong: true }, { key: 'note', label: 'Detalhe', wrap: true }],
                    rows: items.map((item, index) => ({ label: points[index].label, value: (item.querySelector('strong') || {}).textContent, note: (item.querySelector('small') || {}).textContent, __index: index })),
                }),
                drill: (index) => points[index].drill(),
                canDrill: (index) => !!points[index] && points[index].can(),
            });
        }
    }

    filter.subscribe(render);
    render(filter.get());

    // Ao vivo: relê a base a cada 2 minutos enquanto a aba está visível.
    const liveToggle = el('mxLive');
    if (liveToggle) {
        L.live({
            url: `${window.location.pathname}?formato=json`, every: 120000, toggle: '#mxLive', stamp: '#mxStamp',
            onData: (next) => { if (next && Array.isArray(next.rows)) { load(next); render(filter.get()); } },
        });
        liveToggle.addEventListener('change', () => {
            const box = liveToggle.closest('.mx-live-status');
            if (box) box.classList.toggle('is-off', !liveToggle.checked);
        });
    }
})();
