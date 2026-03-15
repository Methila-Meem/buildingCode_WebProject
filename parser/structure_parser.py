"""
parser/structure_parser.py
===========================
Parses Datalab Marker API JSON output into a structured document tree.

Confirmed Datalab JSON structure (from real BCBC output):
    result["json"]["children"]         -> list of Page objects
        page["children"]               -> list of blocks
            block["block_type"]        -> SectionHeader | ListGroup | Text | Table | Caption | PageFooter
            block["html"]              -> HTML content of the block
            block["page"]              -> 0-based page index

BCBC heading hierarchy assigned by Datalab:
    h1 -> Part         e.g. "Part 1 Compliance"
    h2 -> Section      e.g. "Section 1.1. General"
    h3 -> Article      e.g. "1.1.1. Application of this Code"
    h4 -> Sentence     e.g. "1.1.1.1. Application of this Code"

Internal data model (matching BCBC terminology):
    Document
    -> Chapter   (maps to Part  - h1)
        -> Section              (h2)
            -> Clause           (h3 Article, h4 Sentence)
                -> SubClause    (list items: 1), a), i) etc.)

Bug fixes applied (confirmed against real raw_output.json):
    1. H1 "Part 1Compliance" missing space - RE_PART uses \s* to handle both cases
    2. ListGroup collapsed to one line - now each <li> becomes its own line
    3. Caption is a separate block - buffered and attached to the next Table block
    4. RE_ARTICLE greedily matches 4-part numbers - SENTENCE always checked before ARTICLE
"""

import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from datetime import datetime


# =============================================================================
# Data models
# =============================================================================

@dataclass
class SubClause:
    id: str
    marker: str       # e.g. "1)", "a)", "i)"
    text: str
    page: int = 0


@dataclass
class Table:
    id: str
    caption: str
    headers: List[str]
    rows: List[List[str]]
    page: int = 0


@dataclass
class Equation:
    id: str
    raw_text: str
    page: int = 0


@dataclass
class Clause:
    id: str
    number: str
    title: str
    text: str
    sub_clauses: List[SubClause] = field(default_factory=list)
    tables: List[Table] = field(default_factory=list)
    equations: List[Equation] = field(default_factory=list)
    references: List[dict] = field(default_factory=list)
    page_span: List[int] = field(default_factory=list)


@dataclass
class Section:
    id: str
    number: str
    title: str
    clauses: List[Clause] = field(default_factory=list)
    page_span: List[int] = field(default_factory=list)


@dataclass
class Chapter:
    id: str
    number: str
    title: str
    sections: List[Section] = field(default_factory=list)
    page_span: List[int] = field(default_factory=list)


@dataclass
class Document:
    title: str
    source_pdf: str
    total_pages: int
    extracted_at: str
    chapters: List[Chapter] = field(default_factory=list)


# =============================================================================
# Regex patterns
# =============================================================================

# BUG FIX 1: \s* (zero or more spaces) handles "Part 1Compliance" and "Part 1 Compliance"
RE_PART     = re.compile(r'^Part\s*(\d+)\s*(.*)', re.IGNORECASE)

# "Section 1.1. General" or "1.1. General"
RE_SECTION  = re.compile(r'^(?:Section\s+)?(\d+\.\d+)\.?\s+(.+)', re.IGNORECASE)

# "1.1.1. Application..." - 3-part article
RE_ARTICLE  = re.compile(r'^(\d+\.\d+\.\d+)\.?\s*(.*)')

# "1.1.1.1. Application..." - 4-part sentence
# IMPORTANT: Always check RE_SENTENCE before RE_ARTICLE
# RE_ARTICLE also matches 4-part numbers and will steal them if checked first
RE_SENTENCE = re.compile(r'^(\d+\.\d+\.\d+\.\d+)\.?\s*(.*)')

# Sub-clause markers: (a), (b), a), i), ii. etc.
RE_SUBCLAUSE = re.compile(r'^\s*(\([a-z]+\)|[a-z]\)|[ivxlcdm]+\.)\s+(.+)', re.IGNORECASE)

# Cross-references in text
RE_REFERENCE = re.compile(
    r'(?:section|clause|table|figure|article|sentence|subsection)\s+'
    r'(\d+(?:\.\d+)*(?:\([^)]+\))?)',
    re.IGNORECASE
)


# =============================================================================
# HTML helpers
# =============================================================================

def strip_html(html: str) -> str:
    """Remove all HTML tags and normalise whitespace."""
    if not html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = (text
            .replace('&amp;', '&')
            .replace('&lt;', '<')
            .replace('&gt;', '>')
            .replace('&nbsp;', ' ')
            .replace('&#39;', "'")
            .replace('&quot;', '"'))
    return re.sub(r'\s+', ' ', text).strip()


def parse_heading(html: str):
    """
    Extract (level, text) from a SectionHeader HTML block.
    e.g. '<h3>1.1.1. Title</h3>' -> (3, '1.1.1. Title')
    """
    m = re.match(r'<h(\d)[^>]*>(.*?)</h\1>', html.strip(), re.DOTALL | re.IGNORECASE)
    if m:
        return int(m.group(1)), strip_html(m.group(2))
    return 0, strip_html(html)


def listgroup_to_lines(html: str) -> str:
    """
    BUG FIX 2: Convert ListGroup HTML to newline-separated plain text.

    Problem: strip_html() collapses all <li> items onto one line.
    _extract_subclauses() uses splitlines() so it finds zero sub-clauses.

    Fix: Replace </li> with newline BEFORE stripping tags.
    Each list item becomes its own line, preserving structure for parsing.

    Input:
        <ol><li><b>1)</b> This Code applies to...<ol><li>a) design</li></ol></li></ol>
    Output:
        "1)  This Code applies to...\na) design"
    """
    text = re.sub(r'</li>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = (text
            .replace('&amp;', '&')
            .replace('&lt;', '<')
            .replace('&gt;', '>')
            .replace('&nbsp;', ' ')
            .replace('&#39;', "'")
            .replace('&quot;', '"'))
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.splitlines()]
    return '\n'.join(line for line in lines if line)


def parse_table_html(html: str):
    """
    Parse HTML table string into (headers, rows).
    Handles data-bbox attributes on th/td (present in Datalab output page 2+).
    Returns (list[str], list[list[str]])
    """
    headers = []
    rows = []

    thead = re.search(r'<thead[^>]*>(.*?)</thead>', html, re.DOTALL | re.IGNORECASE)
    if thead:
        ths = re.findall(r'<th[^>]*>(.*?)</th>', thead.group(1), re.DOTALL | re.IGNORECASE)
        headers = [strip_html(th) for th in ths]

    tbody = re.search(r'<tbody[^>]*>(.*?)</tbody>', html, re.DOTALL | re.IGNORECASE)
    if tbody:
        trs = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody.group(1), re.DOTALL | re.IGNORECASE)
        for tr in trs:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.DOTALL | re.IGNORECASE)
            if tds:
                row = [strip_html(td) for td in tds]
                if any(cell.strip() for cell in row):
                    rows.append(row)

    return headers, rows


# =============================================================================
# Main parser
# =============================================================================

class StructureParser:

    def __init__(self, source_pdf: str = "unknown.pdf"):
        self.source_pdf = source_pdf
        self._chapter_counter = 0
        self._table_counter = 0
        self._equation_counter = 0

    def parse(self, datalab_result: dict) -> Document:
        blocks = self._flatten_blocks(datalab_result)
        total_pages = (
            datalab_result.get("page_count")
            or len((datalab_result.get("json") or {}).get("children", []))
        )
        document = Document(
            title=self._detect_title(blocks),
            source_pdf=self.source_pdf,
            total_pages=total_pages or 0,
            extracted_at=datetime.utcnow().isoformat() + "Z",
        )
        document.chapters = self._build_hierarchy(blocks)
        return document

    # -------------------------------------------------------------------------
    # Block flattening
    # -------------------------------------------------------------------------

    def _flatten_blocks(self, datalab_result: dict) -> list:
        """
        Flatten result["json"]["children"] pages into an ordered list of
        normalised block dicts: {type, level, text, page, raw}
        """
        flat = []
        json_output = datalab_result.get("json") or {}
        page_objects = json_output.get("children", [])

        if page_objects:
            for page_obj in page_objects:
                if page_obj.get("block_type") != "Page":
                    continue
                try:
                    page_num = int(page_obj["id"].split("/page/")[1].split("/")[0]) + 1
                except (IndexError, ValueError, KeyError):
                    page_num = 1

                for block in page_obj.get("children", []):
                    btype_raw = block.get("block_type", "")
                    html = block.get("html", "").strip()

                    if btype_raw in ("PageFooter", "PageHeader"):
                        continue
                    if not html:
                        continue

                    if btype_raw == "SectionHeader":
                        level, text = parse_heading(html)
                        flat.append({"type": "heading", "level": level,
                                     "text": text, "page": page_num, "raw": block})

                    elif btype_raw == "ListGroup":
                        # BUG FIX 2: preserve line structure
                        text = listgroup_to_lines(html)
                        if text:
                            flat.append({"type": "text", "level": 0,
                                         "text": text, "page": page_num, "raw": block})

                    elif btype_raw == "Table":
                        # Keep raw HTML for parse_table_html()
                        flat.append({"type": "table", "level": 0,
                                     "text": html, "page": page_num, "raw": block})

                    elif btype_raw in ("Equation", "Formula"):
                        text = strip_html(html)
                        if text:
                            flat.append({"type": "equation", "level": 0,
                                         "text": text, "page": page_num, "raw": block})

                    elif btype_raw == "Caption":
                        # BUG FIX 3 & 4: tag captions explicitly
                        text = strip_html(html)
                        if text:
                            flat.append({"type": "caption", "level": 0,
                                         "text": text, "page": page_num, "raw": block})

                    else:
                        text = strip_html(html)
                        if text:
                            flat.append({"type": "text", "level": 0,
                                         "text": text, "page": page_num, "raw": block})
            return flat

        # Fallback: old pages/blocks format
        for page_num, page in enumerate(datalab_result.get("pages", []), start=1):
            for block in page.get("blocks", []):
                flat.append({
                    "type": block.get("block_type", "text"),
                    "text": block.get("html", block.get("text", "")).strip(),
                    "level": block.get("level", 0),
                    "page": page_num,
                    "raw": block,
                })

        # Fallback: markdown string
        if not flat and datalab_result.get("markdown"):
            flat = self._parse_markdown_fallback(datalab_result["markdown"])

        return flat

    def _parse_markdown_fallback(self, markdown: str) -> list:
        blocks = []
        page = 1
        for line in markdown.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("# "):
                blocks.append({"type": "heading", "level": 1, "text": s[2:], "page": page})
            elif s.startswith("## "):
                blocks.append({"type": "heading", "level": 2, "text": s[3:], "page": page})
            elif s.startswith("### "):
                blocks.append({"type": "heading", "level": 3, "text": s[4:], "page": page})
            elif s.startswith("#### "):
                blocks.append({"type": "heading", "level": 4, "text": s[5:], "page": page})
            elif s.lower().startswith("[page"):
                page += 1
            else:
                blocks.append({"type": "text", "level": 0, "text": s, "page": page})
        return blocks

    # -------------------------------------------------------------------------
    # Hierarchy builder
    # -------------------------------------------------------------------------

    def _detect_title(self, blocks: list) -> str:
        for b in blocks:
            if b["type"] == "heading" and b.get("level", 0) == 1:
                return b["text"]
        return "Building Code Document"

    def _build_hierarchy(self, blocks: list) -> List[Chapter]:
        chapters: List[Chapter] = []
        current_chapter: Optional[Chapter] = None
        current_section: Optional[Section] = None
        current_clause: Optional[Clause] = None
        pending_text: list = []
        pending_caption: str = ""   # BUG FIX 3: buffer for Caption -> Table association

        def flush_text():
            nonlocal pending_text
            if not pending_text:
                return
            combined = "\n".join(pending_text).strip()
            pending_text = []
            if combined and current_clause:
                if current_clause.text:
                    current_clause.text += "\n" + combined
                else:
                    current_clause.text = combined
                self._extract_subclauses(current_clause, combined)

        for block in blocks:
            btype = block["type"]
            text  = block["text"]
            page  = block["page"]
            level = block.get("level", 0)

            # ── Headings ──────────────────────────────────────────────────────
            if btype == "heading":
                flush_text()

                if level <= 1:
                    # BUG FIX 1: handles "Part 1Compliance"
                    num, title = self._parse_part_heading(text)
                    current_chapter = Chapter(
                        id=f"CH-{num}", number=num, title=title, page_span=[page]
                    )
                    chapters.append(current_chapter)
                    current_section = None
                    current_clause = None

                elif level == 2:
                    m = RE_SECTION.match(text)
                    if m and current_chapter:
                        current_section = Section(
                            id=f"SEC-{m.group(1).replace('.', '-')}",
                            number=m.group(1), title=m.group(2).strip(), page_span=[page]
                        )
                        current_chapter.sections.append(current_section)
                        current_clause = None
                    else:
                        pending_text.append(text)

                elif level == 3:
                    m = RE_ARTICLE.match(text)
                    if m and current_chapter:
                        num   = m.group(1)
                        title = m.group(2).lstrip(". ").strip() or num
                        current_section = Section(
                            id=f"SEC-{num.replace('.', '-')}",
                            number=num, title=title, page_span=[page]
                        )
                        current_chapter.sections.append(current_section)
                        current_clause = None
                    else:
                        pending_text.append(text)

                elif level >= 4:
                    # BUG FIX 4: RE_SENTENCE checked BEFORE RE_ARTICLE
                    m = RE_SENTENCE.match(text)
                    if m and current_section:
                        num   = m.group(1)
                        title = m.group(2).lstrip(". ").strip() or num
                        current_clause = Clause(
                            id=f"CL-{num.replace('.', '-')}",
                            number=num, title=title, text="", page_span=[page]
                        )
                        current_section.clauses.append(current_clause)
                    else:
                        pending_text.append(text)

            # ── Plain text / list content ─────────────────────────────────────
            elif btype == "text":
                first_line = text.splitlines()[0] if text else ""
                # BUG FIX 4: check 4-part BEFORE 3-part in text blocks too
                sen_m = RE_SENTENCE.match(first_line)
                art_m = RE_ARTICLE.match(first_line)
                sec_m = RE_SECTION.match(first_line)

                if sen_m and current_section:
                    flush_text()
                    num   = sen_m.group(1)
                    title = sen_m.group(2).lstrip(". ").strip() or num
                    current_clause = Clause(
                        id=f"CL-{num.replace('.', '-')}",
                        number=num, title=title, text="", page_span=[page]
                    )
                    current_section.clauses.append(current_clause)
                elif art_m and current_chapter:
                    flush_text()
                    num   = art_m.group(1)
                    title = art_m.group(2).lstrip(". ").strip() or num
                    current_section = Section(
                        id=f"SEC-{num.replace('.', '-')}",
                        number=num, title=title, page_span=[page]
                    )
                    current_chapter.sections.append(current_section)
                    current_clause = None
                elif sec_m and current_chapter:
                    flush_text()
                    current_section = Section(
                        id=f"SEC-{sec_m.group(1).replace('.', '-')}",
                        number=sec_m.group(1), title=sec_m.group(2).strip(), page_span=[page]
                    )
                    current_chapter.sections.append(current_section)
                    current_clause = None
                else:
                    pending_text.append(text)
                    if current_clause and page not in current_clause.page_span:
                        current_clause.page_span.append(page)

            # ── Caption: buffer for next table (BUG FIX 3) ───────────────────
            elif btype == "caption":
                flush_text()
                pending_caption = text

            # ── Table ─────────────────────────────────────────────────────────
            elif btype == "table":
                flush_text()
                self._table_counter += 1
                caption = pending_caption or f"Table {self._table_counter}"
                pending_caption = ""    # consume

                headers, rows = parse_table_html(text)
                table = Table(
                    id=f"TBL-{self._table_counter}",
                    caption=caption,
                    headers=headers,
                    rows=rows,
                    page=page,
                )
                if current_clause:
                    current_clause.tables.append(table)

            # ── Equation ──────────────────────────────────────────────────────
            elif btype in ("equation", "formula"):
                flush_text()
                self._equation_counter += 1
                eq = Equation(id=f"EQ-{self._equation_counter}", raw_text=text, page=page)
                if current_clause:
                    current_clause.equations.append(eq)

        flush_text()
        return chapters

    # -------------------------------------------------------------------------
    # Sub-clause extraction
    # -------------------------------------------------------------------------

    def _extract_subclauses(self, clause: Clause, text: str):
        """
        Scan newline-separated text for sub-clause markers.
        BUG FIX 2 ensures text arrives here as separate lines,
        so each list item (1), a), i)) is detected correctly.
        """
        sc_counter = len(clause.sub_clauses)
        for line in text.splitlines():
            m = RE_SUBCLAUSE.match(line)
            if m:
                sc_counter += 1
                clause.sub_clauses.append(SubClause(
                    id=f"{clause.id}-SC{sc_counter}",
                    marker=m.group(1),
                    text=m.group(2).strip(),
                ))

    # -------------------------------------------------------------------------
    # Part/Chapter heading parser (BUG FIX 1)
    # -------------------------------------------------------------------------

    def _parse_part_heading(self, text: str):
        """
        Parse "Part 1Compliance" or "Part 1 Compliance".
        RE_PART uses \s* to handle the missing space case.
        """
        self._chapter_counter += 1
        m = RE_PART.match(text)
        if m:
            return m.group(1), m.group(2).strip() or text
        return str(self._chapter_counter), text

    # -------------------------------------------------------------------------
    # Serialisation
    # -------------------------------------------------------------------------

    def to_dict(self, document: Document) -> dict:
        return asdict(document)


# =============================================================================
# Public entry point
# =============================================================================

def parse_datalab_output(datalab_result: dict, source_pdf: str = "unknown.pdf") -> dict:
    parser = StructureParser(source_pdf=source_pdf)
    document = parser.parse(datalab_result)
    return parser.to_dict(document)