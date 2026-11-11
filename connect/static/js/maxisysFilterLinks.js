/* Filtro por clique nas telas montadas no servidor (padrão MaxiSys).

   Um elemento com data-mx-filter="chave" e data-mx-value="valor" troca o parâmetro da página:
   - clique: só esse valor (clicar de novo no único escolhido tira o filtro);
   - Ctrl (⌘ no Mac) + clique: soma ou tira o valor; a página abre ao soltar o Ctrl, então dá
     para marcar vários de uma vez (marcados com borda tracejada até lá).
   `data-mx-drop="periodo"` tira outros parâmetros junto (ex.: meses escolhidos trocam o período).
   O endereço guarda tudo (?rep=10004,10010), então o link compartilhado abre igual. */
(function () {
    const pending = new Map();
    let dropping = new Set();

    const read = (url, key) => (url.searchParams.get(key) || '').split(',').map((value) => value.trim()).filter(Boolean);
    const additive = (event) => !!(event && (event.ctrlKey || event.metaKey));

    function open(changes) {
        const url = new URL(window.location.href);
        changes.forEach((list, key) => {
            if (list.length) url.searchParams.set(key, list.join(','));
            else url.searchParams.delete(key);
        });
        dropping.forEach((key) => url.searchParams.delete(key));
        document.documentElement.classList.add('mx-filter-loading');
        window.location.href = url.toString();
    }

    function choose(target, event) {
        const key = target.dataset.mxFilter;
        const value = String(target.dataset.mxValue);
        (target.dataset.mxDrop || '').split(',').filter(Boolean).forEach((name) => dropping.add(name));
        const url = new URL(window.location.href);
        const current = pending.has(key) ? pending.get(key) : read(url, key);
        // `data-mx-single`: a tela só filtra um valor por vez (o Ctrl vira clique normal).
        if (additive(event) && !target.hasAttribute('data-mx-single')) {
            const next = current.includes(value) ? current.filter((item) => item !== value) : [...current, value];
            pending.set(key, next);
            document.querySelectorAll(`[data-mx-filter="${CSS.escape(key)}"]`).forEach((node) => {
                node.classList.toggle('is-pending', next.includes(String(node.dataset.mxValue)) !== node.classList.contains('is-filter-active'));
            });
            return;
        }
        pending.clear();
        open(new Map([[key, current.length === 1 && current[0] === value ? [] : [value]]]));
    }

    document.addEventListener('click', (event) => {
        const target = event.target.closest('[data-mx-filter]');
        if (!target) return;
        // Links de verdade dentro da linha (ex.: abrir a ficha) seguem o próprio caminho.
        const link = event.target.closest('a[href]');
        if (link && !link.hasAttribute('data-mx-filter')) return;
        event.preventDefault();
        choose(target, event);
    });
    const commit = () => { if (pending.size) { const changes = new Map(pending); pending.clear(); open(changes); } };
    window.addEventListener('keyup', (event) => { if (event.key === 'Control' || event.key === 'Meta') commit(); });
    window.addEventListener('blur', commit);

    // Marca os elementos já filtrados (o servidor só manda o endereço). Use <button> nos alvos:
    // o teclado (Enter/Espaço) já vira clique.
    const url = new URL(window.location.href);
    document.querySelectorAll('[data-mx-filter]').forEach((node) => {
        const active = read(url, node.dataset.mxFilter).includes(String(node.dataset.mxValue));
        node.classList.toggle('is-filter-active', active);
        if (node.tagName === 'BUTTON') node.setAttribute('aria-pressed', active ? 'true' : 'false');
        if (!node.title) node.title = 'Clique para filtrar a página · Ctrl + clique soma à seleção';
    });
})();
