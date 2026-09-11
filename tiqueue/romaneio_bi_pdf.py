"""
PDF do BI de Romaneios.

Mesma linguagem dos relatórios do BI do TI e do BI de Viagens, com o conteúdo
de produtividade de contagem de pallets: volume, etapas, filiais e ranking de
colaboradores.
"""

from xml.sax.saxutils import escape

from django.utils import timezone


def _text(value, fallback="-"):
    if value is None or value == "":
        return fallback
    return str(value)


def build_romaneio_bi_pdf(dashboard, snapshot):
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
    ai = snapshot.ai_response or {}
    deep = (snapshot.metrics or {}).get("deep") or {}
    funnel = deep.get("funnel") or {}
    generated_at = timezone.localtime()

    story = [
        Paragraph("CONNECTMX DASHES / BI DE ROMANEIOS", styles["BiSubtitle"]),
        Paragraph("Produtividade da contagem de pallets", styles["BiTitle"]),
        Paragraph(
            f"{escape(_text(scope['period']['full_label']))} | {escape(_text(scope['branch']['label']))}"
            f" | {escape(_text(scope['stage']['label']))} | {escape(_text(scope['matricula']['label']))}"
            f" | Gerado em {generated_at.strftime('%d/%m/%Y %H:%M')}",
            styles["BiMuted"],
        ),
        Spacer(1, 9),
    ]

    resume = table(
        [[
            label_value("Registros", metrics.get("records_display")),
            label_value("Volumes", metrics.get("volumes_display")),
            label_value("Peso total", metrics.get("weight_display")),
            label_value("Colaboradores", metrics.get("employees_display")),
        ]],
        [44 * mm] * 4,
        background=green_soft,
    )
    resume.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 2, green)]))
    story.extend([resume, Spacer(1, 9)])

    story.append(Paragraph("Qualidade do cadastro", styles["BiSection"]))
    story.extend([
        table(
            [[
                label_value("Pallets distintos", metrics.get("pallets_display")),
                label_value("Registros por colaborador", metrics.get("records_per_employee_display")),
                label_value("Sem peso informado", f"{metrics.get('missing_weight_pct')}%"),
                label_value("Sem volume informado", f"{metrics.get('missing_volume_pct')}%"),
            ]],
            [44 * mm] * 4,
        ),
        Spacer(1, 9),
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
                 "capacity": blue_soft, "quality": amber_soft}
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

    if funnel:
        story.append(Paragraph("Funil de conclusão do pallet", styles["BiSection"]))
        story.extend([
            table(
                [[
                    label_value("Pallets no recorte", funnel.get("total_display")),
                    label_value("Concluíram (chegaram a Carregar)", f"{funnel.get('completed_display')} ({funnel.get('completed_pct')}%)"),
                    label_value("Tempo médio de ciclo", funnel.get("lead_time_display")),
                ]],
                [58 * mm] * 3,
                background=blue_soft,
            ),
            Spacer(1, 6),
        ])
        rows = [[paragraph("Etapas alcançadas", "BiHead"), paragraph("Pallets", "BiHead"), paragraph("Participação", "BiHead")]]
        for label_key, count_key, pct_key in (
            ("Só 1 etapa", "stopped_stage_1", "stopped_stage_1_pct"),
            ("Até 2 etapas", "stopped_stage_2", "stopped_stage_2_pct"),
            ("Até 3 etapas", "stopped_stage_3", "stopped_stage_3_pct"),
            ("As 4 etapas", "reached_stage_4", "reached_stage_4_pct"),
        ):
            rows.append([
                paragraph(label_key, "BiSmall"),
                paragraph(_text(funnel.get(count_key)), "BiSmall"),
                paragraph(f"{funnel.get(pct_key)}%", "BiSmall"),
            ])
        story.extend([table(rows, [64 * mm, 52 * mm, 52 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    by_stage = dashboard.get("by_stage") or []
    if by_stage:
        story.append(Paragraph("Distribuição por etapa", styles["BiSection"]))
        rows = [[paragraph("Etapa", "BiHead"), paragraph("Registros", "BiHead"),
                 paragraph("Participação", "BiHead"), paragraph("Peso", "BiHead")]]
        for item in by_stage:
            rows.append([
                paragraph(item.get("label"), "BiSmall"),
                paragraph(item.get("total_display"), "BiSmall"),
                paragraph(f"{item.get('share_pct')}%", "BiSmall"),
                paragraph(item.get("peso_display"), "BiSmall"),
            ])
        story.extend([table(rows, [46 * mm, 40 * mm, 40 * mm, 42 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    by_branch = dashboard.get("by_branch") or []
    if by_branch:
        story.append(Paragraph("Distribuição por filial", styles["BiSection"]))
        rows = [[paragraph("Filial", "BiHead"), paragraph("Registros", "BiHead"), paragraph("Participação", "BiHead")]]
        for item in by_branch:
            rows.append([
                paragraph(item.get("label"), "BiSmall"),
                paragraph(item.get("total_display"), "BiSmall"),
                paragraph(f"{item.get('share_pct')}%", "BiSmall"),
            ])
        story.extend([table(rows, [64 * mm, 52 * mm, 52 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    ranking = dashboard.get("ranking") or []
    if ranking:
        story.append(Paragraph("Ranking de colaboradores", styles["BiSection"]))
        story.append(Paragraph(f"{dashboard.get('ranking_total_employees')} colaboradores no recorte", styles["BiMuted"]))
        rows = [[paragraph("Colaborador", "BiHead"), paragraph("Registros", "BiHead"),
                 paragraph("Volumes", "BiHead"), paragraph("Peso", "BiHead"),
                 paragraph("Dias trabalhados", "BiHead"), paragraph("Pallets", "BiHead")]]
        for item in ranking[:25]:
            nome = item.get("nome")
            colaborador = f"{item.get('matricula')} - {nome}" if nome else item.get("matricula")
            rows.append([
                paragraph(colaborador, "BiSmall"),
                paragraph(item.get("registros_display"), "BiSmall"),
                paragraph(item.get("volumes_display"), "BiSmall"),
                paragraph(item.get("peso_display"), "BiSmall"),
                paragraph(item.get("dias_trabalhados"), "BiSmall"),
                paragraph(item.get("pallets"), "BiSmall"),
            ])
        story.append(table(rows, [38 * mm, 24 * mm, 24 * mm, 28 * mm, 30 * mm, 24 * mm], header=True, repeat_rows=1))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(muted)
        canvas.drawString(document.leftMargin, 8.5 * mm, f"BI de Romaneios | {_text(snapshot.scope_label)}")
        canvas.drawRightString(A4[0] - document.rightMargin, 8.5 * mm, f"Página {canvas.getPageNumber()}")
        canvas.restoreState()

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=17 * mm, rightMargin=17 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"BI de Romaneios - {_text(snapshot.scope_label)}",
    )
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
