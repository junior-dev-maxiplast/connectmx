"""
PDF do BI de Romaneios.

Mesma linguagem dos relatórios do BI do TI e do BI de Viagens, com o conteúdo
de produtividade de contagem de pallets: volume, etapas, filiais e ranking de
colaboradores.
"""

from xml.sax.saxutils import escape

from django.utils import timezone

from .bi_pdf import BiPdf, _text


def build_romaneio_bi_pdf(dashboard, snapshot):
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, TableStyle

    pdf = BiPdf()
    styles, paragraph, label_value, table = pdf.styles, pdf.paragraph, pdf.label_value, pdf.table
    green, green_soft, blue_soft = pdf.green, pdf.green_soft, pdf.blue_soft

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

    pdf.ai_section(story, ai, snapshot, blue_tones=("capacity",))

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

    return pdf.build(story, "BI de Romaneios", snapshot.scope_label)
