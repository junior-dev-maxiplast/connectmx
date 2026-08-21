"""
PDF do BI do TI.

Mesma linguagem do relatório do DNA do Cliente, mas com o conteúdo do service
desk: volume, SLA, satisfação, apontamento e as análises processadas.
"""

from xml.sax.saxutils import escape

from django.utils import timezone


def _text(value, fallback="-"):
    if value is None or value == "":
        return fallback
    return str(value)


def build_it_bi_pdf(dashboard, snapshot):
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
    sla = dashboard["sla"]
    ratings = dashboard["ratings"]
    logged = dashboard["logged"]
    scope = dashboard["scope"]
    ai = snapshot.ai_response or {}
    deep = (snapshot.metrics or {}).get("deep") or {}
    generated_at = timezone.localtime()

    story = [
        Paragraph("CONNECTMX DASHES / BI DO TI", styles["BiSubtitle"]),
        Paragraph("Indicadores do service desk", styles["BiTitle"]),
        Paragraph(
            f"{escape(_text(scope['period']['label']))} | {escape(_text(scope['company']['label']))}"
            f" | {escape(_text(scope['attendant']['label']))}"
            f" | Gerado em {generated_at.strftime('%d/%m/%Y %H:%M')}",
            styles["BiMuted"],
        ),
        Spacer(1, 9),
    ]

    resume = table(
        [[
            label_value("Chamados", metrics.get("tickets_display")),
            label_value("Fechados", f"{metrics.get('closed_display')} ({metrics.get('closed_pct')}%)"),
            label_value("Backlog", metrics.get("backlog_display")),
            label_value("Resolucao media", metrics.get("resolution_display")),
        ]],
        [44 * mm] * 4,
        background=green_soft,
    )
    resume.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 2, green)]))
    story.extend([resume, Spacer(1, 9)])

    story.append(Paragraph("Qualidade do atendimento", styles["BiSection"]))
    story.extend([
        table(
            [
                [label_value("SLA de conclusao", f"{sla.get('within_pct')}% no prazo | {sla.get('breached_display')} vencidos"),
                 label_value("Primeira resposta", f"{sla.get('response_breached_pct')}% fora | media {sla.get('response_average_display')}")],
                [label_value("Avaliacao media", f"{ratings.get('average_display')} | {ratings.get('rated_display')} avaliados ({ratings.get('rated_pct')}%)"),
                 label_value("Avaliacoes ruins", f"{ratings.get('bad')} nota ate {ratings.get('bad_max')} ({ratings.get('bad_pct')}%)")],
                [label_value("Tempo apontado", f"{logged.get('total_hours')}h no periodo | media {logged.get('average_display')}"),
                 label_value("Cobertura de apontamento", f"{logged.get('coverage_pct')}% dos chamados")],
            ],
            [88 * mm, 88 * mm],
        ),
        Spacer(1, 9),
    ])

    if ai:
        story.append(Paragraph("Analise da IA", styles["BiSection"]))
        story.extend([
            table(
                [[label_value("Situacao geral", _text(ai.get("health")).title()),
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
                 "capacity": blue_soft, "quality": amber_soft}
        for insight in ai.get("insights") or []:
            evidence = "; ".join(_text(item) for item in insight.get("evidence") or [])
            card = table(
                [[
                    Paragraph(
                        f"<b>{escape(_text(insight.get('title')))}</b><br/>{escape(_text(insight.get('summary')))}"
                        f"<br/><font color='#647983'>Evidencias: {escape(evidence)}</font>",
                        styles["BiSmall"],
                    ),
                    label_value("Acao recomendada", insight.get("recommended_action"), "BiSmall"),
                ]],
                [116 * mm, 60 * mm],
                background=tones.get(insight.get("type"), colors.white),
            )
            story.append(KeepTogether([card, Spacer(1, 5)]))

        actions = ai.get("recommended_actions") or []
        if actions:
            rows = [[paragraph(str(index), "BiCardTitle"), paragraph(action)] for index, action in enumerate(actions, start=1)]
            story.extend([Spacer(1, 3), table(rows, [12 * mm, 164 * mm], background=green_soft), Spacer(1, 8)])

    spread = deep.get("resolution_spread") or []
    if spread:
        story.append(Paragraph("Tempo de resolucao", styles["BiSection"]))
        rows = [[paragraph("Faixa", "BiHead"), paragraph("Chamados", "BiHead"),
                 paragraph("Participacao", "BiHead"), paragraph("Media na faixa", "BiHead")]]
        for item in spread:
            rows.append([
                paragraph(item.get("label"), "BiSmall"),
                paragraph(item.get("total_display"), "BiSmall"),
                paragraph(f"{item.get('share_pct')}%", "BiSmall"),
                paragraph(item.get("average_display"), "BiSmall"),
            ])
        story.extend([table(rows, [52 * mm, 40 * mm, 40 * mm, 44 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    rating_speed = [item for item in (deep.get("rating_vs_speed") or []) if item.get("rated")]
    if rating_speed:
        story.append(Paragraph("Satisfacao por tempo de resolucao", styles["BiSection"]))
        rows = [[paragraph("Faixa", "BiHead"), paragraph("Nota media", "BiHead"), paragraph("Avaliacoes", "BiHead")]]
        for item in rating_speed:
            rows.append([
                paragraph(item.get("label"), "BiSmall"),
                paragraph(item.get("rating_display"), "BiSmall"),
                paragraph(item.get("rated"), "BiSmall"),
            ])
        story.extend([table(rows, [72 * mm, 52 * mm, 52 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    attendants = dashboard.get("attendants") or []
    if attendants:
        story.append(Paragraph("Atendentes", styles["BiSection"]))
        rows = [[paragraph("Atendente", "BiHead"), paragraph("Chamados", "BiHead"),
                 paragraph("Fechados", "BiHead"), paragraph("Tempo medio", "BiHead"),
                 paragraph("Apontado", "BiHead"), paragraph("Nota", "BiHead")]]
        for item in attendants[:15]:
            rows.append([
                paragraph(item.get("name"), "BiSmall"),
                paragraph(item.get("total_display"), "BiSmall"),
                paragraph(f"{item.get('closed_pct')}%", "BiSmall"),
                paragraph(item.get("average_display"), "BiSmall"),
                paragraph(item.get("logged_display"), "BiSmall"),
                paragraph(item.get("rating_display"), "BiSmall"),
            ])
        story.extend([
            table(rows, [46 * mm, 24 * mm, 24 * mm, 30 * mm, 28 * mm, 24 * mm], header=True, repeat_rows=1),
            Spacer(1, 8),
        ])

    aging = dashboard.get("aging") or {}
    if aging.get("items"):
        story.append(Paragraph("Backlog mais antigo", styles["BiSection"]))
        counts = aging.get("counts") or {}
        story.extend([
            table(
                [[label_value("Ate 7 dias", counts.get("ate_7")), label_value("8 a 30 dias", counts.get("de_8_a_30")),
                  label_value("31 a 90 dias", counts.get("de_31_a_90")), label_value("Mais de 90 dias", counts.get("acima_90"))]],
                [44 * mm] * 4,
                background=red_soft,
            ),
            Spacer(1, 6),
        ])
        rows = [[paragraph("Chamado", "BiHead"), paragraph("Abertura", "BiHead"), paragraph("Dias", "BiHead"),
                 paragraph("Status", "BiHead"), paragraph("Atendente", "BiHead")]]
        for item in aging["items"][:12]:
            rows.append([
                Paragraph(
                    f"<b>{escape(_text(item.get('code')))}</b><br/>"
                    f"<font color='#647983'>{escape(_text(item.get('subject'))[:58])}</font>",
                    styles["BiSmall"],
                ),
                paragraph(item.get("opened_display"), "BiSmall"),
                paragraph(item.get("days"), "BiSmall"),
                paragraph(item.get("status"), "BiSmall"),
                paragraph(item.get("attendant"), "BiSmall"),
            ])
        story.append(table(rows, [62 * mm, 30 * mm, 16 * mm, 34 * mm, 34 * mm], header=True, repeat_rows=1, font_size=6.8))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(muted)
        canvas.drawString(document.leftMargin, 8.5 * mm, f"BI do TI | {_text(snapshot.scope_label)}")
        canvas.drawRightString(A4[0] - document.rightMargin, 8.5 * mm, f"Pagina {canvas.getPageNumber()}")
        canvas.restoreState()

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=17 * mm, rightMargin=17 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"BI do TI - {_text(snapshot.scope_label)}",
    )
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
