"""Markdown → .docx export for `doc` artifacts.

Minimal, dependency-light converter covering what the doc-writer actually
emits: #/##/### headings, paragraphs, - / * bullets, 1. numbered lists,
**bold** / *italic* / `code` inline, and GitHub-style | pipe | tables.
Anything unrecognized degrades to a plain paragraph — never raises.
"""
import io
import re
from typing import List

from docx import Document
from docx.shared import Pt


_INLINE_RE = re.compile(r"(\*\*.+?\*\*|\*.+?\*|`.+?`)")


def _add_inline(par, text: str) -> None:
    """Render **bold** / *italic* / `code` runs inside a paragraph."""
    for part in _INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            par.add_run(part[2:-2]).bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            par.add_run(part[1:-1]).italic = True
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            run = par.add_run(part[1:-1])
            run.font.name = "Courier New"
            run.font.size = Pt(9)
        else:
            par.add_run(part)


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def _is_divider_row(line: str) -> bool:
    return bool(re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", line.strip()))


def markdown_to_docx(markdown_text: str, title: str) -> bytes:
    doc = Document()
    doc.add_heading(title or "Document", level=0)

    lines: List[str] = (markdown_text or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # fenced code block
        if stripped.startswith("```"):
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # closing fence
            par = doc.add_paragraph()
            run = par.add_run("\n".join(code_lines))
            run.font.name = "Courier New"
            run.font.size = Pt(9)
            continue

        # table block
        if _is_table_row(stripped):
            rows = []
            while i < len(lines) and _is_table_row(lines[i].strip()):
                if not _is_divider_row(lines[i]):
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    rows.append(cells)
                i += 1
            if rows:
                ncols = max(len(r) for r in rows)
                table = doc.add_table(rows=len(rows), cols=ncols)
                table.style = "Light Grid Accent 1"
                for r_idx, r in enumerate(rows):
                    for c_idx in range(ncols):
                        cell_par = table.cell(r_idx, c_idx).paragraphs[0]
                        _add_inline(cell_par, r[c_idx] if c_idx < len(r) else "")
                        if r_idx == 0:
                            for run in cell_par.runs:
                                run.bold = True
            continue

        # headings
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = min(len(m.group(1)), 4)
            h = doc.add_heading("", level=level)
            _add_inline(h, m.group(2).strip())
            i += 1
            continue

        # bullets
        m = re.match(r"^[-*]\s+(.*)$", stripped)
        if m:
            par = doc.add_paragraph(style="List Bullet")
            _add_inline(par, m.group(1))
            i += 1
            continue

        # numbered list
        m = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if m:
            par = doc.add_paragraph(style="List Number")
            _add_inline(par, m.group(1))
            i += 1
            continue

        # horizontal rule → skip
        if re.fullmatch(r"[-*_]{3,}", stripped):
            i += 1
            continue

        # plain paragraph (strip {viz:...} placeholders the in-app viewer uses)
        text = re.sub(r"\{viz:[^}]+\}", "", stripped).strip()
        if text:
            par = doc.add_paragraph()
            _add_inline(par, text)
        i += 1

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
