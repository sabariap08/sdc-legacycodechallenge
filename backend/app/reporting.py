import io
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

PRIMARY = colors.HexColor("#1f2937")
HEADER_BG = colors.HexColor("#0f1117")


def _fmt(dt):
    if not dt:
        return "N/A"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return dt
    if dt.tzinfo:
        dt = dt.replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M")


def _build_doc(title, subtitle):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        rightMargin=0.7 * inch, leftMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleX", parent=styles["Title"], fontSize=20, spaceAfter=4)
    subtitle_style = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=10, textColor=colors.grey, spaceAfter=8)
    h2_style = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13, spaceBefore=14, spaceAfter=6, textColor=PRIMARY)
    cell_style = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=9, leading=12)

    story = []
    story.append(Paragraph(title, title_style))
    story.append(Paragraph(f"Generated: {subtitle}", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e5e7eb"), spaceAfter=8))
    return doc, story, styles, h2_style, cell_style


def _make_table(rows, col_widths, font_size=9):
    tbl = Table(rows, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return tbl


def challenge_report_pdf(data: dict) -> bytes:
    doc, story, styles, h2, cell = _build_doc("Legacy Code Rescue - Challenges Report", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))
    challenges = data.get("challenges", [])
    story.append(Paragraph("Challenge Summary", h2))
    story.append(Paragraph(f"Total Challenges: {len(challenges)}", styles["Normal"]))
    story.append(Spacer(1, 8))

    if challenges:
        rows = [["#", "Challenge Code", "Challenge Name", "Files", "Created"]]
        for i, c in enumerate(challenges, 1):
            rows.append([
                str(i),
                c.get("challenge_code", ""),
                c.get("challenge_name", ""),
                str(c.get("file_count", 0) or 0),
                _fmt(c.get("created_at")),
            ])
        story.append(_make_table(rows, [0.4 * inch, 1.5 * inch, 2.6 * inch, 0.7 * inch, 1.2 * inch]))
    else:
        story.append(Paragraph("No challenges have been uploaded.", styles["Normal"]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, rightMargin=0.7 * inch, leftMargin=0.7 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch)
    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


def teams_report_pdf(data: dict) -> bytes:
    doc, story, styles, h2, cell = _build_doc("Legacy Code Rescue - Teams Report", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))
    teams = data.get("teams", [])
    story.append(Paragraph("Team Details", h2))
    story.append(Paragraph(f"Total Teams: {len(teams)}", styles["Normal"]))
    story.append(Spacer(1, 8))

    if not teams:
        story.append(Paragraph("No teams have been created.", styles["Normal"]))
    else:
        for t in teams:
            name = t.get("team_name", "")
            code = t.get("team_code", "")
            members = t.get("members", [])
            count = t.get("team_count", len(members))
            story.append(Paragraph(f"<b>{name}</b> <font size=8 color=#6b7280>({code})</font> &mdash; {count} member(s)", h2))
            if members:
                mrows = [["#", "Member Name", "Email"]]
                for i, m in enumerate(members, 1):
                    mrows.append([str(i), m.get("name", ""), m.get("email", "")])
                story.append(_make_table(mrows, [0.5 * inch, 2.5 * inch, 3.0 * inch], font_size=9))
            else:
                story.append(Paragraph("No members recorded.", cell))
            story.append(Spacer(1, 10))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, rightMargin=0.7 * inch, leftMargin=0.7 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch)
    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


def allocations_report_pdf(data: dict) -> bytes:
    doc, story, styles, h2, cell = _build_doc("Legacy Code Rescue - Allocated Challenges Report", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))
    allocations = data.get("allocations", [])
    story.append(Paragraph("Challenge Allocation", h2))
    story.append(Paragraph(f"Total Allocation Entries: {len(allocations)}", styles["Normal"]))
    story.append(Spacer(1, 8))

    if allocations:
        rows = [["Team", "Members", "Allocated Challenge", "Challenge Code", "Status"]]
        for a in allocations:
            status = "Allocated" if a.get("challenge_code") else "Not Allocated"
            rows.append([
                a.get("team_name", ""),
                str(a.get("member_count", 0)),
                a.get("challenge_name", "") or "-",
                a.get("challenge_code", "") or "-",
                status,
            ])
        story.append(_make_table(rows, [1.6 * inch, 0.8 * inch, 1.8 * inch, 1.2 * inch, 1.0 * inch], font_size=8))
    else:
        story.append(Paragraph("No challenge allocations have been made.", styles["Normal"]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, rightMargin=0.7 * inch, leftMargin=0.7 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch)
    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


def status_report_pdf(data: dict) -> bytes:
    doc, story, styles, h2, cell = _build_doc("Legacy Code Rescue - Status Report", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))

    stats = data.get("stats", {})
    story.append(Paragraph("Platform Status", h2))
    story.append(Paragraph(f"Total Challenges: {stats.get('total_challenges', 0)}", styles["Normal"]))
    story.append(Paragraph(f"Total Teams: {stats.get('total_teams', 0)}", styles["Normal"]))
    story.append(Paragraph(f"Total Team Members: {stats.get('total_team_members', 0)}", styles["Normal"]))
    story.append(Paragraph(f"Allocated Teams: {stats.get('allocated_teams', 0)}", styles["Normal"]))
    story.append(Paragraph(f"Unallocated Teams: {stats.get('unallocated_teams', 0)}", styles["Normal"]))
    story.append(Paragraph(f"Total Releases: {stats.get('total_releases', 0)}", styles["Normal"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Allocation Status by Team", h2))
    allocations = data.get("allocations", [])
    if allocations:
        rows = [["Team", "Allocated Challenge", "Challenge Code", "Status"]]
        for a in allocations:
            status = "Allocated" if a.get("challenge_code") else "Not Allocated"
            rows.append([
                a.get("team_name", ""),
                a.get("challenge_name", "") or "-",
                a.get("challenge_code", "") or "-",
                status,
            ])
        story.append(_make_table(rows, [1.6 * inch, 1.8 * inch, 1.2 * inch, 1.0 * inch], font_size=8))
    else:
        story.append(Paragraph("No teams have been allocated a challenge.", styles["Normal"]))

    story.append(Paragraph("Release History", h2))
    releases = data.get("releases", [])
    if releases:
        rows = [["Team", "Challenge", "Recipients", "Status", "Released At"]]
        for r in releases:
            rows.append([
                r.get("team_name", ""),
                r.get("challenge_name", "") or r.get("challenge_code", ""),
                ", ".join(r.get("recipients", [])),
                r.get("status", ""),
                _fmt(r.get("released_at")),
            ])
        story.append(_make_table(rows, [1.4 * inch, 1.4 * inch, 2.0 * inch, 0.8 * inch, 1.4 * inch], font_size=8))
    else:
        story.append(Paragraph("No release emails have been sent.", styles["Normal"]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, rightMargin=0.7 * inch, leftMargin=0.7 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch)
    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
