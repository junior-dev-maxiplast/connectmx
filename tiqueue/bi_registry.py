"""
Catálogo dos dados dos painéis do Dashes — usado pelo item 4 da Central de
Decisão ("Pergunte aos dados", `data_qa.py`).

Só o que é seguro de reaproveitar de fora: rótulo, descrição em português e as
métricas-chave de cada fonte. Não guarda a função de carga (`load_X_dashboard`)
diretamente — um dict resolvido na importação do módulo travaria a referência
antes de qualquer patch de teste, o mesmo problema que `decision_center.py`
evita com `_load_dashboard`. Quem precisa carregar de verdade importa e chama
`load_X_dashboard` no momento do uso (ver `data_qa._load_and_payload`).
"""

BI_SOURCES = {
    "romaneios": {
        "label": "Romaneios (contagem de pallets)",
        "description": (
            "Produtividade da contagem de pallets por colaborador e etapa "
            "(separar, guardar, paletizar, carregar), a partir do "
            "USU_TCONROM do ERP Senior."
        ),
        "key_metrics": ["records", "volumes", "weight", "employees", "pallets", "missing_weight_pct"],
    },
    "viagens": {
        "label": "Viagens (frota e logística)",
        "description": (
            "Viagens da frota: quilometragem, litros, consumo e custo, a "
            "partir do USU_TCADVIA do ERP Senior."
        ),
        "key_metrics": ["trips", "finished", "open", "km_total", "liters_total", "km_coverage_pct"],
    },
    "ti-bi": {
        "label": "TI (helpdesk / SM)",
        "description": "Chamados do helpdesk: volume, SLA, backlog e avaliação, do MySQL do SM.",
        "key_metrics": ["tickets", "closed", "open", "backlog", "sla_valid_pct", "rating_display"],
    },
    # DNA de Cliente fica de fora: a consulta ali é sempre por cliente
    # (`load_customer_dna(customer_id, ...)`), então uma pergunta em
    # linguagem natural sobre um cliente específico precisaria primeiro
    # resolver o nome/código do cliente — um passo que esta v1 não cobre.
}


def describe_sources(slugs=None):
    """Lista enxuta pro seletor de fonte da pergunta.

    `slugs`, quando informado, restringe às fontes desse conjunto (os
    painéis que o usuário tem liberado) — sem isso, alguém sem acesso ao BI
    do TI poderia perguntar sobre chamados de qualquer jeito.
    """
    items = BI_SOURCES.items() if slugs is None else ((s, BI_SOURCES[s]) for s in slugs if s in BI_SOURCES)
    return [
        {"slug": slug, "label": source["label"], "description": source["description"]}
        for slug, source in items
    ]
