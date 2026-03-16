"""
parser/structure_parser.py
===========================
Parses Datalab Marker API JSON output into a structured document tree.

Confirmed from real BCBC Part 4 raw output (146 pages, 41 figures, 49 equations):

Block types present:
    SectionHeader  h1-h6   headings
    Text                   body paragraphs, variable definitions (may contain inline <math>)
    ListGroup              numbered/lettered clause lists
    Equation               display math: <math display="block">LaTeX</math>
    Figure                 images: html has <img alt="...">, images={key: base64}
    Picture                same as Figure but with richer html description
    Caption                standalone caption blocks for tables AND figures
    Table                  HTML tables
    PageFooter             ignored

Key design decision - ordered content model:
    Previous approach stored clause content in separate typed lists
    (text string, equations[], tables[]) which destroyed reading order.

    This version stores an ordered content[] array on each Clause:
        [
          {type: "text",     value: "The drift length..."},
          {type: "equation", latex: "x_d = 5 \\frac{...}"},
          {type: "text",     value: "where,"},
          {type: "figure",   figure_id: "FIG-1", image_key: "17cd...", caption: "...", alt_text: "..."},
          {type: "table",    table_id: "TBL-1", ...},
        ]

    This preserves the exact reading sequence from the PDF.

Caption association (bidirectional):
    Main body pages: Caption appears BEFORE Figure
    Appendix pages:  Caption appears AFTER Figure
    Some pages:      No adjacent caption (title is in Figure alt text)
    Solution: look one block before AND after each Figure block.

Heading levels in Part 4:
    h1 -> Part
    h2 -> Section
    h3 -> Subsection
    h4 -> Article (most clauses live here)
    h5 -> Notes heading (Notes to Table X / Notes to Figure X)
    h6 -> Sub-article or importance category label

Images:
    Saved to storage/figures/{image_key} as JPEG files.
    The content[] item stores the relative path for viewer rendering.
"""

import re
import os
import base64
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Any
from datetime import datetime


# =============================================================================
# Data models
# =============================================================================

@dataclass
class ContentItem:
    """
    One item in the ordered content sequence of a Clause.
    type is one of: "text" | "equation" | "figure" | "table" | "sub_clause"
    All other fields are optional depending on type.
    """
    type: str

    # text
    value: str = ""

    # equation
    latex: str = ""

    # figure
    figure_id: str = ""
    image_key: str = ""
    image_path: str = ""     # relative path: storage/figures/{image_key}
    caption: str = ""
    alt_text: str = ""

    # table (inline reference — full table data also stored in tables[])
    table_id: str = ""

    # sub_clause marker
    marker: str = ""


@dataclass
class Table:
    id: str
    caption: str
    headers: List[str]
    rows: List[List[str]]
    page: int = 0


@dataclass
class Figure:
    id: str
    caption: str
    alt_text: str
    image_key: str
    image_path: str          # relative path for viewer
    page: int = 0


@dataclass
class Equation:
    id: str
    latex: str
    page: int = 0


@dataclass
class Clause:
    id: str
    number: str
    title: str
    # Ordered mixed content — preserves reading sequence
    content: List[ContentItem] = field(default_factory=list)
    # Typed indexes for backwards compatibility and quick access
    tables: List[Table] = field(default_factory=list)
    figures: List[Figure] = field(default_factory=list)
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

# h1: "Part 4Structural Design" or "Part 4 Structural Design"
RE_PART     = re.compile(r'^Part\s*(\d+)\s*(.*)', re.IGNORECASE)
# h2/h3: "Section 4.1." or "4.1. Title"
RE_SECTION  = re.compile(r'^(?:Section\s+)?(\d+\.\d+)\.?\s*(.*)', re.IGNORECASE)
# h3: "1.1.1. Title" - 3-part
RE_ARTICLE  = re.compile(r'^(\d+\.\d+\.\d+)\.?\s*(.*)')
# h4: "1.1.1.1. Title" - 4-part  ALWAYS check before RE_ARTICLE
RE_SENTENCE = re.compile(r'^(\d+\.\d+\.\d+\.\d+)\.?\s*(.*)')
# sub-clause markers
RE_SUBCLAUSE = re.compile(r'^\s*(\([a-z]+\)|[a-z]\)|[ivxlcdm]+\.)\s+(.+)', re.IGNORECASE)
# cross-references
RE_REFERENCE = re.compile(
    r'(?:Sentence|Article|Subsection|Section|Table|Figure)\s+'
    r'([\d\.]+[\w\.\-\(\)]*)',
    re.IGNORECASE
)
# Figure caption number extraction e.g. "Figure 4.1.6.5.-A"
RE_FIGURE_NUM = re.compile(r'Figure\s+([\d\.]+[\w\.\-]*)', re.IGNORECASE)


# =============================================================================
# HTML helpers
# =============================================================================

def strip_html(html: str) -> str:
    """Remove HTML tags, decode entities, normalise whitespace."""
    if not html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = (text
            .replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            .replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"'))
    return re.sub(r'\s+', ' ', text).strip()


def extract_math(html: str) -> str:
    """
    Extract LaTeX from <math> tags.
    Handles both display math and inline math.
    Unescapes double-backslash from JSON encoding.
    """
    parts = re.findall(r'<math[^>]*>(.*?)</math>', html, re.DOTALL | re.IGNORECASE)
    if parts:
        latex = ' '.join(p.strip() for p in parts)
        # JSON encoding doubles backslashes: \\frac -> \frac
        latex = latex.replace('\\\\', '\\')
        latex = (latex.replace('&amp;', '&').replace('&lt;', '<')
                 .replace('&gt;', '>').replace('&nbsp;', ' '))
        return re.sub(r'\s+', ' ', latex).strip()
    return strip_html(html)


def parse_heading(html: str):
    """Extract (level, plain_text) from a SectionHeader HTML block."""
    m = re.match(r'<h(\d)[^>]*>(.*?)</h\1>', html.strip(), re.DOTALL | re.IGNORECASE)
    if m:
        return int(m.group(1)), strip_html(m.group(2))
    return 0, strip_html(html)


def listgroup_to_lines(html: str) -> str:
    """
    Convert ListGroup HTML to newline-separated lines.
    Replaces </li> with newline BEFORE stripping tags so each
    list item becomes its own line for sub-clause detection.
    """
    text = re.sub(r'</li>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = (text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            .replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"'))
    lines = [re.sub(r'[ \t]+', ' ', ln).strip() for ln in text.splitlines()]
    return '\n'.join(ln for ln in lines if ln)


def parse_table_html(html: str):
    """
    Parse HTML table into (headers, rows) with correct rowspan and colspan handling.

    Problems this fixes (confirmed from BCBC Part 4 Table 4.5.1.1):
      - rowspan='N': cell spans N rows in its column. Without handling,
        content shifts left — e.g. a Functional Statement ends up in
        the Provision column.
      - colspan=num_cols: full-width section header row embedded in tbody
        (e.g. '4.1.3.3. Fatigue' spanning all columns). Without handling,
        the text duplicates into every column.

    Algorithm: rowspan_carry[col] = (rows_remaining, cell_value).
    For each new row, inject carried values before consuming new td cells.
    """
    headers, rows = [], []

    thead = re.search(r'<thead[^>]*>(.*?)</thead>', html, re.DOTALL | re.IGNORECASE)
    if thead:
        ths = re.findall(r'<th[^>]*>(.*?)</th>', thead.group(1), re.DOTALL | re.IGNORECASE)
        headers = [strip_html(th) for th in ths]
    num_cols = len(headers) if headers else 2

    tbody = re.search(r'<tbody[^>]*>(.*?)</tbody>', html, re.DOTALL | re.IGNORECASE)
    if not tbody:
        return headers, rows

    trs = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody.group(1), re.DOTALL | re.IGNORECASE)
    rowspan_carry = {}  # {col_index: (rows_remaining, cell_value)}

    for tr in trs:
        td_matches = re.findall(r'<td([^>]*)>(.*?)</td>', tr, re.DOTALL | re.IGNORECASE)
        row     = [''] * num_cols
        td_iter = iter(td_matches)
        col     = 0

        while col < num_cols:
            # Inject rowspan-carried value for this column position
            if col in rowspan_carry:
                remaining, value = rowspan_carry[col]
                row[col] = value
                if remaining - 1 > 0:
                    rowspan_carry[col] = (remaining - 1, value)
                else:
                    del rowspan_carry[col]
                col += 1
                continue

            # Consume the next td element
            try:
                attrs_str, cell_html = next(td_iter)
            except StopIteration:
                col += 1
                continue

            cell_value = strip_html(cell_html)
            rs = re.search(r'rowspan=["\'](\d+)["\']', attrs_str)
            cs = re.search(r'colspan=["\'](\d+)["\']', attrs_str)
            rowspan = int(rs.group(1)) if rs else 1
            colspan = int(cs.group(1)) if cs else 1

            # Full-width colspan = section header embedded in tbody
            # Put value in col 0 only, leave other columns empty
            if colspan >= num_cols:
                row[0] = cell_value
            else:
                for c in range(colspan):
                    if col + c < num_cols:
                        row[col + c] = cell_value

            # Register rowspan carry for subsequent rows
            if rowspan > 1:
                for c in range(min(colspan, num_cols)):
                    if col + c < num_cols:
                        rowspan_carry[col + c] = (rowspan - 1, cell_value)

            col += colspan

        if any(c.strip() for c in row):
            rows.append(row)

    return headers, rows


def extract_alt_text(html: str) -> str:
    """Extract alt attribute from <img> tag."""
    m = re.search(r'<img[^>]+alt=["\']([^"\']*)["\']', html, re.IGNORECASE)
    return m.group(1).strip() if m else strip_html(html)


def save_image(image_key: str, base64_data: str, figures_dir: str) -> str:
    """
    Decode base64 image and save to disk.
    Returns the relative path: storage/figures/{image_key}
    """
    os.makedirs(figures_dir, exist_ok=True)
    file_path = os.path.join(figures_dir, image_key)
    if not os.path.exists(file_path):
        try:
            img_bytes = base64.b64decode(base64_data)
            with open(file_path, 'wb') as f:
                f.write(img_bytes)
        except Exception as e:
            print(f"[Parser] Warning: could not save image {image_key}: {e}")
            return ""
    return os.path.join("storage", "figures", image_key)


# =============================================================================
# Main parser
# =============================================================================

class StructureParser:

    def __init__(self, source_pdf: str = "unknown.pdf",
                 figures_dir: str = "storage/figures"):
        self.source_pdf  = source_pdf
        self.figures_dir = figures_dir
        self._chapter_counter  = 0
        self._table_counter    = 0
        self._equation_counter = 0
        self._figure_counter   = 0
        self._images_dict      = {}   # populated from datalab result

    def parse(self, datalab_result: dict) -> Document:
        self._images_dict = datalab_result.get("images") or {}
        blocks = self._flatten_blocks(datalab_result)
        total_pages = (
            datalab_result.get("page_count") or
            len((datalab_result.get("json") or {}).get("children", []))
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
        Flatten result["json"]["children"] pages into an ordered list.
        Each block becomes: {type, level, text, latex, page, raw,
                             image_key, alt_text, caption_hint}

        Caption association happens here for Figure blocks:
          - Check the block immediately before for a Caption
          - If not found, check the block immediately after
          - Fall back to extracting the figure number from alt text
        """
        flat = []
        json_output  = datalab_result.get("json") or {}
        page_objects = json_output.get("children", [])

        if not page_objects:
            # Fallback to old format or markdown
            return self._flatten_legacy(datalab_result)

        for page_obj in page_objects:
            if page_obj.get("block_type") != "Page":
                continue
            try:
                page_num = int(page_obj["id"].split("/page/")[1].split("/")[0]) + 1
            except (IndexError, ValueError, KeyError):
                page_num = 1

            children = page_obj.get("children", [])

            for idx, block in enumerate(children):
                btype_raw = block.get("block_type", "")
                html      = (block.get("html") or "").strip()

                if btype_raw in ("PageFooter", "PageHeader"):
                    continue
                if not html:
                    continue

                if btype_raw == "SectionHeader":
                    level, text = parse_heading(html)
                    flat.append({"type": "heading", "level": level,
                                 "text": text, "page": page_num, "raw": block})

                elif btype_raw == "ListGroup":
                    text = listgroup_to_lines(html)
                    if text:
                        flat.append({"type": "text", "level": 0,
                                     "text": text, "page": page_num, "raw": block})

                elif btype_raw == "Equation":
                    latex = extract_math(html)
                    if latex:
                        flat.append({"type": "equation", "level": 0,
                                     "text": latex, "latex": latex,
                                     "page": page_num, "raw": block})

                elif btype_raw in ("Figure", "Picture"):
                    # Get image key from block's images dict
                    block_images = block.get("images") or {}
                    image_key    = next(iter(block_images.keys()), "")
                    alt_text     = extract_alt_text(html)

                    # Skip decorative artifacts (horizontal lines, dividers)
                    alt_lower = alt_text.lower().strip()
                    if alt_lower in ("horizontal line", "vertical line",
                                     "divider", "line", "rule", "separator"):
                        continue

                    # Bidirectional caption association
                    caption = self._find_figure_caption(children, idx, alt_text)

                    flat.append({"type": "figure", "level": 0,
                                 "text": alt_text, "image_key": image_key,
                                 "alt_text": alt_text, "caption": caption,
                                 "page": page_num, "raw": block})

                elif btype_raw == "Caption":
                    text = strip_html(html)
                    if text:
                        flat.append({"type": "caption", "level": 0,
                                     "text": text, "page": page_num, "raw": block})

                elif btype_raw == "Table":
                    flat.append({"type": "table", "level": 0,
                                 "text": html, "page": page_num, "raw": block})

                else:
                    # Text and anything else
                    text = strip_html(html)
                    if text:
                        flat.append({"type": "text", "level": 0,
                                     "text": text, "page": page_num, "raw": block})

        return flat

    def _find_figure_caption(self, siblings: list, fig_idx: int,
                              alt_text: str) -> str:
        """
        Find the caption for a Figure block using bidirectional search.

        Strategy:
          1. Look at the block immediately before — if Caption, use it
          2. Look at the block immediately after  — if Caption, use it
          3. Look at block after for SectionHeader "Notes to Figure X"
          4. Try to extract a figure number from alt text
          5. Return empty string if nothing found
        """
        # Check block before
        if fig_idx > 0:
            prev = siblings[fig_idx - 1]
            if prev.get("block_type") == "Caption":
                return strip_html(prev.get("html", ""))

        # Check block after
        if fig_idx < len(siblings) - 1:
            nxt = siblings[fig_idx + 1]
            if nxt.get("block_type") == "Caption":
                return strip_html(nxt.get("html", ""))
            # e.g. <h5>Notes to Figure 4.1.6.5.-A:</h5>
            if nxt.get("block_type") == "SectionHeader":
                m = re.search(r'Notes to (Figure\s+[\w\.\-]+)',
                              nxt.get("html", ""), re.IGNORECASE)
                if m:
                    return m.group(1)

        # Fallback: extract figure number from alt text
        m = RE_FIGURE_NUM.search(alt_text)
        if m:
            return f"Figure {m.group(1)}"

        return ""

    def _flatten_legacy(self, datalab_result: dict) -> list:
        """Fallback for old API format or markdown-only responses."""
        flat = []
        for page_num, page in enumerate(
                datalab_result.get("pages", []), start=1):
            for block in page.get("blocks", []):
                flat.append({
                    "type":  block.get("block_type", "text"),
                    "text":  block.get("html", block.get("text", "")).strip(),
                    "level": block.get("level", 0),
                    "page":  page_num, "raw": block,
                })
        if not flat and datalab_result.get("markdown"):
            for line in datalab_result["markdown"].splitlines():
                s = line.strip()
                if not s:
                    continue
                for prefix, lvl in [("#### ",4),("### ",3),("## ",2),("# ",1)]:
                    if s.startswith(prefix):
                        flat.append({"type":"heading","level":lvl,
                                     "text":s[len(prefix):],"page":1,"raw":{}})
                        break
                else:
                    flat.append({"type":"text","level":0,"text":s,"page":1,"raw":{}})
        return flat

    # -------------------------------------------------------------------------
    # Title detection
    # -------------------------------------------------------------------------

    def _detect_title(self, blocks: list) -> str:
        for b in blocks:
            if b["type"] == "heading" and b.get("level", 0) == 1:
                return b["text"]
        return "Building Code Document"

    # -------------------------------------------------------------------------
    # Hierarchy builder
    # -------------------------------------------------------------------------

    def _build_hierarchy(self, blocks: list) -> List[Chapter]:
        """
        Walk all blocks in order and build:
            Chapter -> Section -> Clause -> content[]

        Each content item is appended in document order so reading
        sequence is preserved in the output.
        """
        chapters: List[Chapter] = []
        current_chapter: Optional[Chapter] = None
        current_section: Optional[Section] = None
        current_clause:  Optional[Clause]  = None
        pending_caption: str = ""   # caption buffer for next Table block

        def add_text(text: str, page: int):
            """Add a text ContentItem to current clause, extracting sub-clauses."""
            if not text or not current_clause:
                return
            # Check if this is a sub-clause marker line
            for line in text.splitlines():
                m = RE_SUBCLAUSE.match(line)
                if m:
                    current_clause.content.append(ContentItem(
                        type="sub_clause",
                        marker=m.group(1),
                        value=m.group(2).strip(),
                    ))
                else:
                    if line.strip():
                        current_clause.content.append(ContentItem(
                            type="text", value=line.strip()
                        ))
            if page not in current_clause.page_span:
                current_clause.page_span.append(page)

        for block in blocks:
            btype = block["type"]
            text  = block.get("text", "")
            page  = block["page"]
            level = block.get("level", 0)

            # ── Headings ──────────────────────────────────────────────────────
            if btype == "heading":

                if level <= 1:
                    num, title = self._parse_part_heading(text)
                    current_chapter = Chapter(
                        id=f"CH-{num}", number=num,
                        title=title, page_span=[page]
                    )
                    chapters.append(current_chapter)
                    current_section = None
                    current_clause  = None

                elif level == 2:
                    m = RE_SECTION.match(text)
                    if m and current_chapter:
                        num, title = m.group(1), (m.group(2).strip() or m.group(1))
                        current_section = Section(
                            id=f"SEC-{num.replace('.', '-')}",
                            number=num, title=title, page_span=[page]
                        )
                        current_chapter.sections.append(current_section)
                        current_clause = None
                    # else: orphan heading — skip

                elif level == 3:
                    # Could be 3-part "4.1.6." or plain subsection title
                    m3 = RE_ARTICLE.match(text)
                    if m3 and current_chapter:
                        num   = m3.group(1)
                        title = m3.group(2).lstrip(". ").strip() or num
                        current_section = Section(
                            id=f"SEC-{num.replace('.', '-')}",
                            number=num, title=title, page_span=[page]
                        )
                        current_chapter.sections.append(current_section)
                        current_clause = None
                    elif current_section:
                        # Plain subsection title — treat as a label clause
                        current_clause = self._make_clause("", text, page,
                                                           current_section)

                elif level == 4:
                    # Primary clause level in Part 4: "4.1.6.5. Multi-level Roofs"
                    # Check 4-part BEFORE 3-part
                    m4 = RE_SENTENCE.match(text)
                    m3 = RE_ARTICLE.match(text)
                    if m4 and current_section:
                        num   = m4.group(1)
                        title = m4.group(2).lstrip(". ").strip() or num
                        current_clause = self._make_clause(num, title, page,
                                                           current_section)
                    elif m3 and current_section:
                        num   = m3.group(1)
                        title = m3.group(2).lstrip(". ").strip() or num
                        current_clause = self._make_clause(num, title, page,
                                                           current_section)
                    elif current_section:
                        current_clause = self._make_clause("", text, page,
                                                           current_section)

                elif level == 5:
                    # Two cases:
                    # a) "Notes to Table X" / "Notes to Figure X" subsections
                    # b) Appendix entries: "A-4.1.3.2.(2) Load Combinations."
                    # Both become new clauses that receive following content.
                    clean = re.sub(r'\s+', ' ', strip_html(text)).strip()
                    if current_section:
                        current_clause = self._make_clause("", clean, page,
                                                           current_section)

                elif level >= 6:
                    # Sub-article, appendix sub-entry, or importance category
                    # e.g. "Low Importance Category" / "A-4.1.8.2.(1) Notation"
                    clean = re.sub(r'\s+', ' ', strip_html(text)).strip()
                    if current_clause is not None:
                        # Sub-labels like "Low/Normal/High Importance Category"
                        # are headings within the current clause body
                        current_clause.content.append(
                            ContentItem(type="text", value=f"**{clean}**"))
                        if page not in current_clause.page_span:
                            current_clause.page_span.append(page)
                    elif current_section:
                        current_clause = self._make_clause("", clean, page,
                                                           current_section)

            # ── Text ──────────────────────────────────────────────────────────
            elif btype == "text":
                first_line = text.splitlines()[0] if text else ""
                # Auto-detect structural numbers in text blocks.
                # Always check 4-part BEFORE 3-part.
                # GUARD: only promote to new clause/section if the number
                # is not already registered — prevents duplicate clauses
                # from appendix text blocks like "A-4.1.5.5." that match
                # the same regex as the heading already processed above.
                m4  = RE_SENTENCE.match(first_line)
                m3  = RE_ARTICLE.match(first_line)
                sec = RE_SECTION.match(first_line)

                if m4 and current_section:
                    num   = m4.group(1)
                    cid   = f"CL-{num.replace('.', '-')}"
                    # Only create if this ID doesn't already exist in section
                    existing = any(cl.id == cid
                                   for cl in current_section.clauses)
                    if not existing:
                        title = m4.group(2).lstrip(". ").strip() or num
                        current_clause = self._make_clause(num, title, page,
                                                           current_section)
                    else:
                        add_text(text, page)
                elif m3 and current_chapter:
                    num   = m3.group(1)
                    sid   = f"SEC-{num.replace('.', '-')}"
                    existing = any(s.id == sid
                                   for s in current_chapter.sections)
                    if not existing:
                        title = m3.group(2).lstrip(". ").strip() or num
                        current_section = Section(
                            id=sid, number=num,
                            title=title, page_span=[page]
                        )
                        current_chapter.sections.append(current_section)
                        current_clause = None
                    else:
                        add_text(text, page)
                elif sec and current_chapter:
                    sid = f"SEC-{sec.group(1).replace('.', '-')}"
                    existing = any(s.id == sid
                                   for s in current_chapter.sections)
                    if not existing:
                        current_section = Section(
                            id=sid,
                            number=sec.group(1),
                            title=sec.group(2).strip(), page_span=[page]
                        )
                        current_chapter.sections.append(current_section)
                        current_clause = None
                    else:
                        add_text(text, page)
                else:
                    add_text(text, page)

            # ── Equation ──────────────────────────────────────────────────────
            elif btype == "equation":
                if current_clause:
                    self._equation_counter += 1
                    eq_id  = f"EQ-{self._equation_counter}"
                    latex  = block.get("latex", text)
                    eq_obj = Equation(id=eq_id, latex=latex, page=page)
                    current_clause.equations.append(eq_obj)
                    current_clause.content.append(ContentItem(
                        type="equation", latex=latex, value=eq_id
                    ))
                    if page not in current_clause.page_span:
                        current_clause.page_span.append(page)

            # ── Figure ────────────────────────────────────────────────────────
            elif btype == "figure":
                self._figure_counter += 1
                fig_id    = f"FIG-{self._figure_counter}"
                image_key = block.get("image_key", "")
                alt_text  = block.get("alt_text", "")
                caption   = block.get("caption", "")

                # Save image to disk
                image_path = ""
                if image_key and image_key in self._images_dict:
                    image_path = save_image(
                        image_key,
                        self._images_dict[image_key],
                        self.figures_dir
                    )

                fig_obj = Figure(
                    id=fig_id, caption=caption, alt_text=alt_text,
                    image_key=image_key, image_path=image_path, page=page
                )
                content_item = ContentItem(
                    type="figure", figure_id=fig_id,
                    image_key=image_key, image_path=image_path,
                    caption=caption, alt_text=alt_text
                )

                # Skip purely decorative images (horizontal rules, dividers)
                # Only filter if the ENTIRE alt text is a short decorative description.
                # Do NOT filter figures whose alt text merely mentions lines within a diagram.
                alt_stripped = alt_text.strip().lower()
                is_decorative = (
                    len(alt_stripped) < 60 and          # short alt text
                    any(kw == alt_stripped or            # exact match
                        alt_stripped.startswith(kw)      # starts with decorative label
                        for kw in ("horizontal line", "vertical line", "divider", 
                                   "separator", "solid black line", "decorative"))
                )

                if is_decorative:
                    self._figure_counter -= 1   # don't count it
                    continue

                if current_clause:
                    # Normal case: attach to active clause
                    current_clause.figures.append(fig_obj)
                    current_clause.content.append(content_item)
                    if page not in current_clause.page_span:
                        current_clause.page_span.append(page)
                elif current_section:
                    # Orphaned figure with no active clause
                    # Create a minimal holder so it's not lost
                    orphan = self._make_clause(
                        "", caption or alt_text[:60] or f"Figure {fig_id}",
                        page, current_section
                    )
                    orphan.figures.append(fig_obj)
                    orphan.content.append(content_item)
                    current_clause = orphan

            # ── Caption (for tables — figures handled above) ──────────────────
            elif btype == "caption":
                pending_caption = text

            # ── Table ─────────────────────────────────────────────────────────
            elif btype == "table":
                if current_clause:
                    self._table_counter += 1
                    tbl_id  = f"TBL-{self._table_counter}"
                    caption = pending_caption or f"Table {self._table_counter}"
                    pending_caption = ""
                    headers, rows = parse_table_html(text)
                    tbl_obj = Table(
                        id=tbl_id, caption=caption,
                        headers=headers, rows=rows, page=page
                    )
                    current_clause.tables.append(tbl_obj)
                    current_clause.content.append(ContentItem(
                        type="table", table_id=tbl_id,
                        value=caption
                    ))
                    if page not in current_clause.page_span:
                        current_clause.page_span.append(page)
                else:
                    pending_caption = ""

        return self._remove_empty_clauses(chapters)

    def _remove_empty_clauses(self, chapters: List[Chapter]) -> List[Chapter]:
        """
        Post-process step: remove clauses that have no content at all.
        These arise from consecutive heading blocks with nothing between them
        (e.g. two h5 Notes headings in a row in Appendix A).
        Clauses with at least a title but no body are kept if they have
        figures, tables, or equations - only truly empty shells are removed.
        """
        for chapter in chapters:
            for section in chapter.sections:
                section.clauses = [
                    cl for cl in section.clauses
                    if cl.content or cl.figures or cl.tables or cl.equations
                ]
        return chapters

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _make_clause(self, number: str, title: str,
                     page: int, section: Section) -> Clause:
        """Create a new Clause and append it to the given section."""
        cl = Clause(
            id=self._clause_id_for(number),
            number=number, title=title,
            page_span=[page]
        )
        section.clauses.append(cl)
        return cl

    def _clause_id_for(self, number: str) -> str:
        if number:
            return f"CL-{number.replace('.', '-')}"
        self._chapter_counter += 1
        return f"CL-AUTO-{self._chapter_counter}"

    def _parse_part_heading(self, text: str):
        self._chapter_counter += 1
        m = RE_PART.match(text)
        if m:
            return m.group(1), m.group(2).strip() or text
        return str(self._chapter_counter), text

    def to_dict(self, document: Document) -> dict:
        return asdict(document)


# =============================================================================
# Public entry point
# =============================================================================

def parse_datalab_output(datalab_result: dict, source_pdf: str = "unknown.pdf",
                         figures_dir: str = "storage/figures") -> dict:
    """
    Parse Datalab result -> return JSON-serializable structured document dict.
    Called by main.py.

    Args:
        datalab_result: Full Datalab API response dict
        source_pdf:     Original PDF filename
        figures_dir:    Directory to save extracted figure images

    Returns:
        dict with document tree including ordered content[] on every clause
    """
    parser = StructureParser(source_pdf=source_pdf, figures_dir=figures_dir)
    document = parser.parse(datalab_result)
    return parser.to_dict(document)