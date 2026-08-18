"""
Convert a Zendesk article's HTML body into a .docx file.

Heading mapping is the reverse of the QRG migration tool's style map so
round-tripping through both tools preserves structure:
    h2 -> Heading 1,  h3 -> Heading 2,  h4 -> Heading 3
"""

import io
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

import audit


HEADING_MAP = {"h2": 1, "h3": 2, "h4": 3}


def fetch_article(article_id):
    resp = audit.zendesk_request(
        "get", f"/api/v2/help_center/articles/{article_id}.json"
    )
    return resp.json().get("article", {})


def _download_image(url):
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return io.BytesIO(r.content)
    except Exception:
        pass
    return None


def _add_inline_runs(paragraph, element):
    if isinstance(element, NavigableString):
        text = str(element)
        if text.strip():
            paragraph.add_run(text)
        return

    if not isinstance(element, Tag):
        return

    tag = element.name

    if tag in ("strong", "b"):
        run = paragraph.add_run(element.get_text())
        run.bold = True
    elif tag in ("em", "i"):
        run = paragraph.add_run(element.get_text())
        run.italic = True
    elif tag == "a":
        run = paragraph.add_run(element.get_text())
        run.underline = True
        run.font.color.rgb = RGBColor(0x1A, 0x56, 0xDB)
    elif tag == "code":
        run = paragraph.add_run(element.get_text())
        run.font.name = "Consolas"
        run.font.size = Pt(9)
    elif tag == "br":
        paragraph.add_run("\n")
    elif tag == "img":
        src = element.get("src", "")
        img_stream = _download_image(src) if src.startswith("http") else None
        if img_stream:
            try:
                paragraph.add_run().add_picture(img_stream, width=Inches(5.5))
            except Exception:
                alt = element.get("alt", src)
                paragraph.add_run(f"[Image: {alt}]")
        else:
            alt = element.get("alt", src)
            paragraph.add_run(f"[Image: {alt}]")
    else:
        for child in element.children:
            _add_inline_runs(paragraph, child)


def _process_list(doc, list_tag, indent=0):
    ordered = list_tag.name == "ol"
    counter = 1
    for li in list_tag.find_all("li", recursive=False):
        prefix = f"{counter}. " if ordered else "• "
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Inches(0.5 * (indent + 1))
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        p.add_run(prefix)

        for child in li.children:
            if isinstance(child, Tag) and child.name in ("ul", "ol"):
                _process_list(doc, child, indent + 1)
            else:
                _add_inline_runs(p, child)

        counter += 1


def _process_table(doc, table_tag):
    rows = table_tag.find_all("tr")
    if not rows:
        return

    col_count = max(
        len(row.find_all(["td", "th"])) for row in rows
    )
    if col_count == 0:
        return

    tbl = doc.add_table(rows=len(rows), cols=col_count)
    tbl.style = "Table Grid"

    for r_idx, row in enumerate(rows):
        cells = row.find_all(["td", "th"])
        for c_idx, cell in enumerate(cells):
            if c_idx < col_count:
                tbl_cell = tbl.cell(r_idx, c_idx)
                tbl_cell.text = ""
                p = tbl_cell.paragraphs[0]
                for child in cell.children:
                    _add_inline_runs(p, child)
                if cell.name == "th":
                    for run in p.runs:
                        run.bold = True


def html_to_docx(title, html_body):
    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    heading = doc.add_heading(title, level=1)
    heading.style = doc.styles["Heading 1"]

    if not html_body:
        return doc

    soup = BeautifulSoup(html_body, "html.parser")

    for element in soup.children:
        if isinstance(element, NavigableString):
            text = str(element).strip()
            if text:
                doc.add_paragraph(text)
            continue

        if not isinstance(element, Tag):
            continue

        tag = element.name

        if tag in HEADING_MAP:
            doc.add_heading(element.get_text(strip=True), level=HEADING_MAP[tag])

        elif tag == "h1":
            doc.add_heading(element.get_text(strip=True), level=1)

        elif tag == "p":
            p = doc.add_paragraph()
            for child in element.children:
                _add_inline_runs(p, child)

        elif tag in ("ul", "ol"):
            _process_list(doc, element)

        elif tag == "table":
            _process_table(doc, element)

        elif tag == "div":
            cls = element.get("class", [])
            if "callout" in cls:
                p = doc.add_paragraph()
                p.style = doc.styles["Normal"]
                p.paragraph_format.left_indent = Inches(0.5)
                run = p.add_run("⚠ ")
                run.bold = True
                for child in element.children:
                    _add_inline_runs(p, child)
            else:
                for child in element.children:
                    if isinstance(child, Tag):
                        wrapper = BeautifulSoup(str(child), "html.parser")
                        for sub in wrapper.children:
                            if isinstance(sub, Tag):
                                html_to_docx_element(doc, sub)

        elif tag == "pre":
            p = doc.add_paragraph()
            run = p.add_run(element.get_text())
            run.font.name = "Consolas"
            run.font.size = Pt(9)

        elif tag == "hr":
            p = doc.add_paragraph()
            p.add_run("_" * 60)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        elif tag == "blockquote":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.5)
            p.add_run("“")
            for child in element.children:
                _add_inline_runs(p, child)
            p.add_run("”")

    return doc


def html_to_docx_element(doc, element):
    tag = element.name
    if tag in HEADING_MAP:
        doc.add_heading(element.get_text(strip=True), level=HEADING_MAP[tag])
    elif tag == "p":
        p = doc.add_paragraph()
        for child in element.children:
            _add_inline_runs(p, child)
    elif tag in ("ul", "ol"):
        _process_list(doc, element)
    elif tag == "table":
        _process_table(doc, element)


def export_article_docx(article_id):
    article = fetch_article(article_id)
    if not article:
        return None, None

    title = article.get("title", "Untitled")
    html_body = article.get("body", "")

    doc = html_to_docx(title, html_body)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')[:80]
    filename = f"{safe_title}.docx"

    return buf, filename
