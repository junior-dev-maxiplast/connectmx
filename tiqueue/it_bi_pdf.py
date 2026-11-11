"""
PDF do BI do TI.

Mesma linguagem do relatório do DNA do Cliente, mas com o conteúdo do service
desk: volume, SLA, satisfação, apontamento e as análises processadas.
"""

from xml.sax.saxutils import escape

from django.utils import timezone

from .bi_pdf import BiPdf, _text


def build_it_bi_pdf(dashboard, snapshot):
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, TableStyle

    pdf = BiPdf()
    styles, paragraph, label_value, table = pdf.styles, pdf.paragraph, pdf.label_value, pdf.table
    green, green_soft, red_soft = pdf.green, pdf.green_soft, pdf.red_soft

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
            f"{escape(_text(scope['period']['full_label']))} | {escape(_text(scope['company']['label']))}"
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

    pdf.ai_section(story, ai, snapshot, blue_tones=("capacity",))

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

    return pdf.build(story, "BI do TI", snapshot.scope_label)
