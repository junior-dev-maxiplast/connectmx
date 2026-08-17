# OpenAI no DNA do Cliente

## Configuração recomendada

1. Copie `.env.example` para `.env`.
2. Defina `DJANGO_SECRET_KEY` com uma chave estável.
3. Preencha `OPENAI_API_KEY`.
4. Altere `CONNECTMX_OPENAI_ENABLED` para `true`.
5. Reinicie o ConnectMX.

As variáveis de ambiente têm prioridade sobre os valores cadastrados em **Sistema → Configurações**. A chave também pode ser cadastrada nessa página; nesse caso, ela é criptografada com uma chave derivada de `DJANGO_SECRET_KEY` e nunca volta para o navegador.

## Fluxo

O processamento foi separado em duas ações:

1. **Gerar indicadores** consulta o ERP Senior, recalcula os indicadores determinísticos e armazena o payload pelo fingerprint dos dados.
2. **Pedir análise à IA** reutiliza o payload armazenado, envia à Responses API, exige resposta compatível com o JSON Schema e persiste resposta, modelo, identificador, consumo, horários e erros.

Nenhuma chamada é feita ao abrir, pesquisar ou calcular indicadores. O consumo da OpenAI ocorre somente quando o gestor solicita separadamente a análise.
