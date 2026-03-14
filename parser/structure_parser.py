"""
parser/structure_parser.py
===========================
Converts the raw Datalab output into a clean hierarchical document tree.

The hierarchy we build:
    Document
    └── Chapter      (e.g. "Chapter 3 — Structural Loads")
        └── Section  (e.g. "3.1 Dead Loads")
            └── Clause  (e.g. "3.1.2 Calculation method")
                └── SubClause  (e.g. "(a)", "(b)", "i.", "ii.")

Each node gets:
  - A unique ID  (e.g. SEC-3-1, CL-3-1-2)
  - Page span metadata
  - Extracted tables and equations
  - Detected internal references (resolved later)
"""

import re
import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional


# -------------------------------------------------------
# Data models — each level of the hierarchy
# -------------------------------------------------------

@dataclass
class SubClause:
    id: str
    marker: str          # e.g. "(a)", "i."
    text: str
    page: int = 0


@dataclass
class TableCell:
    row: int
    col: int
    value: str


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
    raw_text: str        # LaTeX or plain text as extracted
    page: int = 0


@dataclass
class Clause:
    id: str
    number: str          # e.g. "3.1.2"
    title: str
    text: str
    sub_clauses: List[SubClause] = field(default_factory=list)
    tables: List[Table] = field(default_factory=list)
    equations: List[Equation] = field(default_factory=list)
    references: List[dict] = field(default_factory=list)  # filled by reference_linker
    page_span: List[int] = field(default_factory=list)


@dataclass
class Section:
    id: str
    number: str          # e.g. "3.1"
    title: str
    clauses: List[Clause] = field(default_factory=list)
    page_span: List[int] = field(default_factory=list)


@dataclass
class Chapter:
    id: str
    number: str          # e.g. "3"
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


# -------------------------------------------------------
# Regex patterns for detecting document structure
# -------------------------------------------------------

# Matches: "Chapter 3", "CHAPTER 3", "3. Title"
RE_CHAPTER = re.compile(
    r'^(?:chapter\s+(\d+)|(\d+)\.\s+[A-Z])',
    re.IGNORECASE
)

# Matches section numbers like "3.1", "3.1.2", "10.4.3"
RE_SECTION = re.compile(r'^(\d+\.\d+)\s+(.+)')

# Matches clause numbers like "3.1.2", "3.1.2.1"
RE_CLAUSE = re.compile(r'^(\d+\.\d+\.\d+(?:\.\d+)?)\s*(.*)')

# Matches sub-clause markers: (a), (b), (i), (ii), a), i.
RE_SUBCLAUSE = re.compile(r'^\s*(\([a-z]+\)|[a-z]\)|[ivxlcdm]+\.)\s+(.+)', re.IGNORECASE)

# Matches inline equations (LaTeX-style or bracketed)
RE_EQUATION = re.compile(r'\$\$.+?\$\$|\$.+?\$|\[EQ\s*\d+\]', re.DOTALL)

# Matches cross-references inside text
RE_REFERENCE = re.compile(
    r'(?:section|clause|table|figure|article)\s+(\d+(?:\.\d+)*)',
    re.IGNORECASE
)


class StructureParser:
    """
    Parses Datalab JSON output into a structured Document tree.

    Usage:
        parser = StructureParser(source_pdf="building_code.pdf")
        document = parser.parse(datalab_result)
    """

    def __init__(self, source_pdf: str = "unknown.pdf"):
        self.source_pdf = source_pdf
        self._chapter_counter = 0
        self._equation_counter = 0
        self._table_counter = 0

    def parse(self, datalab_result: dict) -> Document:
        """
        Main entry point. Pass the full Datalab API response dict.
        Returns a structured Document object.
        """
        from datetime import datetime

        # Datalab returns a list of 'blocks' per page in result['output']
        # Flatten all blocks into a single list with page numbers attached
        blocks = self._flatten_blocks(datalab_result)

        document = Document(
            title=self._detect_title(blocks),
            source_pdf=self.source_pdf,
            total_pages=len(datalab_result.get("pages", [])),
            extracted_at=datetime.utcnow().isoformat() + "Z",
        )

        document.chapters = self._build_hierarchy(blocks)
        return document

    # -------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------

    def _flatten_blocks(self, datalab_result: dict) -> list:
        """
        Datalab returns output as pages → blocks.
        We flatten everything into [{type, text, page}, ...].
        Falls back to parsing markdown if JSON blocks unavailable.
        """
        flat = []

        pages = datalab_result.get("pages", [])
        for page_num, page in enumerate(pages, start=1):
            for block in page.get("blocks", []):
                flat.append({
                    "type": block.get("block_type", "text"),  # heading, text, table, equation
                    "text": block.get("html", block.get("text", "")).strip(),
                    "level": block.get("level", 0),           # heading depth
                    "page": page_num,
                    "raw": block,
                })

        # Fallback: if no structured pages, parse the markdown string
        if not flat and datalab_result.get("markdown"):
            flat = self._parse_markdown_fallback(datalab_result["markdown"])

        return flat

    def _parse_markdown_fallback(self, markdown: str) -> list:
        """
        When Datalab returns only markdown (no structured JSON blocks),
        we parse heading levels and text lines manually.
        """
        blocks = []
        page = 1
        for line in markdown.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("# "):
                blocks.append({"type": "heading", "level": 1, "text": stripped[2:], "page": page})
            elif stripped.startswith("## "):
                blocks.append({"type": "heading", "level": 2, "text": stripped[3:], "page": page})
            elif stripped.startswith("### "):
                blocks.append({"type": "heading", "level": 3, "text": stripped[4:], "page": page})
            elif stripped.startswith("#### "):
                blocks.append({"type": "heading", "level": 4, "text": stripped[5:], "page": page})
            elif stripped.lower().startswith("[page"):
                page += 1
            else:
                blocks.append({"type": "text", "level": 0, "text": stripped, "page": page})
        return blocks

    def _detect_title(self, blocks: list) -> str:
        """Return the first H1 heading as the document title."""
        for b in blocks:
            if b["type"] == "heading" and b.get("level", 0) == 1:
                return b["text"]
        return "Building Code Document"

    def _build_hierarchy(self, blocks: list) -> List[Chapter]:
        """
        Walk through all blocks and assemble the chapter → section → clause tree.
        """
        chapters = []
        current_chapter: Optional[Chapter] = None
        current_section: Optional[Section] = None
        current_clause: Optional[Clause] = None
        pending_text_lines = []   # buffer text lines until we know where they belong

        def flush_text():
            """Attach buffered text to the most specific current node."""
            nonlocal pending_text_lines
            if not pending_text_lines:
                return
            combined = " ".join(pending_text_lines).strip()
            pending_text_lines = []
            if current_clause:
                current_clause.text += (" " if current_clause.text else "") + combined
                # Extract sub-clauses from combined text
                self._extract_subclauses(current_clause, combined)
            elif current_section:
                pass  # section-level prose — skip for now
            pending_text_lines = []

        for block in blocks:
            btype = block["type"]
            text = block["text"]
            page = block["page"]
            level = block.get("level", 0)

            # ---- HEADINGS ----
            if btype == "heading":
                flush_text()

                if level <= 1:
                    # Chapter-level heading
                    ch_num, ch_title = self._parse_chapter_heading(text)
                    current_chapter = Chapter(
                        id=f"CH-{ch_num}",
                        number=ch_num,
                        title=ch_title,
                        page_span=[page],
                    )
                    chapters.append(current_chapter)
                    current_section = None
                    current_clause = None

                elif level == 2:
                    # Section heading like "3.1 Dead Loads"
                    match = RE_SECTION.match(text)
                    if match and current_chapter:
                        sec_num = match.group(1)
                        sec_title = match.group(2)
                        current_section = Section(
                            id=self._make_section_id(sec_num),
                            number=sec_num,
                            title=sec_title,
                            page_span=[page],
                        )
                        current_chapter.sections.append(current_section)
                        current_clause = None
                    else:
                        # No number — treat as a new clause title
                        pending_text_lines.append(text)

                elif level >= 3:
                    # Clause heading like "3.1.2 Calculation"
                    match = RE_CLAUSE.match(text)
                    if match and current_section:
                        cl_num = match.group(1)
                        cl_title = match.group(2)
                        current_clause = Clause(
                            id=self._make_clause_id(cl_num),
                            number=cl_num,
                            title=cl_title,
                            text="",
                            page_span=[page],
                        )
                        current_section.clauses.append(current_clause)
                    else:
                        pending_text_lines.append(text)

            # ---- PLAIN TEXT ----
            elif btype == "text":
                # Check if this line IS a clause number even without a heading marker
                cl_match = RE_CLAUSE.match(text)
                sec_match = RE_SECTION.match(text)

                if cl_match and current_section:
                    flush_text()
                    cl_num = cl_match.group(1)
                    cl_title = cl_match.group(2)
                    current_clause = Clause(
                        id=self._make_clause_id(cl_num),
                        number=cl_num,
                        title=cl_title,
                        text="",
                        page_span=[page],
                    )
                    current_section.clauses.append(current_clause)
                elif sec_match and current_chapter:
                    flush_text()
                    sec_num = sec_match.group(1)
                    sec_title = sec_match.group(2)
                    current_section = Section(
                        id=self._make_section_id(sec_num),
                        number=sec_num,
                        title=sec_title,
                        page_span=[page],
                    )
                    current_chapter.sections.append(current_section)
                    current_clause = None
                else:
                    pending_text_lines.append(text)
                    # Update page span of current clause if it spans pages
                    if current_clause and page not in current_clause.page_span:
                        current_clause.page_span.append(page)

            # ---- TABLES ----
            elif btype == "table":
                flush_text()
                table = self._parse_table_block(block, page)
                if current_clause:
                    current_clause.tables.append(table)
                elif current_section:
                    # Attach orphaned table to a dummy clause
                    pass

            # ---- EQUATIONS ----
            elif btype in ("equation", "formula"):
                flush_text()
                self._equation_counter += 1
                eq = Equation(
                    id=f"EQ-{self._equation_counter}",
                    raw_text=text,
                    page=page,
                )
                if current_clause:
                    current_clause.equations.append(eq)

        flush_text()
        return chapters

    def _extract_subclauses(self, clause: Clause, text: str):
        """
        Scan text for sub-clause markers like (a), (b), (i), (ii)
        and attach them to the clause.
        """
        lines = text.splitlines() if '\n' in text else [text]
        sc_counter = 0
        for line in lines:
            m = RE_SUBCLAUSE.match(line)
            if m:
                sc_counter += 1
                clause.sub_clauses.append(SubClause(
                    id=f"{clause.id}-SC{sc_counter}",
                    marker=m.group(1),
                    text=m.group(2).strip(),
                ))

    def _parse_table_block(self, block: dict, page: int) -> Table:
        """Convert a Datalab table block into our Table dataclass."""
        self._table_counter += 1
        raw = block.get("raw", {})
        rows_raw = raw.get("rows", [])
        headers = []
        rows = []

        for i, row in enumerate(rows_raw):
            cells = [str(c.get("text", c) if isinstance(c, dict) else c) for c in row]
            if i == 0:
                headers = cells
            else:
                rows.append(cells)

        return Table(
            id=f"TBL-{self._table_counter}",
            caption=raw.get("caption", f"Table {self._table_counter}"),
            headers=headers,
            rows=rows,
            page=page,
        )

    # -------------------------------------------------------
    # ID generation helpers
    # -------------------------------------------------------

    def _parse_chapter_heading(self, text: str):
        """Extract chapter number and title from heading text."""
        self._chapter_counter += 1
        match = RE_CHAPTER.match(text)
        if match:
            num = match.group(1) or match.group(2) or str(self._chapter_counter)
            title = re.sub(r'^chapter\s+\d+\s*[-–—]?\s*', '', text, flags=re.IGNORECASE).strip()
            return num, title or text
        return str(self._chapter_counter), text

    @staticmethod
    def _make_section_id(number: str) -> str:
        """Turn "3.1" into "SEC-3-1"."""
        return "SEC-" + number.replace(".", "-")

    @staticmethod
    def _make_clause_id(number: str) -> str:
        """Turn "3.1.2" into "CL-3-1-2"."""
        return "CL-" + number.replace(".", "-")

    def to_dict(self, document: Document) -> dict:
        """Convert Document dataclass tree to a plain dict for JSON serialization."""
        return asdict(document)


def parse_datalab_output(datalab_result: dict, source_pdf: str = "unknown.pdf") -> dict:
    """
    Convenience function: parse Datalab result → return JSON-serializable dict.

    Args:
        datalab_result: The dict returned by ingestion/datalab_client.py
        source_pdf:     Filename of the original PDF

    Returns:
        dict representing the full structured document
    """
    parser = StructureParser(source_pdf=source_pdf)
    document = parser.parse(datalab_result)
    return parser.to_dict(document)