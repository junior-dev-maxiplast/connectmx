"""
PDF do BI de Viagens.

Mesma linguagem visual dos relatórios do DNA do Cliente e do BI do TI, com o
conteúdo da frota: volume, quilometragem, consumo, adiantamento, qualidade de
cadastro e a fila de viagens sem baixa.
"""

from xml.sax.saxutils import escape

from django.utils import timezone


def _text(value, fallback="-"):
    if value is None or value == "":
        return fallback
    return str(value)


def build_travel_bi_pdf(dashboard, snapshot):
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
    cargo = dashboard.get("cargo") or {}
    cost = dashboard.get("cost") or {}
    profile = dashboard.get("fleet_profile") or {}
    validation = dashboard["validation"]
    forecast = dashboard["forecast"]
    backlog = dashboard["open_backlog"]
    scope = dashboard["scope"]
    ai = snapshot.ai_response or {}
    deep = (snapshot.metrics or {}).get("deep") or {}
    generated_at = timezone.localtime()

    story = [
        Paragraph("CONNECTMX DASHES / BI DE VIAGENS", styles["BiSubtitle"]),
        Paragraph("Indicadores da frota", styles["BiTitle"]),
        Paragraph(
            f"{escape(_text(scope['period']['full_label']))} | {escape(_text(scope['carrier']['label']))}"
            f" | {escape(_text(scope['situation']['label']))}"
            f" | Gerado em {generated_at.strftime('%d/%m/%Y %H:%M')}",
            styles["BiMuted"],
        ),
        Spacer(1, 9),
    ]

    resume = table(
        [[
            label_value("Viagens", metrics.get("trips_display")),
            label_value("Finalizadas", f"{metrics.get('finished_display')} ({metrics.get('finished_pct')}%)"),
            label_value("KM rodado", metrics.get("km_total_display")),
            label_value("Consumo medio", f"{metrics.get('consumption_display')} km/l"),
        ]],
        [44 * mm] * 4,
        background=green_soft,
    )
    resume.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 2, green)]))
    story.extend([resume, Spacer(1, 9)])

    story.append(Paragraph("Operacao e custo", styles["BiSection"]))
    story.extend([
        table(
            [
                [label_value("Custo da frota", f"{cost.get('closed_total_display', '-')} | {cost.get('closed_months', 0)} competencia(s) fechada(s)"),
                 label_value("Custo por km", f"{cost.get('per_km_display', '-')} contabil | adiantamento {cost.get('advance_share_pct', 0)}% do custo")],
                [label_value("Duracao media", f"{metrics.get('duration_average_display')} | {metrics.get('duration_coverage_pct')}% com as duas datas"),
                 label_value("Distancia media", f"{metrics.get('km_average_display')} por viagem")],
                [label_value("Frota", f"{metrics.get('vehicles')} placas | {metrics.get('drivers')} motoristas"),
                 label_value("Combustivel", f"{metrics.get('liters_total_display')} | {metrics.get('liters_coverage_pct')}% das viagens com litros")],
                [label_value("Carga transportada", f"{cargo.get('weight_display', '-')} | {cargo.get('weight_per_trip_display', '-')} por viagem"),
                 label_value("Custo por tonelada", f"{cost.get('per_ton_display', '-')} | roteiro em {cargo.get('coverage_pct', 0)}% das viagens")],
                [label_value("Qualidade de cadastro", f"{validation.get('correct_pct')}% corretos | {validation.get('wrong_display')} com erro"),
                 label_value("Viagens em aberto", f"{backlog.get('total_display')} sem baixa | {backlog.get('advance_display')} adiantados")],
            ],
            [88 * mm, 88 * mm],
        ),
        Spacer(1, 4),
        Paragraph(
            f"O consumo usa {metrics.get('consumption_base')} viagens ({metrics.get('consumption_base_pct')}%) "
            f"com KM e litros coerentes. As demais ficam de fora para nao contaminar a media.",
            styles["BiMuted"],
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
                 "cost": blue_soft, "fleet": blue_soft, "quality": amber_soft}
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

    reasons = validation.get("reasons") or []
    if reasons:
        story.append(Paragraph("Qualidade do cadastro", styles["BiSection"]))
        rows = [[paragraph("Motivo", "BiHead"), paragraph("Viagens", "BiHead"), paragraph("Dos erros", "BiHead")]]
        for item in reasons:
            rows.append([
                paragraph(item.get("label"), "BiSmall"),
                paragraph(item.get("total_display"), "BiSmall"),
                paragraph(f"{item.get('share_pct')}%", "BiSmall"),
            ])
        story.extend([table(rows, [96 * mm, 40 * mm, 40 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    routes = cargo.get("routes") or []
    if routes:
        story.append(Paragraph("Rotas", styles["BiSection"]))
        rows = [[paragraph("Rota", "BiHead"), paragraph("Tipo", "BiHead"),
                 paragraph("Viagens", "BiHead"), paragraph("Carga", "BiHead"),
                 paragraph("Por viagem", "BiHead"), paragraph("Paletes", "BiHead"),
                 paragraph("Part.", "BiHead")]]
        for item in routes:
            rows.append([
                paragraph(item.get("name"), "BiSmall"),
                paragraph("Aproveitamento" if item.get("opportunistic") else "Principal", "BiSmall"),
                paragraph(item.get("trips_display"), "BiSmall"),
                paragraph(item.get("weight_display"), "BiSmall"),
                paragraph(item.get("weight_per_trip_display"), "BiSmall"),
                paragraph(item.get("pallets_display"), "BiSmall"),
                paragraph(f"{item.get('share_pct')}%", "BiSmall"),
            ])
        story.extend([
            table(rows, [44 * mm, 28 * mm, 20 * mm, 24 * mm, 24 * mm, 20 * mm, 16 * mm],
                  header=True, repeat_rows=1, font_size=6.8),
            Spacer(1, 4),
            Paragraph(
                f"Uma viagem pode passar por mais de uma rota ({cargo.get('multi_route_trips', 0)} "
                f"passaram no recorte), entao a coluna de viagens conta a mesma viagem em cada rota. "
                f"Carga e paletes sao aditivos. O roteiro so passou a ser preenchido em setembro de "
                f"2025 e cobre {cargo.get('coverage_pct', 0)}% das viagens deste recorte.",
                styles["BiMuted"],
            ),
            Spacer(1, 8),
        ])

    months = [item for item in (cost.get("months") or []) if not item.get("is_open")]
    if months:
        story.append(Paragraph("Custo da frota por competencia", styles["BiSection"]))
        rows = [[paragraph("Competencia", "BiHead"), paragraph("Custo", "BiHead"),
                 paragraph("KM", "BiHead"), paragraph("Custo por km", "BiHead"),
                 paragraph("Carga", "BiHead")]]
        for item in months[-14:]:
            rows.append([
                paragraph(item.get("label"), "BiSmall"),
                paragraph(item.get("cost_display"), "BiSmall"),
                paragraph(item.get("km_display"), "BiSmall"),
                paragraph(item.get("cost_per_km_display"), "BiSmall"),
                paragraph(item.get("weight_display"), "BiSmall"),
            ])
        story.extend([table(rows, [32 * mm, 42 * mm, 38 * mm, 34 * mm, 30 * mm], header=True, repeat_rows=1)])
        if cost.get("open_month"):
            story.append(Paragraph(
                f"A competencia {cost['open_month']} ainda nao recebeu o lancamento de fechamento "
                f"e ficou fora do custo por km.",
                styles["BiMuted"],
            ))
        story.append(Spacer(1, 8))

    ages = profile.get("ages") or []
    if ages:
        story.append(Paragraph("Perfil da frota", styles["BiSection"]))
        rows = [[paragraph("Faixa de idade", "BiHead"), paragraph("Placas", "BiHead"),
                 paragraph("Viagens", "BiHead"), paragraph("KM", "BiHead"), paragraph("km/l", "BiHead")]]
        for item in ages:
            rows.append([
                paragraph(item.get("label"), "BiSmall"),
                paragraph(item.get("plates"), "BiSmall"),
                paragraph(item.get("trips_display"), "BiSmall"),
                paragraph(item.get("km_display"), "BiSmall"),
                paragraph(item.get("consumption_display"), "BiSmall"),
            ])
        story.extend([
            table(rows, [44 * mm, 26 * mm, 30 * mm, 40 * mm, 36 * mm], header=True, repeat_rows=1),
            Spacer(1, 4),
            Paragraph(
                "Idade nao isola desgaste: a frota mistura carreta, caminhao leve e utilitario, "
                "e o porte do veiculo pesa mais no km/l do que o ano.",
                styles["BiMuted"],
            ),
            Spacer(1, 8),
        ])

    carriers = dashboard.get("carriers") or []
    if carriers:
        story.append(Paragraph("Frotas", styles["BiSection"]))
        rows = [[paragraph("Frota", "BiHead"), paragraph("Viagens", "BiHead"), paragraph("KM", "BiHead"),
                 paragraph("km/l", "BiHead"), paragraph("Adiantamento", "BiHead"), paragraph("Erro", "BiHead")]]
        for item in carriers:
            rows.append([
                Paragraph(
                    f"<b>{escape(_text(item.get('label')))}</b><br/>"
                    f"<font color='#647983'>{escape(_text(item.get('cnpj'), ''))}</font>",
                    styles["BiSmall"],
                ),
                paragraph(item.get("trips_display"), "BiSmall"),
                paragraph(item.get("km_display"), "BiSmall"),
                paragraph(item.get("consumption_display"), "BiSmall"),
                paragraph(item.get("advance_display"), "BiSmall"),
                paragraph(f"{item.get('error_pct')}%", "BiSmall"),
            ])
        story.extend([
            table(rows, [46 * mm, 22 * mm, 30 * mm, 18 * mm, 34 * mm, 26 * mm], header=True, repeat_rows=1),
            Spacer(1, 8),
        ])

    outliers = (dashboard.get("outliers") or {}).get("items") or []
    if outliers:
        story.append(Paragraph("Consumo fora do padrao", styles["BiSection"]))
        story.extend([
            Paragraph(
                f"Referencia da frota: {_text((dashboard.get('outliers') or {}).get('reference_display'))} km/l. "
                f"Só entram placas com pelo menos {(dashboard.get('outliers') or {}).get('min_trips')} viagens.",
                styles["BiMuted"],
            ),
            Spacer(1, 4),
        ])
        rows = [[paragraph("Placa", "BiHead"), paragraph("Viagens", "BiHead"), paragraph("KM", "BiHead"),
                 paragraph("km/l", "BiHead"), paragraph("Desvio", "BiHead")]]
        for item in outliers[:14]:
            rows.append([
                paragraph(item.get("plate"), "BiSmall"),
                paragraph(item.get("trips"), "BiSmall"),
                paragraph(item.get("km_display"), "BiSmall"),
                paragraph(item.get("consumption_display"), "BiSmall"),
                paragraph(item.get("deviation_display"), "BiSmall"),
            ])
        story.extend([
            table(rows, [40 * mm, 26 * mm, 40 * mm, 30 * mm, 40 * mm], header=True, repeat_rows=1),
            Spacer(1, 8),
        ])

    vehicles = dashboard.get("vehicles") or []
    if vehicles:
        story.append(Paragraph("Veiculos", styles["BiSection"]))
        rows = [[paragraph("Placa", "BiHead"), paragraph("Modelo", "BiHead"),
                 paragraph("Idade", "BiHead"), paragraph("Viagens", "BiHead"),
                 paragraph("KM", "BiHead"), paragraph("km/l", "BiHead")]]
        for item in vehicles[:15]:
            rows.append([
                paragraph(item.get("plate"), "BiSmall"),
                paragraph(item.get("model"), "BiSmall"),
                paragraph(item.get("age_label"), "BiSmall"),
                paragraph(item.get("trips_display"), "BiSmall"),
                paragraph(item.get("km_display"), "BiSmall"),
                paragraph(item.get("consumption_display"), "BiSmall"),
            ])
        story.extend([
            table(rows, [26 * mm, 50 * mm, 34 * mm, 20 * mm, 26 * mm, 20 * mm], header=True, repeat_rows=1),
            Spacer(1, 8),
        ])

    drivers = dashboard.get("drivers") or []
    if drivers:
        story.append(Paragraph("Motoristas", styles["BiSection"]))
        rows = [[paragraph("Motorista", "BiHead"), paragraph("Frota", "BiHead"), paragraph("Viagens", "BiHead"),
                 paragraph("KM", "BiHead"), paragraph("km/l", "BiHead"), paragraph("Duracao", "BiHead")]]
        for item in drivers[:15]:
            rows.append([
                Paragraph(
                    f"{escape(_text(item.get('label')))}"
                    f"<br/><font color='#647983'>{escape(_text(item.get('key')))}</font>",
                    styles["BiSmall"],
                ),
                paragraph(item.get("fleet"), "BiSmall"),
                paragraph(item.get("trips_display"), "BiSmall"),
                paragraph(item.get("km_display"), "BiSmall"),
                paragraph(item.get("consumption_display"), "BiSmall"),
                paragraph(item.get("duration_display"), "BiSmall"),
            ])
        story.extend([
            table(rows, [40 * mm, 22 * mm, 24 * mm, 32 * mm, 22 * mm, 26 * mm], header=True, repeat_rows=1),
            Spacer(1, 8),
        ])

    bands = (deep.get("km_band_consumption") or [])
    if bands:
        story.append(Paragraph("Consumo por faixa de distancia", styles["BiSection"]))
        rows = [[paragraph("Faixa", "BiHead"), paragraph("Viagens", "BiHead"),
                 paragraph("KM", "BiHead"), paragraph("km/l", "BiHead")]]
        for item in bands:
            rows.append([
                paragraph(item.get("label"), "BiSmall"),
                paragraph(item.get("trips_display"), "BiSmall"),
                paragraph(item.get("km_display"), "BiSmall"),
                paragraph(item.get("consumption_display"), "BiSmall"),
            ])
        story.extend([table(rows, [52 * mm, 40 * mm, 44 * mm, 40 * mm], header=True, repeat_rows=1), Spacer(1, 8)])

    story.append(Paragraph("Previsto x realizado", styles["BiSection"]))
    story.extend([
        table(
            [[
                label_value("Previsao reescrita", f"{forecast.get('same_date_display')} de {forecast.get('measured_display')} ({forecast.get('same_date_pct')}%)"),
                label_value("Comparaveis", forecast.get("comparable_display")),
                label_value("Atrasadas", f"{forecast.get('late_display')} ({forecast.get('late_pct')}%)"),
                label_value("Atraso medio", forecast.get("delay_average_display")),
            ]],
            [44 * mm] * 4,
            background=amber_soft,
        ),
        Spacer(1, 4),
        Paragraph(
            "A chegada prevista e reescrita no fechamento da viagem: o prazo so pode ser avaliado "
            "onde a previsao continua diferente da data realizada.",
            styles["BiMuted"],
        ),
        Spacer(1, 8),
    ])

    if backlog.get("items"):
        story.append(Paragraph("Viagens em aberto", styles["BiSection"]))
        counts = backlog.get("counts") or {}
        story.extend([
            table(
                [[label_value("No prazo", counts.get("no_prazo")), label_value("Ate 7 dias", counts.get("ate_7")),
                  label_value("8 a 30 dias", counts.get("de_8_a_30")), label_value("Mais de 30 dias", counts.get("acima_30")),
                  label_value("Sem previsao", counts.get("sem_previsao"))]],
                [35.2 * mm] * 5,
                background=red_soft,
            ),
            Spacer(1, 6),
        ])
        rows = [[paragraph("Viagem", "BiHead"), paragraph("Placa", "BiHead"), paragraph("Motorista", "BiHead"),
                 paragraph("Chegada prevista", "BiHead"), paragraph("Dias", "BiHead"), paragraph("Adiantado", "BiHead")]]
        for item in backlog["items"][:12]:
            rows.append([
                paragraph(item.get("trip"), "BiSmall"),
                paragraph(item.get("plate"), "BiSmall"),
                paragraph(item.get("driver"), "BiSmall"),
                paragraph(item.get("forecast_display"), "BiSmall"),
                paragraph(item.get("days_overdue"), "BiSmall"),
                paragraph(item.get("advance_display"), "BiSmall"),
            ])
        story.append(table(rows, [22 * mm, 28 * mm, 34 * mm, 34 * mm, 18 * mm, 30 * mm], header=True, repeat_rows=1, font_size=6.8))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(muted)
        canvas.drawString(document.leftMargin, 8.5 * mm, f"BI de Viagens | {_text(snapshot.scope_label)}")
        canvas.drawRightString(A4[0] - document.rightMargin, 8.5 * mm, f"Pagina {canvas.getPageNumber()}")
        canvas.restoreState()

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=17 * mm, rightMargin=17 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"BI de Viagens - {_text(snapshot.scope_label)}",
    )
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
