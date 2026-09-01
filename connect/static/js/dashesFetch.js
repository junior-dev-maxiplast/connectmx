/* ==========================================================================
   Leitura de resposta JSON nas telas do Dashes.

   `response.json()` cru é uma armadilha: quando a requisição é redirecionada, o
   que chega não é JSON, é a página de login ou a do painel — HTML começando com
   "<!DOCTYPE". O navegador então mostra

       Unexpected token '<', " <!DOCTYPE "... is not valid JSON

   que não diz nada a quem está usando a tela e manda a TI procurar bug de
   parsing onde o problema é sessão ou permissão. `fetch` segue o redirect
   sozinho e devolve 200, então nem `response.ok` denuncia: quem denuncia é o
   Content-Type.
   ========================================================================== */
(function () {
    var SESSION_MESSAGE =
        'Sua sessão expirou ou este acesso não está liberado para o seu usuário. ' +
        'Recarregue a página e entre novamente; se continuar, avise a TI.';

    window.dashesReadJson = function (response) {
        var type = response.headers.get('Content-Type') || '';

        if (type.indexOf('application/json') !== -1) {
            return response.json();
        }

        // Redirecionado para uma tela HTML: login expirado ou rota barrada para
        // este usuário. São as duas causas reais, e o texto precisa dizer isso.
        if (response.redirected || response.status === 401 || response.status === 403) {
            return Promise.reject(new Error(SESSION_MESSAGE));
        }

        return Promise.reject(new Error(
            'O servidor respondeu ' + response.status + ' sem JSON nesta requisição. ' +
            'Recarregue a página; se continuar, avise a TI.'
        ));
    };
}());
