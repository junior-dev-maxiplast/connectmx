import io
from xml.sax.saxutils import escape

from django.utils import timezone


def _text(value, fallback="-"):
    if value is None or value == "":
        return fallback
    return str(value)


def _pct(value):
    if value is None:
        return "n/d"
    prefix = "+" if float(value) > 0 else ""
    return f"{prefix}{float(value):.1f}%".replace(".", ",")


def build_customer_dna_pdf(dashboard, snapshot):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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
    styles.add(ParagraphStyle(name="DnaTitle", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=20, leading=23, textColor=navy, spaceAfter=3))
    styles.add(ParagraphStyle(name="DnaSubtitle", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, textColor=muted))
    styles.add(ParagraphStyle(name="DnaSection", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=navy, spaceBefore=8, spaceAfter=7))
    styles.add(ParagraphStyle(name="DnaBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=body_color))
    styles.add(ParagraphStyle(name="DnaBodySmall", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.4, leading=9.4, textColor=body_color))
    styles.add(ParagraphStyle(name="DnaMuted", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=muted))
    styles.add(ParagraphStyle(name="DnaCardTitle", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=10.5, textColor=navy))
    styles.add(ParagraphStyle(name="DnaTableHead", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=7.3, leading=8.5, textColor=colors.white))

    def paragraph(value, style="DnaBody"):
        return Paragraph(escape(_text(value)), styles[style])

    def label_value(label, value, style="DnaBody"):
        return Paragraph(f"<b>{escape(label)}</b><br/>{escape(_text(value))}", styles[style])

    def styled_table(rows, widths, header=False, background=colors.white, repeat_rows=0, font_size=7.4):
        table = Table(rows, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
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
            for row_index in range(1, len(rows)):
                if row_index % 2 == 0:
                    commands.append(("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#f6f9fa")))
        table.setStyle(TableStyle(commands))
        return table

    customer = dashboard["customer"]
    metrics = dashboard["metrics"]
    intelligence = snapshot.metrics or {}
    ai_response = snapshot.ai_response or {}
    generated_at = timezone.localtime()
    pdf_title = f"DNA do Cliente - {customer['name']}"

    story = [
        Paragraph("CONNECTMX DASHES / VISAO 360 DO CLIENTE", styles["DnaSubtitle"]),
        Paragraph(escape(pdf_title), styles["DnaTitle"]),
        Paragraph(
            f"Codigo {escape(_text(customer['code']))} | CNPJ {escape(_text(customer['cnpj']))} | Gerado em {generated_at.strftime('%d/%m/%Y %H:%M')}",
            styles["DnaMuted"],
        ),
        Spacer(1, 9),
    ]

    summary_rows = [[
        label_value("Faturamento total", metrics.get("revenue")),
        label_value("Volume faturado", metrics.get("weight")),
        label_value("Valor medio / kg", metrics.get("average_value_kg")),
        label_value("Pedidos", f"{metrics.get('orders', 0)} | Ticket {metrics.get('average_ticket', '-')}")
    ]]
    summary = styled_table(summary_rows, [44 * mm] * 4, background=green_soft)
    summary.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 2, green)]))
    story.extend([summary, Spacer(1, 9)])

    story.append(Paragraph("Identificacao e relacionamento", styles["DnaSection"]))
    identity_rows = [
        [label_value("Cliente", customer.get("name")), label_value("Representante", customer.get("representative"))],
        [label_value("Localizacao", f"{customer.get('city')} / {customer.get('state')} - {customer.get('region')}"), label_value("Mercado", customer.get("market"))],
        [label_value("Endereco", customer.get("address")), label_value("Relacionamento", f"{dashboard['relationship'].get('duration')} | Desde {dashboard['relationship'].get('since')}")],
        [label_value("Ultima compra", dashboard["relationship"].get("last_purchase")), label_value("Perfil", f"{dashboard['profile'].get('frequency')} pedidos/mes | {dashboard['profile'].get('products_count')} produtos")],
    ]
    story.extend([styled_table(identity_rows, [88 * mm, 88 * mm]), Spacer(1, 9)])

    story.append(Paragraph("Analise da OpenAI", styles["DnaSection"]))
    ai_header = styled_table(
        [[label_value("Classificacao", ai_response.get("classification")), label_value("Modelo", f"{snapshot.ai_model or '-'} | {snapshot.ai_total_tokens} tokens")]],
        [116 * mm, 60 * mm],
        background=blue_soft,
    )
    story.extend([
        ai_header,
        Spacer(1, 6),
        paragraph(ai_response.get("executive_summary")),
        Spacer(1, 6),
        styled_table(
            [[label_value("Principal oportunidade", ai_response.get("principal_opportunity")), label_value("Principal atencao", ai_response.get("principal_attention"))]],
            [88 * mm, 88 * mm],
            background=amber_soft,
        ),
        Spacer(1, 8),
    ])

    for insight in ai_response.get("insights") or []:
        tone = {"positive": green_soft, "opportunity": blue_soft, "attention": red_soft, "operational": amber_soft}.get(insight.get("type"), colors.white)
        evidence = "; ".join(_text(item) for item in insight.get("evidence") or [])
        card = styled_table(
            [[
                Paragraph(
                    f"<b>{escape(_text(insight.get('title')))}</b><br/>{escape(_text(insight.get('summary')))}<br/><font color='#647983'>Evidencias: {escape(evidence)}</font>",
                    styles["DnaBodySmall"],
                ),
                label_value("Acao recomendada", insight.get("recommended_action"), "DnaBodySmall"),
            ]],
            [116 * mm, 60 * mm],
            background=tone,
        )
        story.append(KeepTogether([card, Spacer(1, 5)]))

    ai_actions = ai_response.get("recommended_actions") or []
    if ai_actions:
        action_rows = [[paragraph(str(index), "DnaCardTitle"), paragraph(action)] for index, action in enumerate(ai_actions, start=1)]
        story.extend([Spacer(1, 3), styled_table(action_rows, [12 * mm, 164 * mm], background=green_soft), Spacer(1, 8)])

    story.append(Paragraph("Indicadores calculados pelo ConnectMX", styles["DnaSection"]))
    for card in snapshot.insight_cards or []:
        evidence = "; ".join(_text(item) for item in card.get("evidence") or [])
        card_table = styled_table(
            [[
                Paragraph(f"<b>{escape(_text(card.get('eyebrow')))}</b><br/>{escape(_text(card.get('title')))}", styles["DnaCardTitle"]),
                Paragraph(f"{escape(_text(card.get('summary')))}<br/><font color='#647983'>{escape(evidence)}</font>", styles["DnaBodySmall"]),
            ]],
            [52 * mm, 124 * mm],
        )
        story.append(KeepTogether([card_table, Spacer(1, 5)]))
    story.append(Spacer(1, 3))

    def comparison_rows(title, values):
        rows = [[Paragraph(title, styles["DnaTableHead"]), paragraph("Atual", "DnaTableHead"), paragraph("Anterior", "DnaTableHead"), paragraph("Variacao", "DnaTableHead")]]
        for label, item in values:
            rows.append([paragraph(label, "DnaBodySmall"), paragraph(item.get("current"), "DnaBodySmall"), paragraph(item.get("previous"), "DnaBodySmall"), paragraph(_pct(item.get("change_pct")), "DnaBodySmall")])
        return rows

    ytd = intelligence.get("ytd") or {}
    rolling = intelligence.get("rolling_12_months") or {}
    order_entry = intelligence.get("order_entry_ytd") or {}
    comparison_specs = [
        (
            "Comparativo YTD",
            [("Faturamento", ytd.get("revenue", {})), ("Quantidade", ytd.get("quantity", {})), ("Pedidos", ytd.get("orders", {})), ("Notas fiscais", ytd.get("invoices", {}))],
        ),
        (
            "Janela movel de 12 meses",
            [("Faturamento", rolling.get("revenue", {})), ("Quantidade", rolling.get("quantity", {})), ("Pedidos", rolling.get("orders", {}))],
        ),
        (
            "Entrada de pedidos YTD",
            [("Pedidos emitidos", order_entry.get("orders", {})), ("Valor pedido", order_entry.get("value", {})), ("Quantidade pedida", order_entry.get("quantity", {})), ("Ticket medio", order_entry.get("average_ticket", {}))],
        ),
    ]
    for title, values in comparison_specs:
        story.extend([styled_table(comparison_rows(title, values), [68 * mm, 36 * mm, 36 * mm, 36 * mm], header=True, repeat_rows=1), Spacer(1, 6)])

    story.extend([Paragraph("Evolucao anual", styles["DnaSection"])])
    yearly_rows = [[paragraph("Ano", "DnaTableHead"), paragraph("Faturamento", "DnaTableHead"), paragraph("Volume (kg)", "DnaTableHead"), paragraph("Pedidos", "DnaTableHead")]]
    for row in dashboard.get("yearly") or []:
        yearly_rows.append([paragraph(row.get("label"), "DnaBodySmall"), paragraph(f"R$ {row.get('revenue', 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), "DnaBodySmall"), paragraph(f"{row.get('weight', 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), "DnaBodySmall"), paragraph(row.get("orders"), "DnaBodySmall")])
    story.extend([styled_table(yearly_rows, [28 * mm, 58 * mm, 52 * mm, 38 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    product_rows = [[paragraph("Produto", "DnaTableHead"), paragraph("Faturamento", "DnaTableHead"), paragraph("Volume", "DnaTableHead"), paragraph("Participacao", "DnaTableHead")]]
    for product in dashboard.get("products") or []:
        product_rows.append([paragraph(product.get("name"), "DnaBodySmall"), paragraph(product.get("revenue"), "DnaBodySmall"), paragraph(product.get("weight"), "DnaBodySmall"), paragraph(f"{product.get('share', 0)}%", "DnaBodySmall")])
    story.append(KeepTogether([
        Paragraph("Mix de produtos", styles["DnaSection"]),
        styled_table(product_rows, [88 * mm, 35 * mm, 33 * mm, 20 * mm], header=True, repeat_rows=1),
        Spacer(1, 8),
    ]))

    operational = dashboard.get("operational") or {}
    complaints = operational.get("complaints") or {}
    returns = operational.get("returns") or {}
    cliches = operational.get("cliches") or {}
    story.append(Paragraph("Qualidade, devoluções e clichês", styles["DnaSection"]))
    operational_summary = [
        [paragraph("Fonte", "DnaTableHead"), paragraph("Registros", "DnaTableHead"), paragraph("Indicador principal", "DnaTableHead"), paragraph("Impacto", "DnaTableHead")],
        [paragraph("Reclamações", "DnaBodySmall"), paragraph(complaints.get("count", 0), "DnaBodySmall"), paragraph(f"{complaints.get('severe_count', 0)} graves", "DnaBodySmall"), paragraph(f"Incidência média {complaints.get('incidence_pct', 0)}%", "DnaBodySmall")],
        [paragraph("Devoluções", "DnaBodySmall"), paragraph(returns.get("count", 0), "DnaBodySmall"), paragraph(returns.get("total_value_display"), "DnaBodySmall"), paragraph(f"{returns.get('revenue_share_pct', 0)}% do faturamento", "DnaBodySmall")],
        [paragraph("Clichês", "DnaBodySmall"), paragraph(cliches.get("count", 0), "DnaBodySmall"), paragraph(cliches.get("total_value_display"), "DnaBodySmall"), paragraph(f"Maxiplast {cliches.get('maxiplast_cost_value_display', '-')}", "DnaBodySmall")],
    ]
    story.extend([styled_table(operational_summary, [42 * mm, 28 * mm, 51 * mm, 55 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    story.append(Paragraph("Reclamações recentes", styles["DnaSection"]))
    complaint_rows = [[paragraph("Data", "DnaTableHead"), paragraph("Código", "DnaTableHead"), paragraph("Problema", "DnaTableHead"), paragraph("Classificação", "DnaTableHead"), paragraph("Volume NC", "DnaTableHead")]]
    for item in complaints.get("recent_items") or []:
        complaint_rows.append([paragraph(item.get("date_display"), "DnaBodySmall"), paragraph(item.get("code"), "DnaBodySmall"), paragraph(item.get("problem"), "DnaBodySmall"), paragraph(item.get("classification"), "DnaBodySmall"), paragraph(f"{item.get('nonconforming_volume', 0)} {item.get('measurement_unit', '')}", "DnaBodySmall")])
    if len(complaint_rows) == 1:
        complaint_rows.append([paragraph("Nenhuma reclamação encontrada.", "DnaBodySmall"), "", "", "", ""])
    story.extend([styled_table(complaint_rows, [24 * mm, 22 * mm, 77 * mm, 28 * mm, 25 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    story.append(Paragraph("Devoluções recentes", styles["DnaSection"]))
    return_rows = [[paragraph("Data", "DnaTableHead"), paragraph("NF", "DnaTableHead"), paragraph("Valor", "DnaTableHead"), paragraph("Problema", "DnaTableHead"), paragraph("Setor", "DnaTableHead")]]
    for item in returns.get("recent_items") or []:
        return_rows.append([paragraph(item.get("date_display"), "DnaBodySmall"), paragraph(item.get("invoice"), "DnaBodySmall"), paragraph(item.get("value_display"), "DnaBodySmall"), paragraph(item.get("problem"), "DnaBodySmall"), paragraph(item.get("sector"), "DnaBodySmall")])
    if len(return_rows) == 1:
        return_rows.append([paragraph("Nenhuma devolução encontrada.", "DnaBodySmall"), "", "", "", ""])
    story.extend([styled_table(return_rows, [24 * mm, 24 * mm, 30 * mm, 68 * mm, 30 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    story.append(Paragraph("Clichês recentes", styles["DnaSection"]))
    cliche_rows = [[paragraph("Data", "DnaTableHead"), paragraph("Pedido", "DnaTableHead"), paragraph("Valor", "DnaTableHead"), paragraph("Área", "DnaTableHead"), paragraph("Custo da troca", "DnaTableHead")]]
    for item in cliches.get("recent_items") or []:
        cliche_rows.append([paragraph(item.get("date_display"), "DnaBodySmall"), paragraph(item.get("order"), "DnaBodySmall"), paragraph(item.get("value_display"), "DnaBodySmall"), paragraph(item.get("area_display"), "DnaBodySmall"), paragraph(item.get("exchange_cost"), "DnaBodySmall")])
    if len(cliche_rows) == 1:
        cliche_rows.append([paragraph("Nenhum clichê encontrado.", "DnaBodySmall"), "", "", "", ""])
    story.extend([styled_table(cliche_rows, [24 * mm, 27 * mm, 35 * mm, 45 * mm, 45 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    open_orders = sorted(
        (intelligence.get("open_orders") or {}).get("items") or [],
        key=lambda item: float(item.get("value") or 0),
        reverse=True,
    )
    order_header = [paragraph("Pedido", "DnaTableHead"), paragraph("Valor", "DnaTableHead"), paragraph("Emissao", "DnaTableHead"), paragraph("Previsao", "DnaTableHead"), paragraph("Saldo fisico", "DnaTableHead"), paragraph("Atendimento", "DnaTableHead")]
    order_rows = []
    for order in open_orders:
        order_rows.append([
            paragraph(order.get("number"), "DnaBodySmall"),
            paragraph(order.get("value_display"), "DnaBodySmall"),
            paragraph(order.get("issue_date_display"), "DnaBodySmall"),
            paragraph(order.get("forecast_date_display"), "DnaBodySmall"),
            paragraph(order.get("balance_display"), "DnaBodySmall"),
            paragraph(_pct(order.get("service_pct")), "DnaBodySmall"),
        ])
    if not order_rows:
        order_rows.append([paragraph("Nenhum pedido aberto identificado.", "DnaBodySmall"), "", "", "", "", ""])

    story.append(PageBreak())
    rows_per_page = 31
    for chunk_index in range(0, len(order_rows), rows_per_page):
        if chunk_index:
            story.append(PageBreak())
        section_title = "Carteira aberta - ordenada por valor"
        if chunk_index:
            section_title += " (continuacao)"
        story.append(Paragraph(section_title, styles["DnaSection"]))
        chunk = order_rows[chunk_index:chunk_index + rows_per_page]
        story.extend([
            styled_table([order_header] + chunk, [23 * mm, 36 * mm, 27 * mm, 27 * mm, 34 * mm, 29 * mm], header=True, repeat_rows=1, font_size=7),
            Spacer(1, 8),
        ])

    story.append(Paragraph("Ultimos pedidos faturados", styles["DnaSection"]))
    recent_rows = [[paragraph("Pedido", "DnaTableHead"), paragraph("Data", "DnaTableHead"), paragraph("Valor", "DnaTableHead"), paragraph("Volume", "DnaTableHead"), paragraph("Status", "DnaTableHead")]]
    for order in dashboard.get("orders") or []:
        recent_rows.append([paragraph(order.get("number"), "DnaBodySmall"), paragraph(order.get("date_display"), "DnaBodySmall"), paragraph(order.get("value_display"), "DnaBodySmall"), paragraph(order.get("weight_display"), "DnaBodySmall"), paragraph(order.get("status"), "DnaBodySmall")])
    story.extend([styled_table(recent_rows, [25 * mm, 30 * mm, 40 * mm, 38 * mm, 43 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    story.append(Paragraph("Sinais comerciais e condicoes de pagamento", styles["DnaSection"]))
    signal_rows = [[paragraph("Tipo", "DnaTableHead"), paragraph("Descricao", "DnaTableHead"), paragraph("Ocorrencias", "DnaTableHead"), paragraph("Participacao", "DnaTableHead")]]
    commercial_signals = intelligence.get("commercial_signals") or {}
    for signal_type, items in [("Situacao", commercial_signals.get("status_distribution") or []), ("Pagamento", commercial_signals.get("payment_distribution") or [])]:
        for item in items:
            signal_rows.append([paragraph(signal_type, "DnaBodySmall"), paragraph(item.get("label"), "DnaBodySmall"), paragraph(item.get("count"), "DnaBodySmall"), paragraph(_pct(item.get("share_pct")), "DnaBodySmall")])
    story.extend([styled_table(signal_rows, [30 * mm, 86 * mm, 30 * mm, 30 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    story.append(Paragraph("Recomendacoes calculadas pelo ConnectMX", styles["DnaSection"]))
    rule_actions = intelligence.get("actions") or []
    for action in rule_actions:
        action_table = styled_table(
            [[
                paragraph(action.get("priority"), "DnaCardTitle"),
                Paragraph(f"<b>{escape(_text(action.get('title')))}</b><br/>{escape(_text(action.get('detail')))}<br/><font color='#647983'>{escape(_text(action.get('evidence')))}</font>", styles["DnaBodySmall"]),
            ]],
            [12 * mm, 164 * mm],
            background=green_soft,
        )
        story.append(KeepTogether([action_table, Spacer(1, 4)]))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=20 * mm, bottomMargin=15 * mm)
    doc.title = pdf_title
    doc.author = "ConnectMX"

    def on_page(canvas, document):
        canvas.saveState()
        page_width, page_height = A4
        canvas.setFillColor(navy)
        canvas.rect(0, page_height - 15 * mm, page_width, 15 * mm, fill=1, stroke=0)
        canvas.setFillColor(green)
        canvas.rect(0, page_height - 15 * mm, 7 * mm, 15 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 10.5)
        canvas.drawString(document.leftMargin, page_height - 9.5 * mm, pdf_title[:78])
        canvas.setFillColor(muted)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(document.leftMargin, 8.5 * mm, f"Snapshot {snapshot.source_fingerprint[:10]} | Dados ate {_text(snapshot.source_period_end)}")
        canvas.drawRightString(page_width - document.rightMargin, 8.5 * mm, f"Pagina {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()
