"""Base visual comum aos PDFs dos BIs (Compras, Romaneios, Viagens, TI).

Os quatro geradores repetiam a mesma paleta, os mesmos estilos, a mesma tabela
e a mesma seção "Análise da IA"; só o miolo de indicadores muda de um BI para
outro. O reportlab é importado só na hora de gerar, como antes.
"""

import io
from xml.sax.saxutils import escape


def _text(value, fallback="-"):
    if value is None or value == "":
        return fallback
    return str(value)


class BiPdf:
    def __init__(self):
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

        self.colors = colors
        self.navy = colors.HexColor("#071821")
        self.navy_soft = colors.HexColor("#0d2732")
        self.green = colors.HexColor("#57c96d")
        self.green_soft = colors.HexColor("#e8f7ea")
        self.blue_soft = colors.HexColor("#edf5ff")
        self.amber_soft = colors.HexColor("#fff5df")
        self.red_soft = colors.HexColor("#fff0f0")
        self.border = colors.HexColor("#d9e4e8")
        self.muted = colors.HexColor("#647983")
        body_color = colors.HexColor("#263c46")

        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="BiTitle", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=20, leading=23, textColor=self.navy, spaceAfter=3))
        styles.add(ParagraphStyle(name="BiSubtitle", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, textColor=self.muted))
        styles.add(ParagraphStyle(name="BiSection", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=self.navy, spaceBefore=8, spaceAfter=7))
        styles.add(ParagraphStyle(name="BiBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=body_color))
        styles.add(ParagraphStyle(name="BiSmall", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.4, leading=9.4, textColor=body_color))
        styles.add(ParagraphStyle(name="BiMuted", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=self.muted))
        styles.add(ParagraphStyle(name="BiCardTitle", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=10.5, textColor=self.navy))
        styles.add(ParagraphStyle(name="BiHead", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=7.3, leading=8.5, textColor=colors.white))
        self.styles = styles

    def paragraph(self, value, style="BiBody"):
        from reportlab.platypus import Paragraph

        return Paragraph(escape(_text(value)), self.styles[style])

    def label_value(self, label, value, style="BiBody"):
        from reportlab.platypus import Paragraph

        return Paragraph(f"<b>{escape(label)}</b><br/>{escape(_text(value))}", self.styles[style])

    def table(self, rows, widths, header=False, background=None, repeat_rows=0, font_size=7.4):
        from reportlab.platypus import Table, TableStyle

        colors = self.colors
        built = Table(rows, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
        commands = [
            ("BACKGROUND", (0, 0), (-1, -1), background or colors.white),
            ("BOX", (0, 0), (-1, -1), .7, self.border),
            ("INNERGRID", (0, 0), (-1, -1), .45, self.border),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ]
        if header:
            commands.extend([
                ("BACKGROUND", (0, 0), (-1, 0), self.navy_soft),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ])
            for index in range(1, len(rows)):
                if index % 2 == 0:
                    commands.append(("BACKGROUND", (0, index), (-1, index), colors.HexColor("#f6f9fa")))
        built.setStyle(TableStyle(commands))
        return built

    def ai_section(self, story, ai, snapshot, blue_tones=()):
        """Seção "Análise da IA": resumo, risco/oportunidade, insights e ações.

        `blue_tones` são os tipos de insight próprios de cada BI (custo,
        fornecedor, frota, capacidade) que ganham o fundo azul.
        """
        if not ai:
            return
        from reportlab.lib.units import mm
        from reportlab.platypus import KeepTogether, Paragraph, Spacer

        label_value, table = self.label_value, self.table
        story.append(Paragraph("Análise da IA", self.styles["BiSection"]))
        story.extend([
            table(
                [[label_value("Situação geral", _text(ai.get("health")).title()),
                  label_value("Modelo", f"{snapshot.ai_model or '-'} | {snapshot.ai_total_tokens} tokens")]],
                [116 * mm, 60 * mm],
                background=self.blue_soft,
            ),
            Spacer(1, 6),
            self.paragraph(ai.get("executive_summary")),
            Spacer(1, 6),
            table(
                [[label_value("Principal risco", ai.get("principal_risk")),
                  label_value("Principal oportunidade", ai.get("principal_opportunity"))]],
                [88 * mm, 88 * mm],
                background=self.amber_soft,
            ),
            Spacer(1, 8),
        ])

        tones = {"positive": self.green_soft, "attention": self.amber_soft, "risk": self.red_soft,
                 "quality": self.amber_soft, **{tone: self.blue_soft for tone in blue_tones}}
        for insight in ai.get("insights") or []:
            evidence = "; ".join(_text(item) for item in insight.get("evidence") or [])
            card = table(
                [[
                    Paragraph(
                        f"<b>{escape(_text(insight.get('title')))}</b><br/>{escape(_text(insight.get('summary')))}"
                        f"<br/><font color='#647983'>Evidências: {escape(evidence)}</font>",
                        self.styles["BiSmall"],
                    ),
                    label_value("Ação recomendada", insight.get("recommended_action"), "BiSmall"),
                ]],
                [116 * mm, 60 * mm],
                background=tones.get(insight.get("type"), self.colors.white),
            )
            story.append(KeepTogether([card, Spacer(1, 5)]))

        actions = ai.get("recommended_actions") or []
        if actions:
            rows = [[self.paragraph(str(index), "BiCardTitle"), self.paragraph(action)]
                    for index, action in enumerate(actions, start=1)]
            story.extend([Spacer(1, 3), table(rows, [12 * mm, 164 * mm], background=self.green_soft), Spacer(1, 8)])

    def build(self, story, bi_name, scope_label):
        """Monta o A4 com rodapé "<BI> | <recorte>" e número de página."""
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate

        def footer(canvas, document):
            canvas.saveState()
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(self.muted)
            canvas.drawString(document.leftMargin, 8.5 * mm, f"{bi_name} | {_text(scope_label)}")
            canvas.drawRightString(A4[0] - document.rightMargin, 8.5 * mm, f"Página {canvas.getPageNumber()}")
            canvas.restoreState()

        buffer = io.BytesIO()
        document = SimpleDocTemplate(
            buffer, pagesize=A4,
            leftMargin=17 * mm, rightMargin=17 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
            title=f"{bi_name} - {_text(scope_label)}",
        )
        document.build(story, onFirstPage=footer, onLaterPages=footer)
        return buffer.getvalue()
