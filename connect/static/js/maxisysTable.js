/* Tabela do padrão MaxiSys com ordenação, busca e paginação no navegador.

   <table data-mx-table data-page-size="50">
     <th data-sort="profit" data-type="num">Lucro</th>       (clique ordena)
     <tr data-search="texto para busca" data-profit="1234.5"> (um data-* por coluna ordenável)
   <input data-mx-table-search="idDaTabela">                 (busca)
   <div data-mx-table-pager="idDaTabela">                    (paginação)
*/
(function () {
    function mount(table) {
        const body = table.tBodies[0];
        if (!body) return;
        const rows = Array.from(body.rows);
        const pageSize = Number(table.dataset.pageSize) || 50;
        const pager = document.querySelector(`[data-mx-table-pager="${table.id}"]`);
        const search = document.querySelector(`[data-mx-table-search="${table.id}"]`);
        let filtered = rows;
        let page = 0;

        const value = (row, key, type) => {
            const raw = row.dataset[key];
            if (type === 'num') {
                const number = Number(raw);
                return raw === undefined || raw === '' || Number.isNaN(number) ? -Infinity : number;
            }
            return (raw || '').toString();
        };

        function render() {
            const pages = Math.max(Math.ceil(filtered.length / pageSize), 1);
            page = Math.min(page, pages - 1);
            const start = page * pageSize;
            const visible = new Set(filtered.slice(start, start + pageSize));
            const kept = new Set(filtered);
            rows.forEach((row) => { row.hidden = !visible.has(row); });
            filtered.forEach((row) => body.appendChild(row));
            rows.filter((row) => !kept.has(row)).forEach((row) => body.appendChild(row));
            if (!pager) return;
            pager.replaceChildren();
            const info = document.createElement('span');
            info.textContent = filtered.length
                ? `Mostrando ${start + 1}–${Math.min(start + pageSize, filtered.length)} de ${filtered.length}`
                : 'Nenhum cliente com esse filtro';
            pager.appendChild(info);
            if (pages > 1) {
                const nav = document.createElement('span');
                nav.className = 'mx-toolbar-group';
                [['Anterior', -1], ['Próxima', 1]].forEach(([label, delta]) => {
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'mx-btn mx-btn-ghost';
                    button.textContent = label;
                    button.disabled = (delta < 0 && page === 0) || (delta > 0 && page >= pages - 1);
                    button.addEventListener('click', () => { page += delta; render(); table.scrollIntoView({ block: 'nearest' }); });
                    nav.appendChild(button);
                });
                pager.appendChild(nav);
            }
        }

        table.querySelectorAll('th[data-sort]').forEach((header) => {
            header.tabIndex = 0;
            const sort = () => {
                const key = header.dataset.sort;
                const type = header.dataset.type || 'text';
                const descending = header.getAttribute('aria-sort') !== 'descending';
                table.querySelectorAll('th[data-sort]').forEach((other) => other.removeAttribute('aria-sort'));
                header.setAttribute('aria-sort', descending ? 'descending' : 'ascending');
                filtered = filtered.slice().sort((a, b) => {
                    const left = value(a, key, type);
                    const right = value(b, key, type);
                    const order = type === 'num' ? left - right : left.localeCompare(right, 'pt-BR');
                    return descending ? -order : order;
                });
                page = 0;
                render();
            };
            header.addEventListener('click', sort);
            header.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); sort(); } });
        });

        if (search) {
            search.addEventListener('input', () => {
                const term = search.value.trim().toLowerCase();
                const current = table.querySelector('th[aria-sort]');
                filtered = rows.filter((row) => !term || (row.dataset.search || '').toLowerCase().includes(term));
                if (current) {
                    const key = current.dataset.sort;
                    const type = current.dataset.type || 'text';
                    const descending = current.getAttribute('aria-sort') === 'descending';
                    filtered.sort((a, b) => {
                        const order = type === 'num' ? value(a, key, type) - value(b, key, type) : value(a, key, type).localeCompare(value(b, key, type), 'pt-BR');
                        return descending ? -order : order;
                    });
                }
                page = 0;
                render();
            });
        }
        render();
    }

    document.querySelectorAll('table[data-mx-table]').forEach(mount);
    window.MXTable = { mount };
})();
