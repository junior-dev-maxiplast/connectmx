# Helpdesk do ConnectMX — mapa funcional

Revisão de 13/11/2026, feita no código (`tiqueue/views.py`, `models.py`, templates do portal),
na base local (SQLite de desenvolvimento) e na suíte de testes. Onde o resultado depende de
configuração do servidor, o texto diz o que vale **neste ambiente** e o que conferir em produção.

## Resumo

| Etapa | Situação | Observação |
|---|---|---|
| Acesso ao portal e cadastro de solicitantes | Funciona | Conta de solicitante ainda abre telas internas pela URL (ver pendências) |
| Abertura pelo formulário | Funciona | SLA, atendente padrão, campos extras, anexos, sugestões da base e de duplicados |
| Abertura por conversa com o assistente de IA | Funciona (IA ligada aqui) | Agora cada conversa deixa rastro e o chamado sai marcado como "conversa" |
| Roteamento automático por IA (N8N) | **Desligado neste ambiente** | Depende de `CONNECTMX_AI_ROUTING_WEBHOOK_URL` e `CONNECTMX_AI_ROUTING_TOKEN` |
| Triagem, assumir, transferir, responder, nota interna, horas | Funciona | Coberto pelos testes do fluxo do portal |
| Aguardar solicitante, retomar, concluir, cancelar, reabrir | Funciona | SLA pausa na espera e volta sozinho quando o solicitante responde |
| Avisos (sino e e-mail) | Sino funciona; e-mail **vai para o console aqui** | Em produção conferir `DJANGO_EMAIL_BACKEND` e `CONNECTMX_PUBLIC_URL` |
| Avaliação ao concluir (nota 1 a 10) | Funciona — **corrigido** | A nota não entrava no histórico nem avisava o atendente |
| Documentar chamado na base (com rascunho por IA) | Funciona | Agora também entra no histórico do chamado |
| Base de conhecimento (cadastro manual) | **Corrigido** | Não havia como editar registro; erro de validação sumia; solicitante podia escrever pela URL |
| MaxiBot | Funciona | É busca por palavras na base, não IA generativa |
| Painel do Helpdesk (Dashes) | **Novo** | Tela inicial do Dashes, lê a base do ConnectMX, com filtro cruzado |

## Etapa por etapa

### 1. Abertura
- **Formulário** (`/portal/chamados/abrir/`): grupo, tipo, prioridade com explicação de impacto,
  campos extras por grupo/tipo, anexos. Na abertura a política de SLA mais específica (tipo > grupo >
  prioridade) define os prazos de 1ª resposta e resolução; se a política tiver atendente padrão com
  "atribuir na abertura", o chamado já nasce assumido e vai para a fila pessoal dele.
- **Trava de avaliação**: quem tem chamado concluído sem nota não abre outro (GET e POST), exceto
  histórico importado do SM e atendentes.
- **Atendente abrindo em nome de alguém**: funciona; o chamado fica com o solicitante e o setor dele
  e agora sai marcado com o canal "Aberto pelo atendente".
- **Conversa com o assistente** (`/portal/chamados/abrir/conversa/`): a cada mensagem o sistema busca
  na base (registros **resolvidos** de categorias ativas e chamados concluídos) e a IA só pode sugerir
  solução a partir desses registros. Termina de três jeitos: resolve sem chamado, sugere solução, ou
  monta um resumo que pré-preenche o formulário (com os anexos da conversa).
  - Antes desta revisão nada disso ficava gravado (a conversa vive só na sessão): uma conversa
    resolvida pela IA não deixava sinal. Agora `PortalAssistantConversation` guarda turnos e desfecho
    (sem o texto) e o chamado que nasce do resumo fica com `opened_via = chat`.
- **Roteamento por IA (N8N)**: ao abrir, o ConnectMX chama o webhook configurado; o N8N lê o contexto e
  devolve área, tipo, prioridade e confiança, que são aplicados e registrados no histórico ("Roteamento
  IA"). Neste ambiente as variáveis estão vazias, então nada é chamado. Falha no webhook vira aviso no
  sino interno.

### 2. Atendimento e trâmites
- Entrada de chamados: abas (abertos, meus, não atribuídos, aguardando, resolvidos, todos), filtros,
  exportação CSV/XLSX, assumir um ou vários, aviso de possível duplicado.
- Conversa do chamado: resposta pública ou nota interna, respostas prontas sugeridas por tipo/grupo,
  apontamento de horas, transferência, "aguardando solicitante" (pausa o SLA), retomada, conclusão,
  cancelamento (atendente; o solicitante só enquanto ninguém assumiu), reabertura (atendente ou
  solicitante, com novo prazo de resolução).
- A 1ª resposta pública do atendente marca o cumprimento do SLA de resposta. Chamados vencidos viram
  aviso crítico no painel interno.
- Dados atuais da base local: 4 chamados com SLA de resolução vencido em aberto desde julho; só
  1 política de SLA, nenhuma resposta pronta e nenhum campo extra cadastrados.

### 3. Avaliação
- Só o solicitante avalia, só chamado concluído, nota de 1 a 10 com comentário opcional.
- O solicitante recebe aviso de conclusão no sino (e e-mail, se configurado).
- **Corrigido**: a avaliação agora entra no histórico do chamado (evento "Avaliação") e o atendente
  responsável recebe o aviso com a nota.

### 4. Base de conhecimento
- **Documentar chamado**: em chamado concluído o atendente descreve como resolveu; a IA lê o histórico
  inteiro e devolve o rascunho (título, problema, causa, solução, tags) para revisão. O registro nasce
  resolvido e ligado ao chamado (agora também anotado no histórico do chamado).
- **Cadastro manual** — três falhas corrigidas:
  1. não havia como editar um registro (a edição só existia num template que deixou de ser usado);
     agora a ficha do registro tem "Editar registro". Importante porque o assistente de IA **só usa
     registros marcados como Resolvido**;
  2. registro ou categoria com erro (campo obrigatório vazio, nome repetido) sumia sem mensagem;
     agora volta para a tela com o erro;
  3. conta de solicitante do portal conseguia gravar na base pela URL; agora só consulta.
- **MaxiBot**: busca por palavras com peso por campo; usa também registros não resolvidos (diferente
  do assistente do portal). Não usa IA generativa.

## Padrão visual

Todas as telas do helpdesk passaram a usar o menu lateral e a barra do MaxiSys, como nos Dashes
(`partials/helpdesk_sidebar.html` + `static/style/helpdesk.css`): portal do solicitante, abertura
(formulário e conversa), minhas demandas, conversa do chamado, entrada de chamados, base de
conhecimento, MaxiBot e parâmetros (empresas, solicitantes, campos, SLA, respostas). O menu muda
pelo papel: o solicitante vê só o que é dele; o atendente ganha fila, base e painel; o administrador,
os parâmetros. A abertura de chamado continua em modo foco (só "Voltar ao portal").

## Painel do Helpdesk (tela inicial do Dashes)

`/dashes/` abre o Painel do Helpdesk para quem tem o painel liberado (administradores e atendentes
ganham acesso na migração `0093`; os demais pela tela de permissões do Dashes). Lê só a base do
ConnectMX (portal + histórico do SM), 24 meses, e recarrega a cada 2 minutos.

- Indicadores: abertos (com comparação ao ano anterior), resolvidos, em aberto agora, SLA cumprido,
  1ª resposta e resolução (medianas), satisfação e reabertos.
- Gráficos: abertos × resolvidos por mês, situação, canal de abertura, SLA, prioridade, empresas,
  setores, áreas, atendentes, tipos, notas de 1 a 10, mapa de calor dia × hora, assistente de IA,
  base de conhecimento e a tabela dos chamados do filtro.
- **Filtro cruzado como no Power BI**: clicar em qualquer barra, fatia, ranking, nota, mês ou dia da
  semana filtra todo o resto; Ctrl + clique soma itens; os filtros viram etiquetas e ficam no
  endereço (link compartilhável).
- Limite dos dados: o histórico do SM chegou sem SLA e sem horário de 1ª resposta, então esses
  indicadores dependem dos chamados abertos no portal; o assistente só tem números a partir desta
  versão.

## Pendências recomendadas (não feitas nesta revisão)

1. Contas só de solicitante entram com `can_access_internal = True` e conseguem abrir telas internas
   pela URL (fila geral, projetos...). Vale bloquear no middleware.
2. Produção: conferir `DJANGO_EMAIL_BACKEND` (aqui o e-mail vai para o console) e
   `CONNECTMX_PUBLIC_URL` (sem ele o link do e-mail sai sem o endereço do servidor).
3. Decidir se o roteamento por IA (N8N) deve ser ligado e cadastrar mais políticas de SLA e
   respostas prontas — hoje há 1 política e nenhuma resposta pronta.
4. Login do Dashes: com sessão já aberta, o parâmetro `next` é seguido sem validar o endereço
   (redirecionamento aberto).
