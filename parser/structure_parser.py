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


# Split pattern for inline <math>...</math> within HTML
_INLINE_MATH_SPLIT = re.compile(
    r'(<math[^>]*>.*?</math>)',
    re.DOTALL | re.IGNORECASE
)


def _strip_html_keep_text(html: str) -> str:
    """
    Strip all HTML tags except <math> markers, decode entities.
    Used when splitting inline-math blocks so non-math text is preserved.
    """
    text = re.sub(r'<(?!/?math)[^>]+>', ' ', html)
    text = (text
            .replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            .replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"'))
    return re.sub(r'\s+', ' ', text).strip()


def inline_math_to_markdown(html: str) -> str:
    """
    FIX (Enhancement 1 rewrite): Convert a Text block HTML that contains
    inline <math> tags into a single markdown string using $...$ notation.

    Previous approach (split_inline_math) created separate ContentItems of
    type='equation' for each <math> tag.  This caused every inline variable
    like $S$, $I_s$, $C_b$ to be rendered by st.latex() as a large centered
    DISPLAY equation — completely breaking sentence flow.

    This approach instead:
      1. Replaces each <math>...</math> with $latex$ inline notation
      2. Strips the remaining HTML tags
      3. Returns one clean markdown string
      4. The caller emits a single ContentItem(type='text', value=...) 
         which render_text_item() renders with st.markdown(), giving proper
         inline math rendering within the sentence.

    Example:
        Input:  '<p>The load, <math>S</math>, due to snow...</p>'
        Output: 'The load, $S$, due to snow...'
        Rendered by st.markdown() as: 'The load, S (inline symbol), due to snow...'

        Input:  '<p><math>I_s</math> = importance factor for snow load,...</p>'
        Output: '$I_s$ = importance factor for snow load,...'
        Rendered inline with I_s as a math symbol.
    """
    def _clean_latex(inner: str) -> str:
        latex = inner.strip()
        latex = latex.replace('\\\\', '\\')
        latex = (latex.replace('&amp;', '&').replace('&lt;', '<')
                 .replace('&gt;', '>').replace('&nbsp;', ' '))
        return re.sub(r'\s+', ' ', latex).strip()

    # Replace <math>...</math> with $...$
    result = re.sub(
        r'<math[^>]*>(.*?)</math>',
        lambda m: f'${_clean_latex(m.group(1))}$',
        html,
        flags=re.DOTALL | re.IGNORECASE
    )
    # Strip all remaining HTML tags
    result = re.sub(r'<(?!/?math)[^>]+>', ' ', result)
    result = (result
              .replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
              .replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"'))
    return re.sub(r'\s+', ' ', result).strip()


def split_inline_math(html: str) -> list:
    """
    Legacy compatibility shim — kept so existing callers don't break.
    Returns a single-item list containing the reconstructed markdown string.
    The type is 'text' so the viewer uses st.markdown() (inline rendering)
    rather than st.latex() (block rendering).
    """
    value = inline_math_to_markdown(html)
    if value:
        return [{"type": "text", "value": value}]
    return []


def extract_math(html: str) -> list:
    """
    Extract LaTeX from <math> tags in an Equation block.

    FIX: Previously joined all <math> tags with a space, merging separate
    display equations into one unreadable string.  Now returns a LIST so
    each <math display="block"> tag becomes its own rendered equation.

    Returns:
        List of LaTeX strings, one per <math> tag found.
        Empty list if no <math> tags present (caller falls back to strip_html).

    The list allows the parser to create one Equation ContentItem per element,
    and the viewer to call st.latex() once per equation — preserving alignment
    and line breaks exactly as the PDF shows them.
    """
    raw_parts = re.findall(r'<math[^>]*>(.*?)</math>', html,
                           re.DOTALL | re.IGNORECASE)
    result = []
    for p in raw_parts:
        latex = p.strip()
        # JSON double-backslash -> single backslash
        latex = latex.replace('\\\\', '\\')
        latex = (latex.replace('&amp;', '&').replace('&lt;', '<')
                 .replace('&gt;', '>').replace('&nbsp;', ' '))
        latex = re.sub(r'\s+', ' ', latex).strip()
        if latex:
            result.append(latex)
    return result


def parse_heading(html: str):
    """Extract (level, plain_text) from a SectionHeader HTML block."""
    m = re.match(r'<h(\d)[^>]*>(.*?)</h\1>', html.strip(), re.DOTALL | re.IGNORECASE)
    if m:
        return int(m.group(1)), strip_html(m.group(2))
    return 0, strip_html(html)


def listgroup_to_lines(html: str) -> str:
    """
    Convert ListGroup HTML to newline-separated lines.

    FIX (Issue 2a): The previous version called strip_html() which stripped
    all HTML tags including <math>.  Sub-clause items like:
        "conform to Table 4.1.6.2.-B, using linear interpolation for
         intermediate values of <math>l_c C_w^2</math>, or"
    had their math content silently dropped, showing "l_c C_w^2" as plain text.

    Now: lines that contain <math> tags are processed by inline_math_to_markdown()
    to produce "$l_c C_w^2$", which st.markdown() renders as inline math.
    Lines without math still go through the original path.
    """
    # Replace </li> with newline BEFORE stripping tags so each item is its own line
    text_with_newlines = re.sub(r'</li>', '\n', html, flags=re.IGNORECASE)
    lines = []
    for raw_line in text_with_newlines.splitlines():
        if re.search(r'<math', raw_line, re.IGNORECASE):
            # Preserve inline math as $...$ notation
            line = inline_math_to_markdown(raw_line)
        else:
            line = re.sub(r'<[^>]+>', '', raw_line)
            line = (line.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                    .replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"'))
        line = re.sub(r'[ \t]+', ' ', line).strip()
        if line:
            lines.append(line)
    return '\n'.join(lines)


def parse_table_html(html: str):
    """
    Parse HTML table into (headers, rows) with correct rowspan and colspan handling.

    FIX (Issue 2 — duplicate column names):
        Tables like Table 4.1.6.10 have a multi-row <thead> where parent cells
        span multiple columns and child cells fill the sub-columns:

          Row 1: | Load Case (rs=3) | Range (rs=3) | Factors (cs=4)         |
          Row 2: |                  |              | All (cs=1) | Arch (cs=2) | Domes |
          Row 3: |                  |              | C_w        | C_a Up | C_a Down | C_a Down |

        The old code used re.findall('<th>') which reads all <th> tags in flat
        order, giving duplicates when a parent label and child labels both
        appear for the same logical column.

        The new approach applies the same rowspan_carry algorithm to <thead>
        rows that was already used for <tbody> rows, then builds final headers
        by joining parent and child labels with ' / ' so the hierarchy is
        visible and column names are unique.

    Existing fixes retained:
      - rowspan in tbody: carried across rows via rowspan_carry
      - full-width colspan in tbody: placed in col 0 only
    """
    # ── Step 1: flatten multi-row thead into a label grid ────────────────────
    headers: list = []

    thead = re.search(r'<thead[^>]*>(.*?)</thead>', html, re.DOTALL | re.IGNORECASE)
    if thead:
        thead_html   = thead.group(1)
        header_rows  = re.findall(r'<tr[^>]*>(.*?)</tr>', thead_html,
                                  re.DOTALL | re.IGNORECASE)

        if len(header_rows) <= 1:
                    # Simple single-row header — original fast path
                    ths     = re.findall(r'<th[^>]*>(.*?)</th>', thead_html,
                                         re.DOTALL | re.IGNORECASE)
                    headers = [
                        inline_math_to_markdown(th) if re.search(r'<math', th, re.IGNORECASE)
                        else strip_html(th)
                        for th in ths
                    ]
        else:
                    # Multi-row header: build a label_grid[row][col] then collapse.
                    #
                    # FIX (Issue 3): Some header rows are "spanning subheader rows" —
                    # a single <th> cell with colspan > 1 that acts as a section label
                    # (e.g. "Value of C_b" spanning all 3 data columns in Table 4.1.6.2.-B).
                    # These are NOT individual column labels; including them in every
                    # column name produces "Value of Cw / 1.0 / Value of Cb" instead
                    # of the correct "Value of Cw / 1.0".
                    #
                    # Detection: a row is a spanning subheader if, after excluding
                    # rowspan-carry columns, it has exactly ONE unique new cell AND
                    # that cell's colspan > 1.  Such rows contribute their label only
                    # to column 0 (for context), not to each individual data column.
                    #
                    # FIX (Issue 1 extension): Header cell content that contains
                    # <math> tags is processed by inline_math_to_markdown() so symbols
                    # like l_c C_w^2 appear as "$l_c C_w^2$" in column headers.

                    # First pass: estimate num_cols from row 0 colspan sum
                    first_ths = re.findall(r'<th([^>]*)>(.*?)</th>', header_rows[0],
                                           re.DOTALL | re.IGNORECASE)
                    num_cols = 0
                    for attrs, _ in first_ths:
                        cs = re.search(r'colspan=["\'](\d+)["\']', attrs)
                        num_cols += int(cs.group(1)) if cs else 1

                    n_rows     = len(header_rows)
                    label_grid = [[''] * num_cols for _ in range(n_rows)]
                    th_carry   = {}   # col -> (rows_remaining, text)
                    # Track new-cell info per row for spanning detection
                    row_new_cells: list = [[] for _ in range(n_rows)]  # list of (col, colspan, label)

                    for row_i, tr_html in enumerate(header_rows):
                        th_matches = re.findall(r'<th([^>]*)>(.*?)</th>', tr_html,
                                                re.DOTALL | re.IGNORECASE)
                        th_iter = iter(th_matches)
                        col     = 0

                        while col < num_cols:
                            if col in th_carry:
                                remaining, label = th_carry[col]
                                label_grid[row_i][col] = label
                                if remaining - 1 > 0:
                                    th_carry[col] = (remaining - 1, label)
                                else:
                                    del th_carry[col]
                                col += 1
                                continue

                            try:
                                attrs, cell_html = next(th_iter)
                            except StopIteration:
                                col += 1
                                continue

                            # Preserve inline math in header cell labels
                            if re.search(r'<math', cell_html, re.IGNORECASE):
                                label = inline_math_to_markdown(cell_html)
                            else:
                                label = strip_html(cell_html)

                            rs      = re.search(r'rowspan=["\'](\d+)["\']', attrs)
                            cs      = re.search(r'colspan=["\'](\d+)["\']', attrs)
                            rowspan = int(rs.group(1)) if rs else 1
                            colspan = int(cs.group(1)) if cs else 1

                            for c in range(colspan):
                                if col + c < num_cols:
                                    label_grid[row_i][col + c] = label

                            if rowspan > 1:
                                for c in range(colspan):
                                    if col + c < num_cols:
                                        th_carry[col + c] = (rowspan - 1, label)

                            row_new_cells[row_i].append((col, colspan, label))
                            col += colspan

                    # Identify spanning subheader rows.
                    # A spanning row is one where a single cell spans all data
                    # columns (colspan > 1) and acts as a parent grouping label
                    # (e.g. "Factors" above individual sub-column labels).
                    # EXCEPTION: the LAST header row is never treated as a
                    # spanning subheader — a spanning final row is the primary
                    # data descriptor (e.g. "Value of C_b" in Table 4.1.6.2.-B)
                    # and must appear in every column header.
                    spanning_rows: set = set()
                    for row_i in range(n_rows):
                        if row_i == n_rows - 1:
                            continue   # last row: always include in all cols
                        new = row_new_cells[row_i]
                        if len(new) == 1 and new[0][1] > 1:
                            # Single new cell with colspan > 1 -> spanning label
                            spanning_rows.add(row_i)

                    # Collapse rows into column names
                    headers = []
                    seen_names: dict = {}
                    for col in range(num_cols):
                        parts = []
                        for row_i in range(n_rows):
                            lbl = label_grid[row_i][col].strip()
                            if not lbl:
                                continue
                            if parts and parts[-1] == lbl:
                                continue  # rowspan duplicate
                            if row_i in spanning_rows and col > 0:
                                # Spanning subheader: add to col 0 only for context,
                                # skip for all other data columns
                                continue
                            # FIX: Datalab sometimes underreports rowspan (e.g. rowspan=2
                            # when the PDF has 3 header rows), causing column-identifier
                            # sub-labels like "1", "1E" to land in columns that already
                            # have a long descriptive header (e.g. "Load Case").
                            # Detect: final row, short column-id label (≤4 chars, looks
                            # like a number/identifier), column already has a longer label.
                            # In that case skip — the sub-label belongs to a later column.
                            if (row_i == n_rows - 1 and parts
                                    and len(lbl) <= 4
                                    and re.match(r'^[0-9A-Z]+$', lbl)
                                    and len(parts[-1]) > len(lbl) + 2):
                                continue
                            parts.append(lbl)
                        name = ' / '.join(parts) if parts else f"Col {col+1}"
                        if name in seen_names:
                            seen_names[name] += 1
                            name = f"{name} ({seen_names[name]})"
                        else:
                            seen_names[name] = 1
                        headers.append(name)

    num_cols = len(headers) if headers else 2

    # ── Step 2: parse tbody rows (unchanged logic) ────────────────────────────
    rows: list = []

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
            if col in rowspan_carry:
                remaining, value = rowspan_carry[col]
                row[col] = value
                if remaining - 1 > 0:
                    rowspan_carry[col] = (remaining - 1, value)
                else:
                    del rowspan_carry[col]
                col += 1
                continue

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

            if colspan >= num_cols:
                row[0] = cell_value
            else:
                for c in range(colspan):
                    if col + c < num_cols:
                        row[col + c] = cell_value

            if rowspan > 1:
                for c in range(min(colspan, num_cols)):
                    if col + c < num_cols:
                        rowspan_carry[col + c] = (rowspan - 1, cell_value)

            col += colspan

        if any(c.strip() for c in row):
            rows.append(row)

    # ── Step 3: bbox-based carry for empty cells ─────────────────────────────
    # Some tables (e.g. Table 4.1.5.3) have load cells with bbox coordinates
    # that span multiple rows, but without HTML rowspan attributes — Datalab
    # emits the value only in the first row and leaves the remaining rows empty.
    # We fix this by checking, for each empty cell, whether a preceding row's
    # load-cell bbox vertically covers this row's use-cell bbox.
    #
    # We store (last_value, last_load_y2) for each column as we scan rows.
    # For each row with an empty cell in column c, if last_load_y2[c] exceeds
    # this row's use_bbox y1, we carry the last value into this cell.
    #
    # This is a post-processing pass: the normal rowspan_carry path above
    # already handles explicit rowspan="N" attributes.  This pass only fills
    # gaps that the explicit-rowspan path missed.

    # Extract per-row bbox info from the original HTML (not from parsed rows,
    # which have lost the attribute data).  We rebuild a parallel list of
    # (use_y1, load_y2) per row.

    row_bboxes: list = []   # parallel to rows[] after filtering
    trs_all = re.findall(r'<tr[^>]*>(.*?)</tr>',
                         (tbody.group(1) if tbody else ""),
                         re.DOTALL | re.IGNORECASE)

    for tr in trs_all:
        td_matches = re.findall(r'<td([^>]*)>', tr, re.IGNORECASE)
        if len(td_matches) < 2:
            continue
        use_attrs  = td_matches[0]
        load_attrs = td_matches[1]

        def _y(attrs_str, coord_idx):
            m = re.search(r'data-bbox=["\']([^"\']+)["\']', attrs_str)
            if not m:
                return None
            parts = m.group(1).split()
            return int(parts[coord_idx]) if len(parts) == 4 else None

        use_y1   = _y(use_attrs,  1)   # y1 of the use-text cell
        load_y2  = _y(load_attrs, 3)   # y2 of the load-value cell

        # Only include rows that would have passed the "any non-empty" filter
        td_content = re.findall(r'<td[^>]*>(.*?)</td>', tr,
                                re.DOTALL | re.IGNORECASE)
        row_texts = [re.sub(r'<[^>]+>', '', c).strip() for c in td_content]
        if any(t for t in row_texts):
            row_bboxes.append((use_y1, load_y2))

    # Carry pass: for each column, track the last non-empty value and its load_y2
    if row_bboxes and len(row_bboxes) == len(rows):
        # Build per-column carry state
        col_last_val  = {}   # col -> last non-empty value seen
        col_last_y2   = {}   # col -> load_y2 of that last value's row

        for row_i, (row, (use_y1, load_y2)) in enumerate(zip(rows, row_bboxes)):
            for col_idx in range(len(row)):
                val = row[col_idx]
                if val.strip():
                    # Record this non-empty value for potential carry forward
                    col_last_val[col_idx] = val
                    if load_y2 is not None:
                        col_last_y2[col_idx] = load_y2
                else:
                    # Empty cell: fill if last value's bbox covers this row
                    if (col_idx in col_last_val
                            and col_idx in col_last_y2
                            and use_y1 is not None
                            and col_last_y2[col_idx] > use_y1):
                        rows[row_i][col_idx] = col_last_val[col_idx]

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
        self._chapter_counter    = 0   # counts real chapters only (h1 headings)
        self._auto_clause_counter = 0  # counts unnumbered CL-AUTO-N clauses only
        self._table_counter      = 0
        self._equation_counter   = 0
        self._figure_counter     = 0
        self._images_dict        = {}   # populated from datalab result

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
                    latex_list = extract_math(html)
                    if not latex_list:
                        # No <math> tags found — fall back to plain text
                        text = strip_html(html)
                        if text:
                            latex_list = [text]
                    # Emit one flat block per equation so they each get their
                    # own ContentItem and their own st.latex() call in the viewer.
                    # This preserves the line structure of multi-equation blocks
                    # (e.g. a piecewise definition with two cases on separate lines).
                    for latex in latex_list:
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
                    # FIX (Issue 1): Use inline_math_to_markdown instead of
                    # strip_html so that math symbols in table captions like
                    # "Importance Factor for Snow Load, I_s" are preserved as
                    # "$I_s$" and rendered inline by st.markdown() in the viewer.
                    if re.search(r'<math', html, re.IGNORECASE):
                        text = inline_math_to_markdown(html)
                    else:
                        text = strip_html(html)
                    if text:
                        flat.append({"type": "caption", "level": 0,
                                     "text": text, "page": page_num, "raw": block})

                elif btype_raw == "Table":
                    flat.append({"type": "table", "level": 0,
                                 "text": html, "page": page_num, "raw": block})

                else:
                    # Text and anything else.
                    # ENHANCEMENT 1: If the HTML contains inline <math> tags,
                    # preserve the raw HTML so _build_hierarchy can split it
                    # into alternating text/equation ContentItems.
                    # For plain text blocks (no math) we strip HTML as before.
                    if re.search(r'<math', html, re.IGNORECASE):
                        # Store raw HTML; mark as needing inline-math expansion
                        flat.append({"type": "text", "level": 0,
                                     "text": html, "has_inline_math": True,
                                     "page": page_num, "raw": block})
                    else:
                        text = strip_html(html)
                        if text:
                            flat.append({"type": "text", "level": 0,
                                         "text": text, "page": page_num,
                                         "raw": block})

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

        def add_text(text: str, page: int, has_inline_math: bool = False):
            """
            Add text content to the current clause.

            FIX (Enhancement 1): If has_inline_math is True, the text argument
            is raw HTML containing inline <math> tags.  We call
            inline_math_to_markdown() to produce a single text string with
            $...$ notation, which st.markdown() renders as proper inline math.

            Previous approach called split_inline_math() which created separate
            ContentItem(type='equation') entries for each <math> tag.  This
            caused st.latex() to render every inline variable (S, I_s, C_b)
            as a large centered display equation, completely breaking sentence
            flow and looking worse than Datalab's raw output.
            """
            if not text or not current_clause:
                return

            if has_inline_math:
                # Convert to a single markdown string with $...$ inline math
                markdown_text = inline_math_to_markdown(text)
                if markdown_text:
                    for line in markdown_text.splitlines():
                        sc = RE_SUBCLAUSE.match(line)
                        if sc:
                            current_clause.content.append(ContentItem(
                                type="sub_clause",
                                marker=sc.group(1),
                                value=sc.group(2).strip(),
                            ))
                        elif line.strip():
                            current_clause.content.append(ContentItem(
                                type="text", value=line.strip()
                            ))
                if page not in current_clause.page_span:
                    current_clause.page_span.append(page)
                return

            # Plain text path — extract sub-clause markers line by line
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
                has_inline_math = block.get("has_inline_math", False)
                first_line = text.splitlines()[0] if text else ""
                # Auto-detect structural numbers in text blocks.
                # Always check 4-part BEFORE 3-part.
                # GUARD: only promote to new clause/section if the number
                # is not already registered — prevents duplicate clauses
                # from appendix text blocks like "A-4.1.5.5." that match
                # the same regex as the heading already processed above.
                #
                # NOTE: When has_inline_math is True, text is raw HTML so
                # strip_html is applied here for the structural-number check
                # only.  The full HTML is passed to add_text for proper
                # inline-math splitting.
                check_line = strip_html(first_line) if has_inline_math else first_line
                m4  = RE_SENTENCE.match(check_line)
                m3  = RE_ARTICLE.match(check_line)
                sec = RE_SECTION.match(check_line)

                if m4 and current_section:
                    num   = m4.group(1)
                    cid   = f"CL-{num.replace('.', '-')}"
                    existing = any(cl.id == cid
                                   for cl in current_section.clauses)
                    if not existing:
                        title = m4.group(2).lstrip(". ").strip() or num
                        current_clause = self._make_clause(num, title, page,
                                                           current_section)
                    else:
                        add_text(text, page, has_inline_math)
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
                        add_text(text, page, has_inline_math)
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
                        add_text(text, page, has_inline_math)
                else:
                    add_text(text, page, has_inline_math)

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

        return self._merge_continued_tables(self._remove_empty_clauses(chapters))

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

    def _merge_continued_tables(self, chapters: List[Chapter]) -> List[Chapter]:
        """
        FIX (Bug 4 + cross-page rowspan): Merge multi-page "(continued)" table
        fragments into a single Table object within each clause.

        Cross-page rowspan fix:
            When a table cell has rowspan="N" on page P, Datalab splits the
            table at the page break, leaving the continuation rows on page P+1
            with empty cells where the value should be carried.  The
            parse_table_html() step handles intra-page bbox carry, but
            cross-page carries are impossible at parse time (page P+1 is a
            separate Table block with no knowledge of page P's rowspan state).
            After merging all page fragments into one row list, we run a final
            cross-row carry pass: for each column whose last non-empty value
            has a load_bbox_y2 that exceeds the NEXT page's first row's
            use_bbox_y1, we carry that value forward.
            Since bbox info is lost after parse_table_html, we detect cross-page
            empty-carry gaps by checking rows that are empty in column c but
            immediately follow a row that also had a non-empty value at the same
            approximate indentation level in column 0 (same parent group).
        """
        cont_re = re.compile(r'\s*\(continued\)', re.IGNORECASE)

        def _tbl_number_norm(caption: str) -> str:
            cap = cont_re.sub('', caption).strip()
            m = re.match(r'(?:Table\s+)?([\d\.A-Za-z\-]+)', cap, re.IGNORECASE)
            if m:
                return re.sub(r'[.\-\s]', '', m.group(1)).lower()
            return cap.lower()

        for chapter in chapters:
            for section in chapter.sections:
                for clause in section.clauses:
                    if len(clause.tables) <= 1:
                        continue

                    ids_to_remove: set = set()
                    base_map: dict = {}   # norm_number -> Table (base)

                    for tbl in clause.tables:
                        cap      = tbl.caption
                        is_cont  = bool(cont_re.search(cap))
                        norm     = _tbl_number_norm(cap)

                        if is_cont:
                            if norm in base_map:
                                base_map[norm].rows.extend(tbl.rows)
                                ids_to_remove.add(tbl.id)
                        else:
                            base_map[norm] = tbl

                    if not ids_to_remove:
                        continue

                    # Remove merged table objects from clause.tables[]
                    clause.tables = [t for t in clause.tables
                                     if t.id not in ids_to_remove]

                    # Remove corresponding inline {type:"table"} content items
                    clause.content = [
                        item for item in clause.content
                        if not (item.type == "table"
                                and item.table_id in ids_to_remove)
                    ]

                    # ── Cross-page rowspan carry (sandwich detection) ─────────
                    # After merging page fragments, some rows may have an empty
                    # load column because a rowspan from the previous fragment
                    # was truncated at the page boundary by Datalab.
                    #
                    # We use "sandwich" detection: if a row's load is empty AND
                    # the immediately preceding AND following non-empty load
                    # values are IDENTICAL, the row is a rowspan continuation
                    # and should carry that value.
                    #
                    # This is intentionally conservative — it only fills cells
                    # where surrounding evidence is unambiguous, avoiding false
                    # fills on genuine category-header rows (which are correctly
                    # empty and sit between rows with DIFFERENT load values).
                    #
                    # Only applied to 2-column use/load tables to avoid
                    # unintended effects on other table structures.
                    for tbl in clause.tables:
                        if len(tbl.headers) != 2:
                            continue

                        # Build look-ahead map: index -> next non-empty load
                        next_val: dict = {}
                        last = ""
                        for idx in range(len(tbl.rows) - 1, -1, -1):
                            v = tbl.rows[idx][1].strip() if len(tbl.rows[idx]) > 1 else ""
                            if v:
                                last = v
                            next_val[idx] = last

                        for idx, row in enumerate(tbl.rows):
                            if len(row) < 2:
                                continue
                            if row[1].strip() or not row[0].strip():
                                continue
                            # Previous non-empty load
                            prev = ""
                            for j in range(idx - 1, -1, -1):
                                pv = tbl.rows[j][1].strip() if len(tbl.rows[j]) > 1 else ""
                                if pv:
                                    prev = pv
                                    break
                            nxt = next_val.get(idx, "")
                            # Sandwich: prev == next -> unambiguous rowspan carry
                            if prev and nxt and prev == nxt:
                                tbl.rows[idx][1] = prev

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
        # FIX (Bug 3): Use a dedicated counter for auto-clauses so that
        # _chapter_counter (incremented by _parse_part_heading for each h1
        # heading) does not interfere with CL-AUTO-N numbering.
        self._auto_clause_counter += 1
        return f"CL-AUTO-{self._auto_clause_counter}"

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