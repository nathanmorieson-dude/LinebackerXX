from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

SAGE = HexColor('#7FA77F')
TEXT_DARK = HexColor('#3D3935')
TEXT_MED = HexColor('#6B6560')


def build_pdf(output_path: str = 'The_Calm_Classroom_System.pdf') -> None:
    doc = SimpleDocTemplate(output_path, pagesize=LETTER)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=30,
        textColor=SAGE,
        alignment=TA_CENTER,
        leading=36,
    )
    subtitle_style = ParagraphStyle(
        'Subtitle',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=14,
        textColor=TEXT_MED,
        alignment=TA_CENTER,
        leading=20,
    )
    body_style = ParagraphStyle(
        'Body',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=11,
        textColor=TEXT_DARK,
        leading=16,
    )

    story = [
        Spacer(1, 120),
        Paragraph('The Calm Classroom System', title_style),
        Spacer(1, 12),
        Paragraph('A Behaviour Management Toolkit for 3-5 Year Olds', subtitle_style),
        Spacer(1, 36),
        Paragraph(
            'This starter file is a clean, runnable ReportLab base. '
            'You can now safely paste and expand your full toolkit content from here.',
            body_style,
        ),
    ]
    doc.build(story)
    print(f'Built: {output_path}')


if __name__ == '__main__':
    build_pdf()
