"""
PDF executivo da Central de Decisão.

Mesma linguagem visual dos relatórios dos 4 painéis (`romaneio_bi_pdf.py` e
os 3 equivalentes) — a Central era o único painel do Dashes sem exportação em
PDF, o que não fazia sentido justamente para a tela pensada para ser levada a
uma reunião. Não depende de nenhum snapshot salvo: recalcula tudo na hora,
igual à própria tela (ver `decision_center.py`).
"""

from xml.sax.saxutils import escape

from django.utils import timezone

from .bi_pdf import _text


_HEALTH_LABELS = {"saudavel": "Saudável", "atencao": "Atenção", "critico": "Crítico"}
_SEVERITY_LABELS = {"atencao": "Atenção", "critico": "Crítico"}


def build_decision_center_pdf(dashboards, alerts, resolved_progress, generated_by=None):
    import io

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    navy = colors.HexColor("#071821")
    navy_soft = colors.HexColor("#0d2732")
    border = colors.HexColor("#d9e4e8")
    muted = colors.HexColor("#647983")
    body_color = colors.HexColor("#263c46")

    # Strings soltas (não `Color`) porque `<font color=''>` do reportlab
    # precisa do hex literal, e `Color.hexval()` devolve `0xRRGGBBAA` — não é
    # o formato que a tag aceita. Mesmo padrão dos outros PDFs do Dashes
    # (`customer_dna_pdf.py`, `romaneio_bi_pdf.py`), que já usam string fixa.
    RED = "#c1394a"
    AMBER = "#a5730f"
    GREEN = "#2f8f52"
    MUTED = "#647983"

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="DcTitle", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=20, leading=23, textColor=navy, spaceAfter=3))
    styles.add(ParagraphStyle(name="DcSubtitle", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, textColor=muted))
    styles.add(ParagraphStyle(name="DcSection", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=navy, spaceBefore=10, spaceAfter=7))
    styles.add(ParagraphStyle(name="DcBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=body_color))
    styles.add(ParagraphStyle(name="DcSmall", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.4, leading=9.6, textColor=body_color))
    styles.add(ParagraphStyle(name="DcMuted", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=muted))
    styles.add(ParagraphStyle(name="DcCardTitle", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=navy))
    styles.add(ParagraphStyle(name="DcHead", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=7.3, leading=8.5, textColor=colors.white))

    def paragraph(value, style="DcBody"):
        return Paragraph(escape(_text(value)), styles[style])

    def table(rows, widths, header=False, repeat_rows=0, font_size=7.6):
        built = Table(rows, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
        commands = [
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
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

    generated_at = timezone.localtime()
    who = f" | Gerado por {_text(generated_by)}" if generated_by else ""
    story = [
        Paragraph("CONNECTMX DASHES / CENTRAL DE DECISÃO", styles["DcSubtitle"]),
        Paragraph("Visão executiva dos painéis", styles["DcTitle"]),
        Paragraph(f"Gerado em {generated_at.strftime('%d/%m/%Y %H:%M')}{who}", styles["DcMuted"]),
        Spacer(1, 9),
    ]

    if alerts:
        story.append(Paragraph("Alertas ativos", styles["DcSection"]))
        rows = [[paragraph("Painel", "DcHead"), paragraph("Alerta", "DcHead"), paragraph("Detalhe", "DcHead")]]
        for alert in alerts:
            severity_color = RED if alert.severity == "critico" else AMBER
            rows.append([
                paragraph(alert.dashboard.name, "DcSmall"),
                Paragraph(
                    f"<font color='{severity_color}'><b>"
                    f"{escape(_SEVERITY_LABELS.get(alert.severity, alert.severity))}</b></font><br/>"
                    f"{escape(_text(alert.title))}",
                    styles["DcSmall"],
                ),
                paragraph(alert.message, "DcSmall"),
            ])
        story.extend([table(rows, [30 * mm, 55 * mm, 85 * mm], header=True, repeat_rows=1), Spacer(1, 6)])
    else:
        story.append(Paragraph("Nenhum alerta ativo no momento.", styles["DcMuted"]))

    story.append(Paragraph("Painéis", styles["DcSection"]))
    for dashboard in dashboards:
        card = getattr(dashboard, "center_card", None) or {}
        story.append(Paragraph(dashboard.name, styles["DcCardTitle"]))
        if dashboard.slug == "customer-dna":
            counts = card.get("health_counts") or {}
            story.append(paragraph(
                f"Clientes analisados: {card.get('analyzed_count', 0)} | "
                f"Em estado crítico: {counts.get('critico', 0)}",
                "DcSmall",
            ))
        elif card.get("available"):
            kpi_text = " | ".join(f"{kpi['label']}: {kpi['value']}" for kpi in card.get("kpis") or [])
            story.append(paragraph(kpi_text, "DcSmall"))
            if card.get("ai_health"):
                health_color = {"saudavel": GREEN, "atencao": AMBER, "critico": RED}.get(card["ai_health"], MUTED)
                story.append(Paragraph(
                    f"<font color='{health_color}'><b>"
                    f"{escape(_HEALTH_LABELS.get(card['ai_health'], card['ai_health']))}</b></font> — "
                    f"{escape(_text(card.get('ai_summary')))}",
                    styles["DcSmall"],
                ))
        else:
            story.append(paragraph(f"Indisponível no momento da geração — {card.get('error') or ''}", "DcMuted"))
        story.append(Spacer(1, 5))

    pending_count = sum(len(getattr(dashboard, "center_actions", None) or []) for dashboard in dashboards)
    if pending_count:
        story.append(Paragraph("Ações pendentes", styles["DcSection"]))
        rows = [[paragraph("Painel", "DcHead"), paragraph("Ação", "DcHead"), paragraph("Status", "DcHead")]]
        for dashboard in dashboards:
            for action in (getattr(dashboard, "center_actions", None) or [])[:8]:
                rows.append([
                    paragraph(dashboard.name, "DcSmall"),
                    paragraph(action.description, "DcSmall"),
                    paragraph(dict(action.STATUS_CHOICES).get(action.status, action.status), "DcSmall"),
                ])
        story.extend([table(rows, [30 * mm, 115 * mm, 25 * mm], header=True, repeat_rows=1), Spacer(1, 6)])

    if resolved_progress:
        story.append(Paragraph("Isso realmente melhorou?", styles["DcSection"]))
        rows = [[paragraph("Ação concluída", "DcHead"), paragraph("Antes", "DcHead"),
                 paragraph("Agora", "DcHead"), paragraph("Resultado", "DcHead")]]
        for action, progress in resolved_progress:
            if progress["improved"] is True:
                result, color = "Melhorou", GREEN
            elif progress["improved"] is False:
                result, color = "Piorou", RED
            else:
                result, color = "Estável", MUTED
            rows.append([
                paragraph(action.description, "DcSmall"),
                paragraph(f"{progress['before']:.0f}", "DcSmall"),
                paragraph(f"{progress['after']:.0f}", "DcSmall"),
                Paragraph(f"<font color='{color}'><b>{result}</b></font>", styles["DcSmall"]),
            ])
        story.append(table(rows, [95 * mm, 25 * mm, 25 * mm, 25 * mm], header=True, repeat_rows=1))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(muted)
        canvas.drawString(document.leftMargin, 8.5 * mm, "Central de Decisão | ConnectMX Dashes")
        canvas.drawRightString(A4[0] - document.rightMargin, 8.5 * mm, f"Página {canvas.getPageNumber()}")
        canvas.restoreState()

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=17 * mm, rightMargin=17 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
        title="Central de Decisão - ConnectMX Dashes",
    )
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
