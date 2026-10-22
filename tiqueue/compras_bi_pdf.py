"""
PDF do BI de Compras.

Mesma linguagem visual dos demais relatórios do Dashes (DNA do Cliente, BI do
TI, BI de Viagens), com o conteúdo de compras: a aba escolhida decide se o
relatório mostra prazo (pagamento/entrega) ou variação de preço.
"""

from xml.sax.saxutils import escape

from django.utils import timezone

from .bi_pdf import BiPdf, _text


def build_compras_bi_pdf(dashboard, snapshot):
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, TableStyle

    pdf = BiPdf()
    styles, paragraph, label_value, table = pdf.styles, pdf.paragraph, pdf.label_value, pdf.table
    green, green_soft, blue_soft = pdf.green, pdf.green_soft, pdf.blue_soft

    metrics = dashboard["metrics"]
    scope = dashboard["scope"]
    view_key = scope["view"]["key"]
    ai = snapshot.ai_response or {}
    generated_at = timezone.localtime()

    story = [
        Paragraph("CONNECTMX DASHES / BI DE COMPRAS", styles["BiSubtitle"]),
        Paragraph(scope["view"]["label"], styles["BiTitle"]),
        Paragraph(
            f"{escape(_text(scope['period']['full_label']))} | {escape(_text(scope['fornecedor']['label']))}"
            f" | {escape(_text(scope['branch']['label']))} | {escape(_text(scope['familia']['label']))}"
            f" | Gerado em {generated_at.strftime('%d/%m/%Y %H:%M')}",
            styles["BiMuted"],
        ),
        Spacer(1, 9),
    ]

    if view_key in ("pagamento", "entrega"):
        resume = table(
            [[
                label_value("Fornecedores", metrics.get("fornecedores_display")),
                label_value("Notas", metrics.get("notas_display")),
                label_value("Ordens", metrics.get("ordens_display")),
                label_value("Produtos", metrics.get("produtos_display")),
            ]],
            [44 * mm] * 4,
            background=green_soft,
        )
        resume.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 2, green)]))
        story.extend([resume, Spacer(1, 9)])

        label = "pagamento" if view_key == "pagamento" else "entrega"
        meta_line = (
            f" | meta contratada {metrics.get('prazo_meta_display')}"
            if view_key == "pagamento" else ""
        )
        story.extend([
            Paragraph("Prazo", styles["BiSection"]),
            table(
                [[
                    label_value(f"Prazo real de {label}", f"{metrics.get('prazo_medio_display')}{meta_line}"),
                    label_value("Cobertura da média", f"{metrics.get('prazo_coverage_pct')}% das linhas com prazo apurado"),
                ]],
                [88 * mm, 88 * mm],
            ),
            Spacer(1, 4),
            Paragraph(
                f"{metrics.get('inconsistencias')} linha(s) ({metrics.get('inconsistencias_pct')}%) com data "
                f"anterior à geração da ordem — tratadas como inconsistência de cadastro e fora da média.",
                styles["BiMuted"],
            ),
            Spacer(1, 9),
        ])

        for section_title, rows_key in (("Por família", "familias"), ("Por origem", "origens")):
            rows_data = dashboard.get(rows_key) or []
            if not rows_data:
                continue
            story.append(Paragraph(section_title, styles["BiSection"]))
            rows = [[paragraph("Rótulo", "BiHead"), paragraph("Linhas", "BiHead"), paragraph("Prazo médio", "BiHead")]]
            for item in rows_data:
                rows.append([
                    paragraph(item.get("label"), "BiSmall"),
                    paragraph(item.get("linhas"), "BiSmall"),
                    paragraph(item.get("prazo_medio_display"), "BiSmall"),
                ])
            story.extend([table(rows, [96 * mm, 40 * mm, 40 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

        fornecedores = dashboard.get("fornecedores") or []
        if fornecedores:
            story.append(Paragraph("Fornecedores", styles["BiSection"]))
            rows = [[paragraph("Fornecedor", "BiHead"), paragraph("Linhas", "BiHead"),
                     paragraph("Ordens", "BiHead"), paragraph("Prazo médio", "BiHead")]]
            for item in fornecedores:
                rows.append([
                    paragraph(item.get("name"), "BiSmall"),
                    paragraph(item.get("linhas"), "BiSmall"),
                    paragraph(item.get("ordens"), "BiSmall"),
                    paragraph(item.get("prazo_medio_display"), "BiSmall"),
                ])
            story.extend([table(rows, [76 * mm, 34 * mm, 34 * mm, 32 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

        detalhe = dashboard.get("detalhe") or []
        if detalhe:
            story.append(Paragraph("Detalhe", styles["BiSection"]))
            rows = [[paragraph("Ordem", "BiHead"), paragraph("Nota", "BiHead"), paragraph("Fornecedor", "BiHead"),
                     paragraph("Produto", "BiHead"), paragraph("Geração", "BiHead"), paragraph("Dias", "BiHead")]]
            for item in detalhe[:20]:
                dias = item.get("dias_pagamento") if view_key == "pagamento" else item.get("dias_entrega")
                rows.append([
                    paragraph(item.get("ordem"), "BiSmall"),
                    paragraph(item.get("nota"), "BiSmall"),
                    paragraph(item.get("fornecedor"), "BiSmall"),
                    paragraph(item.get("produto"), "BiSmall"),
                    paragraph(item.get("geracao"), "BiSmall"),
                    paragraph(dias, "BiSmall"),
                ])
            story.extend([
                table(rows, [20 * mm, 20 * mm, 40 * mm, 46 * mm, 24 * mm, 16 * mm], header=True, repeat_rows=1, font_size=6.8),
                Spacer(1, 8),
            ])
    else:
        resume = table(
            [[
                label_value("Qtde. cotada", metrics.get("qtd_cotada_display")),
                label_value("R$ total cotado", metrics.get("total_cotado_display")),
                label_value("Fornecedores", metrics.get("fornecedores_display")),
                label_value("Produtos", metrics.get("produtos_display")),
            ]],
            [44 * mm] * 4,
            background=green_soft,
        )
        resume.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 2, green)]))
        story.extend([resume, Spacer(1, 9)])

        story.extend([
            Paragraph("Preço unitário", styles["BiSection"]),
            table(
                [[
                    label_value("Mínimo", metrics.get("preco_min_display")),
                    label_value("Médio", metrics.get("preco_medio_display")),
                    label_value("Máximo", metrics.get("preco_max_display")),
                ]],
                [58.6 * mm] * 3,
                background=blue_soft,
            ),
            Spacer(1, 9),
        ])

        for section_title, rows_key in (("Por fornecedor", "fornecedores"), ("Por produto", "produtos")):
            rows_data = dashboard.get(rows_key) or []
            if not rows_data:
                continue
            story.append(Paragraph(section_title, styles["BiSection"]))
            rows = [[paragraph("Nome", "BiHead"), paragraph("Quantidade", "BiHead"),
                     paragraph("Total", "BiHead"), paragraph("Preço médio", "BiHead")]]
            for item in rows_data:
                rows.append([
                    paragraph(item.get("name"), "BiSmall"),
                    paragraph(item.get("quantidade"), "BiSmall"),
                    paragraph(item.get("total_display"), "BiSmall"),
                    paragraph(item.get("preco_medio_display"), "BiSmall"),
                ])
            story.extend([table(rows, [76 * mm, 34 * mm, 38 * mm, 28 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

        detalhe = dashboard.get("detalhe") or []
        if detalhe:
            story.append(Paragraph("Detalhe", styles["BiSection"]))
            rows = [[paragraph("Ordem", "BiHead"), paragraph("Fornecedor", "BiHead"), paragraph("Produto", "BiHead"),
                     paragraph("Qtde.", "BiHead"), paragraph("Unitário", "BiHead"), paragraph("Total", "BiHead")]]
            for item in detalhe[:20]:
                rows.append([
                    paragraph(item.get("ordem"), "BiSmall"),
                    paragraph(item.get("fornecedor"), "BiSmall"),
                    paragraph(item.get("produto"), "BiSmall"),
                    paragraph(item.get("quantidade"), "BiSmall"),
                    paragraph(item.get("preco_unitario_display"), "BiSmall"),
                    paragraph(item.get("total_display"), "BiSmall"),
                ])
            story.extend([
                table(rows, [20 * mm, 40 * mm, 46 * mm, 20 * mm, 24 * mm, 26 * mm], header=True, repeat_rows=1, font_size=6.8),
                Spacer(1, 8),
            ])

    pdf.ai_section(story, ai, snapshot, blue_tones=("cost", "supplier"))

    return pdf.build(story, "BI de Compras", snapshot.scope_label)
