"""
utils/pdf.py — Engagement letter PDF generation using ReportLab.

Generates a professional, firm-branded PDF engagement letter and saves
it to uploads/letters/<client_id>/<letter_id>.pdf.
"""

import os
from datetime import datetime, timezone
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
    Table, TableStyle, KeepTogether
)


# ─── Colour palette (matches frontend design system) ─────────────────────────

NAVY     = colors.HexColor("#0f1a2e")   # headings / firm name
BLUE     = colors.HexColor("#2962cc")   # accent lines and labels
GREY     = colors.HexColor("#64748b")   # sub-labels / muted text
LIGHTGREY = colors.HexColor("#e2e8f0")  # table borders / dividers
WHITE    = colors.white
BLACK    = colors.HexColor("#1e293b")   # body text


# ─── Style sheet ─────────────────────────────────────────────────────────────

def _build_styles():
    base = getSampleStyleSheet()

    styles = {
        "firm_name": ParagraphStyle(
            "FirmName",
            fontName="Helvetica-Bold",
            fontSize=18,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceAfter=2,
        ),
        "firm_tagline": ParagraphStyle(
            "FirmTagline",
            fontName="Helvetica",
            fontSize=8,
            textColor=GREY,
            alignment=TA_CENTER,
            spaceAfter=0,
        ),
        "doc_title": ParagraphStyle(
            "DocTitle",
            fontName="Helvetica-Bold",
            fontSize=13,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceBefore=16,
            spaceAfter=4,
        ),
        "meta_label": ParagraphStyle(
            "MetaLabel",
            fontName="Helvetica",
            fontSize=8,
            textColor=GREY,
            alignment=TA_LEFT,
        ),
        "meta_value": ParagraphStyle(
            "MetaValue",
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=BLACK,
            alignment=TA_LEFT,
        ),
        "section_heading": ParagraphStyle(
            "SectionHeading",
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=BLUE,
            spaceBefore=14,
            spaceAfter=4,
            leading=12,
        ),
        "body": ParagraphStyle(
            "Body",
            fontName="Helvetica",
            fontSize=9,
            textColor=BLACK,
            leading=14,
            spaceAfter=4,
            alignment=TA_JUSTIFY,
        ),
        "body_bold": ParagraphStyle(
            "BodyBold",
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=BLACK,
            leading=14,
            spaceAfter=4,
        ),
        "footer": ParagraphStyle(
            "Footer",
            fontName="Helvetica",
            fontSize=7,
            textColor=GREY,
            alignment=TA_CENTER,
        ),
        "sig_label": ParagraphStyle(
            "SigLabel",
            fontName="Helvetica",
            fontSize=8,
            textColor=GREY,
            alignment=TA_LEFT,
        ),
        "sig_name": ParagraphStyle(
            "SigName",
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=BLACK,
            alignment=TA_LEFT,
        ),
    }
    return styles


# ─── Main generator ──────────────────────────────────────────────────────────

def generate_engagement_letter(letter, client, firm, upload_folder: str) -> str:
    """
    Generate a PDF engagement letter.

    Args:
        letter:        EngagementLetter ORM object
        client:        Client ORM object
        firm:          LawFirm ORM object
        upload_folder: Base upload directory (UPLOAD_FOLDER from config)

    Returns:
        Relative path to the saved PDF (stored in EngagementLetter.pdf_path).
    """
    # ── Prepare directory ────────────────────────────────────────────────────
    letters_dir = os.path.join(upload_folder, "letters", client.client_id)
    os.makedirs(letters_dir, exist_ok=True)
    filename = f"{letter.letter_id}.pdf"
    filepath = os.path.join(letters_dir, filename)
    rel_path = os.path.join("letters", client.client_id, filename)

    # ── Document setup ───────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        title=f"Engagement Letter — {client.full_name}",
        author=firm.firm_name,
    )

    styles = _build_styles()
    story  = []
    W      = A4[0] - 44 * mm   # usable page width

    # ── Header: Firm name + accent rule ─────────────────────────────────────
    story.append(Paragraph(firm.firm_name.upper(), styles["firm_name"]))
    story.append(Paragraph("Legal Services · UAE", styles["firm_tagline"]))
    story.append(Spacer(1, 3 * mm))
    story.append(HRFlowable(width="100%", thickness=2, color=BLUE, spaceAfter=0))
    story.append(HRFlowable(width="100%", thickness=0.5, color=LIGHTGREY, spaceBefore=1, spaceAfter=0))

    # ── Document title ───────────────────────────────────────────────────────
    story.append(Paragraph("CLIENT ENGAGEMENT LETTER", styles["doc_title"]))

    # ── Meta table: date / reference / client ────────────────────────────────
    issue_date = datetime.now(timezone.utc).strftime("%d %B %Y")
    meta_data = [
        [
            Paragraph("DATE", styles["meta_label"]),
            Paragraph("REFERENCE", styles["meta_label"]),
            Paragraph("CLIENT", styles["meta_label"]),
        ],
        [
            Paragraph(issue_date, styles["meta_value"]),
            Paragraph(client.reference_id, styles["meta_value"]),
            Paragraph(client.full_name, styles["meta_value"]),
        ],
    ]
    meta_col_widths = [W * 0.25, W * 0.3, W * 0.45]
    meta_table = Table(meta_data, colWidths=meta_col_widths)
    meta_table.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LINEABOVE",     (0, 0), (-1, 0), 0.5, LIGHTGREY),
        ("LINEBELOW",     (0, -1), (-1, -1), 0.5, LIGHTGREY),
    ]))
    story.append(Spacer(1, 4 * mm))
    story.append(meta_table)
    story.append(Spacer(1, 4 * mm))

    # ── Opening paragraph ────────────────────────────────────────────────────
    story.append(Paragraph(
        f"Dear <b>{client.full_name}</b>,",
        styles["body"]
    ))
    story.append(Paragraph(
        f"Thank you for choosing <b>{firm.firm_name}</b>. This letter confirms the terms "
        "under which we will provide legal services to you. Please read this document "
        "carefully. By signing below, you acknowledge and agree to the terms set out herein.",
        styles["body"]
    ))

    # ── Matter Type ──────────────────────────────────────────────────────────
    if letter.matter_type:
        story.append(_section_heading("1.  NATURE OF MATTER", styles))
        story.append(Paragraph(letter.matter_type, styles["body"]))

    # ── Scope of Work ────────────────────────────────────────────────────────
    if letter.scope_of_work:
        story.append(_section_heading("2.  SCOPE OF WORK", styles))
        story.append(Paragraph(
            _nl_to_para(letter.scope_of_work), styles["body"]
        ))

    # ── Fee Structure ────────────────────────────────────────────────────────
    story.append(_section_heading("3.  FEES AND BILLING", styles))

    fee_rows = []
    if letter.billing_type:
        fee_rows.append(("Billing Type", letter.billing_type.title()))
    if letter.retainer_amount is not None:
        amount = float(letter.retainer_amount)
        fee_rows.append(("Retainer Amount", f"AED {amount:,.2f}"))
    if letter.fee_structure:
        story.append(Paragraph(_nl_to_para(letter.fee_structure), styles["body"]))

    if fee_rows:
        fee_table_data = [
            [Paragraph(k, styles["meta_label"]), Paragraph(v, styles["meta_value"])]
            for k, v in fee_rows
        ]
        fee_col_widths = [W * 0.38, W * 0.62]
        fee_table = Table(fee_table_data, colWidths=fee_col_widths)
        fee_table.setStyle(TableStyle([
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
            ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#f8fafc"), WHITE]),
            ("GRID",          (0, 0), (-1, -1), 0.5, LIGHTGREY),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(fee_table)
        story.append(Spacer(1, 2 * mm))

    # ── Timeline ─────────────────────────────────────────────────────────────
    if letter.timeline:
        story.append(_section_heading("4.  TIMELINE AND MILESTONES", styles))
        story.append(Paragraph(_nl_to_para(letter.timeline), styles["body"]))

    # ── Standard terms ───────────────────────────────────────────────────────
    next_section = 5 if (letter.matter_type or letter.scope_of_work or letter.timeline) else 4
    story.append(_section_heading(f"{next_section}.  GENERAL TERMS", styles))
    story.extend(_standard_terms(styles))

    # ── Signature block ──────────────────────────────────────────────────────
    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=LIGHTGREY))
    story.append(Spacer(1, 4 * mm))
    story.append(_signature_block(firm, client, styles, W))

    # ── Footer ───────────────────────────────────────────────────────────────
    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=LIGHTGREY))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        f"{firm.firm_name} · Confidential · Generated {issue_date}",
        styles["footer"]
    ))

    doc.build(story)
    return rel_path


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _section_heading(text: str, styles: dict):
    return Paragraph(text, styles["section_heading"])


def _nl_to_para(text: str) -> str:
    """Convert newlines to ReportLab <br/> tags."""
    return text.replace("\r\n", "<br/>").replace("\n", "<br/>")


def _standard_terms(styles: dict) -> list:
    """Returns standard engagement letter clauses as Paragraph objects."""
    terms = [
        ("<b>Confidentiality.</b> All information exchanged between the firm and the client "
         "shall be kept strictly confidential, except where disclosure is required by law or "
         "regulatory authority."),
        ("<b>Communication.</b> The firm will communicate with you via the contact details "
         "you have provided. It is your responsibility to notify us of any changes to your "
         "contact information."),
        ("<b>Conflict of Interest.</b> We have conducted a conflict of interest check prior "
         "to accepting your matter. Should any conflict arise during the engagement, we will "
         "notify you immediately."),
        ("<b>Governing Law.</b> This engagement letter shall be governed by the laws of the "
         "United Arab Emirates. Any disputes arising hereunder shall be subject to the "
         "exclusive jurisdiction of the courts of the UAE."),
        ("<b>Termination.</b> Either party may terminate this engagement upon reasonable "
         "written notice. Any fees incurred up to the date of termination remain payable."),
    ]
    return [Paragraph(t, styles["body"]) for t in terms]


def _signature_block(firm, client, styles: dict, width) -> Table:
    """Two-column signature block: firm on left, client on right."""
    col_w = width * 0.46
    gap_w = width * 0.08

    sig_line = HRFlowable(width=col_w, thickness=0.5, color=GREY)

    firm_col = [
        Spacer(1, 12 * mm),
        sig_line,
        Spacer(1, 2),
        Paragraph(firm.firm_name, styles["sig_name"]),
        Paragraph("Authorised Signatory", styles["sig_label"]),
        Spacer(1, 4),
        Paragraph("Date: ___________________", styles["sig_label"]),
    ]

    client_col = [
        Spacer(1, 12 * mm),
        sig_line,
        Spacer(1, 2),
        Paragraph(client.full_name, styles["sig_name"]),
        Paragraph("Client", styles["sig_label"]),
        Spacer(1, 4),
        Paragraph("Date: ___________________", styles["sig_label"]),
    ]

    # Wrap columns in inner tables
    def col_table(items):
        rows = [[item] for item in items]
        t = Table(rows, colWidths=[col_w])
        t.setStyle(TableStyle([
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        return t

    outer = Table(
        [[col_table(firm_col), Spacer(gap_w, 1), col_table(client_col)]],
        colWidths=[col_w, gap_w, col_w],
    )
    outer.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
    ]))
    return outer
