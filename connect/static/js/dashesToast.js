/* ==========================================================================
   Toast das telas do Dashes — substitui window.alert() nos caminhos de erro
   de fetch (romaneio_bi.html, travel_bi.html, it_bi.html, customer_dna.html,
   dashes_decision_center.html). `window.alert()` trava a aba inteira até
   alguém clicar OK; um toast avisa sem interromper o que a pessoa estava
   fazendo, e some sozinho.

   CSS em dashes.css (`.dashes-toast*`) — carregado em toda tela do Dashes,
   então este script não depende do CSS de nenhum painel específico.
   ========================================================================== */
(function () {
    var AUTO_DISMISS_MS = 6000;

    function stack() {
        var el = document.getElementById('dashesToastStack');
        if (!el) {
            el = document.createElement('div');
            el.id = 'dashesToastStack';
            el.className = 'dashes-toast-stack';
            document.body.appendChild(el);
        }
        return el;
    }

    window.dashesToast = function (message, type) {
        if (!message) { return; }
        var host = stack();
        var toast = document.createElement('div');
        toast.className = 'dashes-toast' + (type === 'error' ? ' is-error' : type === 'success' ? ' is-success' : '');
        toast.textContent = message;
        host.appendChild(toast);

        // Duas etapas (criar sem a classe, depois adicionar) para o navegador
        // animar a entrada em vez de já nascer no estado final.
        requestAnimationFrame(function () { toast.classList.add('is-visible'); });

        var remove = function () {
            toast.classList.remove('is-visible');
            toast.addEventListener('transitionend', function () { toast.remove(); }, { once: true });
            // Se a aba estiver em segundo plano, `transitionend` pode nunca
            // disparar - garante que o elemento não fica preso no DOM.
            setTimeout(function () { toast.remove(); }, 500);
        };
        var timer = setTimeout(remove, AUTO_DISMISS_MS);
        toast.addEventListener('click', function () { clearTimeout(timer); remove(); });
    };
}());
