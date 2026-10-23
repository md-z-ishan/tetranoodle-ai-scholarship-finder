#!/usr/bin/env python3
"""Script to generate realistic FICTIONAL resume and marksheet PDFs for testing.

Rules:
- Strictly labelled 'FICTIONAL SAMPLE - FOR TESTING PURPOSES ONLY' in headers and footers.
- Profile A (default / --profile rahul):
    Student: Rahul Sharma, Final-Year B.Tech CSE, Jalandhar, 8.2 CGPA out of 10.0.
    AI Projects: Neural Code Search, Edge Vision Guard.
    Interests: Canada, Germany, Singapore. Limited family budget.
- Profile B (--profile priya):
    Student: Priya Nair, Final-Year B.Sc. Biosciences, Kochi, 7.4 CGPA out of 10.0.
    No GitHub. Interests: Netherlands, Ireland, Australia. Partial family funding.
- ZERO scholarship data included in either profile.
"""

import argparse
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "samples"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Profile A – Rahul Sharma (B.Tech CSE, AI/ML, Canada/Germany/Singapore)
# ---------------------------------------------------------------------------

def create_fictional_resume(output_path: Path):
    doc = SimpleDocTemplate(str(output_path), pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    story = []

    header_style = ParagraphStyle("Hdr", parent=styles["Heading1"], fontSize=18, leading=22, textColor=colors.HexColor("#1A365D"))
    sub_style = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#718096"))
    sec_style = ParagraphStyle("Sec", parent=styles["Heading2"], fontSize=12, leading=16, textColor=colors.HexColor("#2B6CB0"), spaceBefore=10, spaceAfter=4)
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14, textColor=colors.HexColor("#2D3748"))
    fictional_watermark = ParagraphStyle("Fict", parent=styles["Normal"], fontSize=8, textColor=colors.red, alignment=1)

    story.append(Paragraph("RAHUL SHARMA", header_style))
    story.append(Paragraph("Jalandhar, Punjab, India | rahul.cse.fictional@example.com | github.com/rahulsharma-ai", sub_style))
    story.append(Paragraph("<b>[ FICTIONAL SAMPLE FOR TESTING PURPOSES ONLY - CONTAINS NO SCHOLARSHIP DATA ]</b>", fictional_watermark))
    story.append(Spacer(1, 10))

    # Education
    story.append(Paragraph("EDUCATION", sec_style))
    story.append(Paragraph("<b>Bachelor of Technology in Computer Science and Engineering</b> (Final Year, Graduating 2026)", body_style))
    story.append(Paragraph("Lovely Professional University, Jalandhar | Cumulative CGPA: <b>8.2 / 10.0</b>", body_style))
    story.append(Spacer(1, 8))

    # Technical Skills
    story.append(Paragraph("TECHNICAL SKILLS", sec_style))
    story.append(Paragraph("<b>Languages &amp; Frameworks:</b> Python, C++, TypeScript, PyTorch, FastAPI, Docker, SQL", body_style))
    story.append(Paragraph("<b>Specializations:</b> Deep Learning, LLM Fine-Tuning, Computer Vision, Distributed Systems", body_style))
    story.append(Spacer(1, 8))

    # Projects
    story.append(Paragraph("KEY ENGINEERING PROJECTS", sec_style))
    story.append(Paragraph("<b>Neural Code Search Engine</b> (PyTorch, Transformers, FastAPI)", body_style))
    story.append(Paragraph("&bull; Built semantic code search utilizing AST embeddings and dual-encoder neural architecture.", body_style))
    story.append(Paragraph("&bull; Reduced query retrieval latency by 45% using FAISS vector indexing.", body_style))
    story.append(Spacer(1, 4))

    story.append(Paragraph("<b>Edge Vision Guard</b> (OpenCV, ONNX, Python)", body_style))
    story.append(Paragraph("&bull; Deployed lightweight real-time object detection models for resource-constrained edge devices.", body_style))
    story.append(Spacer(1, 8))

    # Target & Aspirations
    story.append(Paragraph("ACADEMIC OBJECTIVES &amp; INTERESTS", sec_style))
    story.append(Paragraph("Seeking fully funded Master's programs in Computer Science and Artificial Intelligence across <b>Canada, Germany, and Singapore</b>. Highly motivated to pursue thesis research with financial assistance due to modest family background.", body_style))
    story.append(Spacer(1, 15))

    story.append(Paragraph("<i>Verified Document: Fictional test profile for TetraNoodle Hackathon evaluation.</i>", fictional_watermark))
    doc.build(story)


def create_fictional_marksheet(output_path: Path):
    doc = SimpleDocTemplate(str(output_path), pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    story = []

    header_style = ParagraphStyle("Hdr", parent=styles["Heading1"], fontSize=16, leading=20, alignment=1, textColor=colors.HexColor("#1A202C"))
    sub_style = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=9, alignment=1, textColor=colors.HexColor("#4A5568"))
    fictional_watermark = ParagraphStyle("Fict", parent=styles["Normal"], fontSize=8, textColor=colors.red, alignment=1)

    story.append(Paragraph("OFFICIAL ACADEMIC TRANSCRIPT RECORD", header_style))
    story.append(Paragraph("Department of Computer Science &amp; Engineering | University Examination Board", sub_style))
    story.append(Paragraph("<b>[ FICTIONAL SAMPLE FOR TESTING PURPOSES ONLY - ZERO SCHOLARSHIP DATA ]</b>", fictional_watermark))
    story.append(Spacer(1, 15))

    student_data = [
        ["Student Name:", "Rahul Sharma", "Registration No:", "12204561 (Fictional)"],
        ["Program:", "B.Tech Computer Science &amp; Eng.", "Graduation Year:", "2026 (Expected)"],
        ["Institution:", "Lovely Professional University", "Status:", "Regular / Final Year"],
    ]
    t1 = Table(student_data, colWidths=[100, 160, 100, 160])
    t1.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#2D3748")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t1)
    story.append(Spacer(1, 12))

    # Semester grades table
    grades_data = [
        ["Course Code", "Course Title", "Credits", "Grade", "Grade Point"],
        ["CSE301", "Design and Analysis of Algorithms", "4", "A+", "9.0"],
        ["CSE305", "Operating Systems &amp; Virtualization", "4", "A", "8.0"],
        ["CSE412", "Artificial Intelligence &amp; Neural Nets", "4", "O", "10.0"],
        ["CSE418", "Natural Language Processing", "3", "A+", "9.0"],
        ["CSE422", "Distributed Cloud Computing", "3", "B+", "7.0"],
        ["CSE498", "Senior Capstone Research Project", "4", "A+", "9.0"],
    ]
    t2 = Table(grades_data, colWidths=[80, 240, 60, 60, 70])
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t2)
    story.append(Spacer(1, 15))

    # Summary
    summary_data = [
        ["Cumulative Grade Point Average (CGPA):", "8.2 / 10.0"],
        ["Total Credits Completed:", "142 / 160"],
        ["Academic Standing:", "First Division with Distinction"],
    ]
    t3 = Table(summary_data, colWidths=[240, 270])
    t3.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#1A365D")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t3)
    story.append(Spacer(1, 20))
    story.append(Paragraph("<i>Certified Registrar Seal: Fictional Test Document for Algorithm Evaluation.</i>", fictional_watermark))

    doc.build(story)


# ---------------------------------------------------------------------------
# Profile B – Priya Nair (B.Sc. Biosciences, Netherlands/Ireland/Australia)
# ---------------------------------------------------------------------------

def create_priya_resume(output_path: Path):
    doc = SimpleDocTemplate(str(output_path), pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    story = []

    header_style = ParagraphStyle("Hdr", parent=styles["Heading1"], fontSize=18, leading=22, textColor=colors.HexColor("#1A3D2B"))
    sub_style = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#718096"))
    sec_style = ParagraphStyle("Sec", parent=styles["Heading2"], fontSize=12, leading=16, textColor=colors.HexColor("#276749"), spaceBefore=10, spaceAfter=4)
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14, textColor=colors.HexColor("#2D3748"))
    watermark_style = ParagraphStyle("Fict", parent=styles["Normal"], fontSize=8, textColor=colors.red, alignment=1)

    story.append(Paragraph("PRIYA NAIR", header_style))
    story.append(Paragraph("Kochi, Kerala, India | priya.bio.fictional@example.com | (No GitHub account)", sub_style))
    story.append(Paragraph("<b>[ FICTIONAL SAMPLE FOR TESTING PURPOSES ONLY - CONTAINS NO SCHOLARSHIP DATA ]</b>", watermark_style))
    story.append(Spacer(1, 10))

    story.append(Paragraph("EDUCATION", sec_style))
    story.append(Paragraph("<b>Bachelor of Science in Biosciences (Biotechnology &amp; Molecular Biology)</b> (Final Year, Graduating 2026)", body_style))
    story.append(Paragraph("St. Xavier's College (Autonomous), Kochi | Cumulative CGPA: <b>7.4 / 10.0</b>", body_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph("TECHNICAL &amp; LABORATORY SKILLS", sec_style))
    story.append(Paragraph("<b>Lab Techniques:</b> PCR, Gel Electrophoresis, Cell Culture, ELISA, Western Blot, Flow Cytometry", body_style))
    story.append(Paragraph("<b>Bioinformatics:</b> BLAST, Clustal Omega, PyMOL, R (basic), Excel-based statistical analysis", body_style))
    story.append(Paragraph("<b>Languages:</b> English (fluent), Malayalam (native), Hindi (conversational)", body_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph("RESEARCH PROJECTS", sec_style))
    story.append(Paragraph("<b>Phytochemical Screening of Morinda citrifolia Leaf Extracts</b> (Final Year Dissertation)", body_style))
    story.append(Paragraph("&bull; Identified and quantified antimicrobial alkaloids using HPLC and spectrophotometry.", body_style))
    story.append(Paragraph("&bull; Compiled comparative analysis of 6 extraction solvents; results submitted for departmental journal.", body_style))
    story.append(Spacer(1, 4))

    story.append(Paragraph("<b>Gut Microbiome &amp; Metabolic Syndrome Review</b> (Independent Literature Synthesis)", body_style))
    story.append(Paragraph("&bull; Authored a 4,000-word critical review on Lactobacillus interventions in Type-2 diabetic mouse models.", body_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph("EXTRACURRICULAR &amp; COMMUNITY", sec_style))
    story.append(Paragraph("Volunteer Science Communicator – Kerala State Science and Technology Museum (2024–2025)", body_style))
    story.append(Paragraph("Secretary – College Biology Society; organized two inter-collegiate symposia (2024)", body_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph("ACADEMIC OBJECTIVES &amp; FUNDING", sec_style))
    story.append(Paragraph(
        "Seeking fully funded or substantially supported Master of Science programs in Molecular Biology, Biotechnology, "
        "or related Life Sciences across <b>the Netherlands, Ireland, and Australia</b>. Family can provide partial "
        "funding (approx. 40%); therefore need scholarship or assistantship covering the remaining tuition and living costs. "
        "Nationality: Indian.",
        body_style,
    ))
    story.append(Spacer(1, 15))
    story.append(Paragraph("<i>Fictional test profile – TetraNoodle AI Hackathon 2026. FOOTER: FICTIONAL SAMPLE</i>", watermark_style))
    doc.build(story)


def create_priya_marksheet(output_path: Path):
    doc = SimpleDocTemplate(str(output_path), pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    story = []

    header_style = ParagraphStyle("Hdr", parent=styles["Heading1"], fontSize=16, leading=20, alignment=1, textColor=colors.HexColor("#1A202C"))
    sub_style = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=9, alignment=1, textColor=colors.HexColor("#4A5568"))
    watermark_style = ParagraphStyle("Fict", parent=styles["Normal"], fontSize=8, textColor=colors.red, alignment=1)
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14, textColor=colors.HexColor("#2D3748"))

    story.append(Paragraph("OFFICIAL ACADEMIC TRANSCRIPT", header_style))
    story.append(Paragraph("Department of Life Sciences | Board of Examinations", sub_style))
    story.append(Paragraph("<b>[ FICTIONAL SAMPLE FOR TESTING PURPOSES ONLY - ZERO SCHOLARSHIP DATA ]</b>", watermark_style))
    story.append(Spacer(1, 15))

    student_data = [
        ["Student Name:", "Priya Nair", "Registration No:", "BSC-2022-0847 (Fictional)"],
        ["Program:", "B.Sc. Biosciences", "Graduation Year:", "2026 (Expected)"],
        ["Institution:", "St. Xavier's College (Autonomous), Kochi", "Status:", "Regular / Final Year"],
        ["Nationality:", "Indian", "State:", "Kerala"],
    ]
    t1 = Table(student_data, colWidths=[100, 180, 100, 140])
    t1.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#2D3748")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t1)
    story.append(Spacer(1, 12))

    grades_data = [
        ["Course Code", "Course Title", "Credits", "Grade", "Grade Point"],
        ["BIO301", "Cell Biology &amp; Biochemistry", "4", "A", "8.0"],
        ["BIO315", "Molecular Genetics", "4", "B+", "7.0"],
        ["BIO322", "Immunology &amp; Infectious Disease", "3", "A", "8.0"],
        ["BIO340", "Biostatistics &amp; Research Design", "3", "B", "6.0"],
        ["BIO401", "Phytochemistry &amp; Natural Products", "4", "A+", "9.0"],
        ["BIO490", "Dissertation Research Project", "6", "A", "8.0"],
        ["CHE210", "Organic &amp; Physical Chemistry", "3", "B+", "7.0"],
    ]
    t2 = Table(grades_data, colWidths=[80, 250, 60, 60, 70])
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6F4EA")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B2DFDB")),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t2)
    story.append(Spacer(1, 15))

    summary_data = [
        ["Cumulative Grade Point Average (CGPA):", "7.4 / 10.0"],
        ["Total Credits Completed:", "110 / 120"],
        ["Academic Standing:", "Second Division (Upper)"],
    ]
    t3 = Table(summary_data, colWidths=[240, 270])
    t3.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#1A365D")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t3)
    story.append(Spacer(1, 20))
    story.append(Paragraph("<i>Certified by Registrar: Fictional Test Document. FOOTER: FICTIONAL SAMPLE</i>", watermark_style))

    doc.build(story)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate fictional sample PDFs for TetraNoodle testing.")
    parser.add_argument(
        "--profile",
        choices=["rahul", "priya", "all"],
        default="rahul",
        help="Which fictional profile to generate (default: rahul). Use 'all' to generate both.",
    )
    args = parser.parse_args()

    profiles = ["rahul", "priya"] if args.profile == "all" else [args.profile]

    if "rahul" in profiles:
        resume_file = OUTPUT_DIR / "sample_resume_rahul.pdf"
        marksheet_file = OUTPUT_DIR / "sample_marksheet_rahul.pdf"
        create_fictional_resume(resume_file)
        create_fictional_marksheet(marksheet_file)
        print(f"Generated: {resume_file}")
        print(f"Generated: {marksheet_file}")

    if "priya" in profiles:
        resume_file = OUTPUT_DIR / "sample_resume_priya.pdf"
        marksheet_file = OUTPUT_DIR / "sample_marksheet_priya.pdf"
        create_priya_resume(resume_file)
        create_priya_marksheet(marksheet_file)
        print(f"Generated: {resume_file}")
        print(f"Generated: {marksheet_file}")


if __name__ == "__main__":
    main()
