"""
PDF do BI de Compras.

Mesma linguagem visual dos demais relatórios do Dashes (DNA do Cliente, BI do
TI, BI de Viagens), com o conteúdo de compras: a aba escolhida decide se o
relatório mostra prazo (pagamento/entrega) ou variação de preço.
"""

from xml.sax.saxutils import escape

from django.utils import timezone


def _text(value, fallback="-"):
    if value is None or value == "":
        return fallback
    return str(value)


def build_compras_bi_pdf(dashboard, snapshot):
    import io

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    navy = colors.HexColor("#071821")
    navy_soft = colors.HexColor("#0d2732")
    green = colors.HexColor("#57c96d")
    green_soft = colors.HexColor("#e8f7ea")
    blue_soft = colors.HexColor("#edf5ff")
    amber_soft = colors.HexColor("#fff5df")
    red_soft = colors.HexColor("#fff0f0")
    border = colors.HexColor("#d9e4e8")
    muted = colors.HexColor("#647983")
    body_color = colors.HexColor("#263c46")

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BiTitle", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=20, leading=23, textColor=navy, spaceAfter=3))
    styles.add(ParagraphStyle(name="BiSubtitle", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, textColor=muted))
    styles.add(ParagraphStyle(name="BiSection", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=navy, spaceBefore=8, spaceAfter=7))
    styles.add(ParagraphStyle(name="BiBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=body_color))
    styles.add(ParagraphStyle(name="BiSmall", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.4, leading=9.4, textColor=body_color))
    styles.add(ParagraphStyle(name="BiMuted", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=muted))
    styles.add(ParagraphStyle(name="BiCardTitle", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=10.5, textColor=navy))
    styles.add(ParagraphStyle(name="BiHead", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=7.3, leading=8.5, textColor=colors.white))

    def paragraph(value, style="BiBody"):
        return Paragraph(escape(_text(value)), styles[style])

    def label_value(label, value, style="BiBody"):
        return Paragraph(f"<b>{escape(label)}</b><br/>{escape(_text(value))}", styles[style])

    def table(rows, widths, header=False, background=colors.white, repeat_rows=0, font_size=7.4):
        built = Table(rows, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
        commands = [
            ("BACKGROUND", (0, 0), (-1, -1), background),
            ("BOX", (0, 0), (-1, -1), .7, border),
            ("INNERGRID", (0, 0), (-1, -1), .45, border),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ]
        if header:
            commands.extend([
                ("BACKGROUND", (0, 0), (-1, 0), navy_soft),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ])
            for index in range(1, len(rows)):
                if index % 2 == 0:
                    commands.append(("BACKGROUND", (0, index), (-1, index), colors.HexColor("#f6f9fa")))
        built.setStyle(TableStyle(commands))
        return built

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

    if ai:
        story.append(Paragraph("Análise da IA", styles["BiSection"]))
        story.extend([
            table(
                [[label_value("Situação geral", _text(ai.get("health")).title()),
                  label_value("Modelo", f"{snapshot.ai_model or '-'} | {snapshot.ai_total_tokens} tokens")]],
                [116 * mm, 60 * mm],
                background=blue_soft,
            ),
            Spacer(1, 6),
            paragraph(ai.get("executive_summary")),
            Spacer(1, 6),
            table(
                [[label_value("Principal risco", ai.get("principal_risk")),
                  label_value("Principal oportunidade", ai.get("principal_opportunity"))]],
                [88 * mm, 88 * mm],
                background=amber_soft,
            ),
            Spacer(1, 8),
        ])

        tones = {"positive": green_soft, "attention": amber_soft, "risk": red_soft,
                 "cost": blue_soft, "supplier": blue_soft, "quality": amber_soft}
        for insight in ai.get("insights") or []:
            evidence = "; ".join(_text(item) for item in insight.get("evidence") or [])
            card = table(
                [[
                    Paragraph(
                        f"<b>{escape(_text(insight.get('title')))}</b><br/>{escape(_text(insight.get('summary')))}"
                        f"<br/><font color='#647983'>Evidências: {escape(evidence)}</font>",
                        styles["BiSmall"],
                    ),
                    label_value("Ação recomendada", insight.get("recommended_action"), "BiSmall"),
                ]],
                [116 * mm, 60 * mm],
                background=tones.get(insight.get("type"), colors.white),
            )
            story.append(KeepTogether([card, Spacer(1, 5)]))

        actions = ai.get("recommended_actions") or []
        if actions:
            rows = [[paragraph(str(index), "BiCardTitle"), paragraph(action)] for index, action in enumerate(actions, start=1)]
            story.extend([Spacer(1, 3), table(rows, [12 * mm, 164 * mm], background=green_soft), Spacer(1, 8)])

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(muted)
        canvas.drawString(document.leftMargin, 8.5 * mm, f"BI de Compras | {_text(snapshot.scope_label)}")
        canvas.drawRightString(A4[0] - document.rightMargin, 8.5 * mm, f"Página {canvas.getPageNumber()}")
        canvas.restoreState()

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=17 * mm, rightMargin=17 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"BI de Compras - {_text(snapshot.scope_label)}",
    )
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
