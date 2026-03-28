"""
viewer_streamlit.py
====================
Streamlit viewer for structured building code documents.

Key improvements over previous version:
  - Renders ordered content[] preserving exact document reading sequence
  - Equations rendered with st.latex() immediately after their context text
  - Figures rendered inline as st.image() with caption and alt text
  - Tables rendered as interactive dataframes
  - Internal references are clickable buttons (st.query_params navigation)
  - Sub-clauses displayed as formatted list items
  - QA flagging system for reporting extraction issues

Run with:
    streamlit run viewer_streamlit.py
"""

import json
import os
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime

st.set_page_config(
    page_title="Building Code Viewer",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────
STRUCTURED_DOC_PATH = Path("storage/output/structured_document.json")
FLAGS_PATH          = Path("storage/output/flagged_issues.json")
FIGURES_DIR         = Path("storage/figures")


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data
def load_document():
    if not STRUCTURED_DOC_PATH.exists():
        return None
    with open(STRUCTURED_DOC_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_flags() -> dict:
    if not FLAGS_PATH.exists():
        return {}
    with open(FLAGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_flag(clause_id: str, issue_type: str, note: str):
    flags = load_flags()
    flags[clause_id] = {
        "clause_id":  clause_id,
        "issue_type": issue_type,
        "note":       note,
        "flagged_at": datetime.utcnow().isoformat() + "Z",
    }
    FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FLAGS_PATH, "w", encoding="utf-8") as f:
        json.dump(flags, f, indent=2)


def remove_flag(clause_id: str):
    flags = load_flags()
    flags.pop(clause_id, None)
    with open(FLAGS_PATH, "w", encoding="utf-8") as f:
        json.dump(flags, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Index builders
# ─────────────────────────────────────────────────────────────────────────────
def build_id_index(doc: dict) -> dict:
    """
    Build a flat {id -> node} lookup for all navigable nodes.

    Includes:
      - Parts     (CH-)
      - Sections  (SEC-)
      - Clauses   (CL-)
      - Figures   (FIG-)  -> stored with _parent_clause_id for navigation
      - Tables    (TBL-)  -> stored with _parent_clause_id for navigation

    FIG- and TBL- nodes cannot be navigated to directly — they live inside
    clauses. So we store them with _parent_clause_id so the viewer can
    jump to the clause that contains them and highlight the item.
    """
    index = {}
    for ch in doc.get("chapters", []):
        index[ch["id"]] = {**ch, "_type": "chapter"}
        for sec in ch.get("sections", []):
            index[sec["id"]] = {**sec, "_type": "section",
                                "_chapter_number": ch["number"],
                                "_chapter_title":  ch["title"]}
            for cl in sec.get("clauses", []):
                cl_entry = {**cl, "_type": "clause",
                            "_section_number": sec["number"],
                            "_section_title":  sec["title"],
                            "_chapter_number": ch["number"],
                            "_chapter_title":  ch["title"]}
                index[cl["id"]] = cl_entry

                # Index figures — point back to parent clause
                for fig in cl.get("figures", []):
                    index[fig["id"]] = {
                        **fig,
                        "_type":             "figure",
                        "_parent_clause_id": cl["id"],
                        "_section_number":   sec["number"],
                        "_chapter_number":   ch["number"],
                    }

                # Index tables — point back to parent clause
                for tbl in cl.get("tables", []):
                    index[tbl["id"]] = {
                        **tbl,
                        "_type":             "table",
                        "_parent_clause_id": cl["id"],
                        "_section_number":   sec["number"],
                        "_chapter_number":   ch["number"],
                    }
    return index


def build_clause_list(doc: dict) -> list:
    clauses = []
    for ch in doc.get("chapters", []):
        for sec in ch.get("sections", []):
            for cl in sec.get("clauses", []):
                clauses.append({
                    **cl,
                    "_chapter_number": ch["number"],
                    "_chapter_title":  ch["title"],
                    "_section_number": sec["number"],
                    "_section_title":  sec["title"],
                })
    return clauses


# ─────────────────────────────────────────────────────────────────────────────
# Navigation via query params
# ─────────────────────────────────────────────────────────────────────────────
def navigate_to(clause_id: str):
    """Set query param to navigate to a clause."""
    st.query_params["clause"] = clause_id


def get_target_clause_id() -> str:
    """Read target clause from query params."""
    return st.query_params.get("clause", "")


# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Global ──────────────────────────────────────────────────────── */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
.block-container { padding-top: 0.25rem !important; padding-bottom: 2rem; }

/* ── Math table (inside st.components.v1.html iframes) ───────────── */
.math-table-wrap {
    overflow-x: auto;
    margin: 8px 0 16px 0;
    border: 1px solid #dee2e6;
    border-radius: 6px;
}
.math-table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.9rem;
    color: inherit;
}
.math-table th {
    background: #f1f3f9;
    padding: 8px 12px;
    text-align: left;
    font-weight: 600;
    border-bottom: 2px solid #dee2e6;
    border-right: 1px solid #dee2e6;
    white-space: nowrap;
}
.math-table th:last-child { border-right: none; }
.math-table td {
    padding: 6px 12px;
    border-bottom: 1px solid #dee2e6;
    border-right: 1px solid #dee2e6;
    vertical-align: top;
}
.math-table td:last-child { border-right: none; }
.math-table tr:last-child td { border-bottom: none; }
.math-table tr:nth-child(even) td { background: rgba(0,0,0,0.02); }
.math-table-caption { font-weight: 700; margin-bottom: 6px; font-size: 0.95rem; }

/* ── Legacy clause-header (kept for compatibility) ───────────────── */
.clause-header {
    background: #f0f4ff;
    border-left: 4px solid #3b5bdb;
    padding: 10px 14px;
    border-radius: 0 6px 6px 0;
    margin-bottom: 12px;
}
.clause-id    { font-family: monospace; color: #3b5bdb; font-size: 0.85rem; }
.clause-title { font-size: 1.1rem; font-weight: 700; color: #1a1a2e; margin: 2px 0 0 0; }

/* ── Where block ─────────────────────────────────────────────────── */
.where-block {
    background: #f8f9fa;
    border-left: 3px solid #adb5bd;
    padding: 8px 14px;
    margin: 4px 0 4px 20px;
    font-size: 0.9rem;
    color: #495057;
}

/* ── Sub-clause ──────────────────────────────────────────────────── */
.subclause-row { display: flex; gap: 12px; padding: 4px 0; font-size: 0.92rem; }
.sc-marker {
    font-family: monospace; font-weight: 700;
    color: #3b5bdb; min-width: 36px; padding-top: 2px;
    white-space: nowrap;
}

/* ── Figure ──────────────────────────────────────────────────────── */
.figure-container {
    border: 1px solid #dee2e6;
    border-radius: 8px;
    padding: 12px;
    margin: 12px 0;
    background: #f8f9fa;
}
.figure-caption {
    text-align: center; font-weight: 600;
    color: #495057; font-size: 0.9rem; margin-top: 8px;
}
.figure-alttext {
    font-size: 0.78rem; color: #868e96;
    font-style: italic; margin-top: 4px; text-align: center;
}

/* ── Flag indicator ──────────────────────────────────────────────── */
.flag-indicator {
    background: #fff3cd;
    border-left: 3px solid #f59e0b;
    padding: 6px 10px;
    border-radius: 0 4px 4px 0;
    font-size: 0.8rem; color: #78350f; margin-bottom: 8px;
}

/* ── Standard cross-reference badges ────────────────────────────── */
.ref-resolved {
    display: inline-block;
    background: #eff6ff; color: #1d4ed8;
    font-size: 0.8rem; font-family: monospace;
    padding: 1px 6px; border-radius: 3px;
    border: 1px solid #bfdbfe; margin: 1px; cursor: pointer;
}
.ref-unresolved {
    display: inline-block;
    background: #f9fafb; color: #9ca3af;
    font-size: 0.8rem; font-family: monospace;
    padding: 1px 6px; border-radius: 3px;
    border: 1px solid #e5e7eb; margin: 1px;
}

/* ── Appendix note badges ────────────────────────────────────────── */
.note-resolved {
    display: inline-block;
    background: #f0fdf4; color: #166534;
    font-size: 0.8rem; font-family: monospace;
    padding: 1px 6px; border-radius: 3px;
    border: 1px solid #bbf7d0; margin: 1px; cursor: pointer;
}
.note-external {
    display: inline-block;
    background: #fefce8; color: #854d0e;
    font-size: 0.8rem; font-family: monospace;
    padding: 1px 6px; border-radius: 3px;
    border: 1px solid #fde68a; margin: 1px;
    cursor: default; font-style: italic;
}

/* ── Hierarchy label badges ──────────────────────────────────────── */
.hierarchy-badge {
    display: inline-block;
    font-size: 0.65rem; font-weight: 700; letter-spacing: 0.06em;
    text-transform: uppercase; padding: 2px 9px;
    border-radius: 10px; margin-bottom: 5px;
}
.hbadge-section    { background:#dbeafe; color:#1e40af; border:1px solid #bfdbfe; }
.hbadge-subsection { background:#ede9fe; color:#5b21b6; border:1px solid #ddd6fe; }
.hbadge-article    { background:#d1fae5; color:#065f46; border:1px solid #a7f3d0; }
.hbadge-sentence   { background:#fef3c7; color:#92400e; border:1px solid #fde68a; }
.hbadge-default    { background:#f3f4f6; color:#374151; border:1px solid #e5e7eb; }

/* ── Clause heading block ────────────────────────────────────────── */
.clause-heading-block { padding: 4px 0 6px 0; }
.clause-num-title { font-size: 1.02rem; font-weight: 700; color: #1a1a2e; line-height: 1.4; }
.clause-id-tag { font-family: monospace; color: #9ca3af; font-size: 0.72rem; margin-bottom: 2px; }

/* ── Note pill buttons (blue rounded pills beside clause heading) ── */
/* Targets Streamlit buttons in the narrow pill columns (non-first column) */
div[data-testid="stHorizontalBlock"] div[data-testid="column"]:not(:first-child) button {
    background-color: #eff6ff !important;
    color: #1d4ed8 !important;
    border: 1px solid #bfdbfe !important;
    border-radius: 20px !important;
    padding: 3px 10px !important;
    font-size: 0.73rem !important;
    font-weight: 500 !important;
    min-height: 28px !important;
    line-height: 1.3 !important;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
div[data-testid="stHorizontalBlock"] div[data-testid="column"]:not(:first-child) button:hover {
    background-color: #dbeafe !important;
    border-color: #93c5fd !important;
}

/* ── Document top header bar ─────────────────────────────────────── */
.doc-top-bar {
    background: linear-gradient(135deg, #1e3a5f 0%, #2d4e7e 100%);
    padding: 14px 20px;
    margin: -0.5rem -1rem 1.5rem -1rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-radius: 0 0 8px 8px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.18);
}
.doc-title { font-weight: 800; font-size: 1.05rem; color: #ffffff; letter-spacing: -0.01em; }
.doc-subtitle { font-size: 0.76rem; color: #93c5fd; margin-top: 3px; }
.doc-nav-hint { font-size: 0.72rem; color: #a5b4fc; font-style: italic; }

/* ── Part card ────────────────────────────────────────────────────── */
.part-card {
    background: linear-gradient(135deg, #f0f4ff 0%, #eaefff 100%);
    border: 1px solid #c7d2fe;
    border-left: 6px solid #3b5bdb;
    border-radius: 6px;
    padding: 14px 20px;
    margin: 28px 0 16px 0;
    box-shadow: 0 1px 4px rgba(59,91,219,0.08);
}
.part-card-badge {
    font-size: 0.67rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.09em; color: #4f46e5; margin-bottom: 5px;
}
.part-card-title { font-size: 1.3rem; font-weight: 800; color: #1e3a5f; line-height: 1.3; }

/* ── Section header ──────────────────────────────────────────────── */
.section-header {
    font-size: 1.05rem;
    font-weight: 700;
    color: #1e3a5f;
    padding: 9px 14px;
    margin: 20px 0 12px 0;
    background: #f8faff;
    border: 1px solid #e0e7ff;
    border-left: 4px solid #3b5bdb;
    border-radius: 0 6px 6px 0;
    line-height: 1.4;
}
.sec-badge {
    display: inline-block;
    font-size: 0.62rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.07em; color: #4f46e5;
    margin-right: 6px; vertical-align: middle;
}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Content item renderers
# ─────────────────────────────────────────────────────────────────────────────

def _recover_vardef_subscripts(text: str) -> str:
    """
    Recover subscript notation lost when the parser stripped <sub> tags.

    Patterns like "C w applicable" or "where w s and l s are" arise when the
    source HTML had "C<sub>w</sub> applicable" and strip_html converted the
    <sub> tag to a space.  This function converts isolated single-letter
    variables followed by a short subscript (e.g. "C w", "w s", "l s") back
    to inline-math "$C_w$", "$w_s$", "$l_s$" for Streamlit KaTeX rendering.

    Safety guards:
      - (?<!\\w)  — the variable letter must NOT be preceded by another word
                    char (prevents matching letters in the middle of words).
      - (?!\\w)   — the subscript string must NOT be followed by another word
                    char (prevents consuming the start of a longer word like
                    "shorter" from "shorter").
      - Only applied outside existing $...$ regions via _split_math_segments.
    """
    import re as _re
    if not text:
        return text
    _SUB_RE = _re.compile(r'(?<!\w)([A-Za-z])\s([a-z]{1,5})(?!\w)')
    out = []
    for segment, is_math in _split_math_segments(text):
        if is_math:
            out.append(segment)
        else:
            out.append(_SUB_RE.sub(lambda m: f'${m.group(1)}_{{{m.group(2)}}}$', segment))
    return ''.join(out)


def render_text_item(value: str, clause: dict = None):
    """
    Render a plain text content item.

    Handles five cases:
      1. 'where' / 'where:' lines      -> indented italic block
      2. Variable definition lines      -> st.latex symbol + markdown description
         e.g. 'I_s = importance factor...' or '$I_s$ = importance factor...'
      3. Text containing (See Note A-.) -> split into text + note button(s)
      4. Text with $...$ inline math    -> st.markdown (renders via KaTeX)
      5. Plain text                     -> st.markdown

    After Enhancement 1 rewrite, text items from inline-math blocks contain
    $...$ notation (e.g. 'The load, $S$, due to snow...') which Streamlit's
    st.markdown() renders as proper inline math symbols via KaTeX.
    Variable definition lines may also start with $symbol$ instead of raw
    LaTeX commands, both are handled.
    """
    if not value:
        return

    import re as _re

    # "where" lines
    if value.strip().lower() in ("where", "where:"):
        st.markdown(
            f'<div class="where-block"><em>{value}</em></div>',
            unsafe_allow_html=True
        )
        return

    # Variable definition lines — two patterns:
    # Old (from sub_clauses): "I_s = ..." or "\phi = ..."  (raw LaTeX commands)
    # New (from inline math): "$I_s$ = ..." or "$C_b$ = ..."  (dollar notation)
    stripped = value.strip()

    # Pre-process space-subscript variable definition symbols:
    # "C ws = ..." → "C_{ws} = ..." so var_def can detect and render them.
    # Handles cases where the parser stripped <sub> tags to spaces, e.g.
    # C_ws → "C ws", l_cs → "l cs", w_s → "w s".
    _SPACE_SUB_DEF_RE = _re.compile(r'^([A-Za-z])\s([a-z0-9]{1,5})\s*=\s*', _re.DOTALL)
    _ssub = _SPACE_SUB_DEF_RE.match(stripped)
    if _ssub:
        stripped = f'{_ssub.group(1)}_{{{_ssub.group(2)}}} = {stripped[_ssub.end():]}'

    # Pattern 1: starts with $symbol$ = ...
    dollar_def = _re.match(r'^(\$[^$]+\$)\s*=\s*(.+)', stripped)
    if dollar_def:
        symbol_md = dollar_def.group(1)   # e.g. '$I_s$'
        rest      = dollar_def.group(2)
        note_match = _re.search(
            r'\(See Note\s+(A-(?:Table\s+)?\d+(?:\.\d+)*(?:\.\(\d+\))?(?:\s+and\s+\(\d+\))*\.?)\)',
            rest, _re.IGNORECASE
        )
        col1, col2 = st.columns([1, 6])
        with col1:
            st.markdown(symbol_md)
        with col2:
            if note_match and clause:
                before = rest[:note_match.start()].strip()
                if before:
                    st.markdown(f"= {_recover_vardef_subscripts(before)}")
                _render_inline_note_button(note_match.group(0), note_match.group(1), clause)
            else:
                st.markdown(f"= {_recover_vardef_subscripts(rest)}")
        return

    # Pattern 2: starts with raw LaTeX symbol = ... (legacy sub-clause values)
    var_def = _re.match(
        r'^([A-Za-z\\][A-Za-z0-9_{}\^\\]+)\s*=\s*(.+)',
        stripped
    )
    if var_def and any(c in var_def.group(1) for c in ('_', '^', '{', '\\')):
        symbol = var_def.group(1)
        rest   = var_def.group(2)
        note_match = _re.search(
            r'\(See Note\s+(A-(?:Table\s+)?\d+(?:\.\d+)*(?:\.\(\d+\))?(?:\s+and\s+\(\d+\))*\.?)\)',
            rest, _re.IGNORECASE
        )
        col1, col2 = st.columns([1, 6])
        with col1:
            try:
                st.latex(symbol)
            except Exception:
                st.markdown(f"`{symbol}`")
        with col2:
            if note_match and clause:
                before = rest[:note_match.start()].strip()
                if before:
                    st.markdown(f"= {_recover_vardef_subscripts(before)}")
                _render_inline_note_button(note_match.group(0), note_match.group(1), clause)
            else:
                st.markdown(f"= {_recover_vardef_subscripts(rest)}")
        return

    # Check for inline (See Note A-...) pattern in plain text
    RE_NOTE_INLINE = _re.compile(
        r'(\(See Note\s+)(A-(?:Table\s+)?\d+(?:\.\d+)*(?:\.\(\d+\))?(?:\s+and\s+\(\d+\))*\.?)(\))',
        _re.IGNORECASE
    )
    if RE_NOTE_INLINE.search(value) and clause is not None:
        _render_text_with_inline_notes(value, RE_NOTE_INLINE, clause)
        return

    # Detect multi-character roman numeral sub-clause markers at start of text:
    # "ii) ...", "iii) ...", "iv) ..." — the parser's RE_SUBCLAUSE only handles
    # single-letter markers ([a-z]\)), so multi-char roman numeral + ')' items
    # come through as plain text items and must be caught here.
    # This MUST run before RE_EMBEDDED_SC / RE_GLUED_SC to prevent those patterns
    # from incorrectly splitting "ii)" into "i" (prefix) + "i)" (marker).
    RE_ROMAN_SC = _re.compile(
        r'^([ivxlcdm]{2,})\)\s+(.+)$',
        _re.IGNORECASE | _re.DOTALL
    )
    roman_m = RE_ROMAN_SC.match(stripped)
    if roman_m:
        render_subclause_item({
            "marker": roman_m.group(1) + ")",
            "value":  roman_m.group(2).strip(),
        })
        return

    # Detect embedded sub-clause: happens when the parser merges a list-intro
    # sentence and the first sub-clause item into one text block.
    #
    # Pattern 1 — colon separator (e.g. "...act simultaneously:a) 0.9 kN..."):
    #   Greedy (.*:) captures up to the LAST colon (the list-intro colon).
    #
    # Pattern 2 — glued to word, no separator (e.g. "...conformance witha) CSA..."):
    #   Marker is directly concatenated to the preceding word with no space or
    #   punctuation — a parsing artifact.  (.*\w) ends on a word char and
    #   ([a-z]\)) must be the very next chars.  Lowercase-only marker prevents
    #   false positives on table-ref suffixes like "Table 4.1.2A)".
    #   SAFETY: skip when text contains '$' math — the a) could be inside \gamma).
    RE_EMBEDDED_SC = _re.compile(
        r'^(.*:)\s*(\([a-z]\)|[a-z]\))\s+(.+)$',
        _re.IGNORECASE | _re.DOTALL
    )
    RE_GLUED_SC = _re.compile(
        r'^(.*\w)([a-z]\))\s+(.+)$',   # no IGNORECASE — lowercase markers only
        _re.DOTALL
    )
    sc_m = RE_EMBEDDED_SC.match(stripped) or (
        RE_GLUED_SC.match(stripped) if '$' not in stripped else None
    )
    if sc_m:
        prefix_txt = sc_m.group(1).strip()
        sc_marker  = sc_m.group(2)
        sc_value   = sc_m.group(3).strip()
        if prefix_txt:
            st.markdown(prefix_txt)
        render_subclause_item({"marker": sc_marker, "value": sc_value})
        return

    # Fix "m 2" / "m 3" → "$m^2$" / "$m^3$" (stripped HTML <sup> tags).
    # Only matches area/volume unit patterns: m², mm², km², m³.
    # Guard: only applied to non-math text to avoid double-wrapping.
    if '$' not in value and '\\' not in value:
        value = _re.sub(
            r'\b(mm|km|m)\s+([23])\b',
            lambda _m: f'${_m.group(1)}^{{{_m.group(2)}}}$',
            value
        )

    # Plain text / inline math text — st.markdown handles both
    # $...$ notation is rendered as inline KaTeX by Streamlit >= 1.16
    st.markdown(value)


def _render_inline_note_button(raw: str, note_ref: str, clause: dict):
    """
    Render a single (See Note A-...) as a styled button or badge.
    Looks up the note in clause.note_refs[] to get resolution status.

    Key uses clause_id + note_ref + occurrence index to stay unique even
    when the same note_ref appears multiple times within the same clause.
    """
    clause_id = clause.get("id", "unknown")
    note_refs = clause.get("note_refs", [])
    match     = next(
        (n for n in note_refs if n.get("note_ref", "").rstrip('.') == note_ref.rstrip('.')),
        None
    )
    resolved   = match.get("resolved", False) if match else False
    target_ids = match.get("target_ids", []) if match else []

    if resolved and target_ids:
        target = target_ids[0]
        # Sanitise note_ref for use in key: remove spaces and special chars
        safe_ref = note_ref.replace(" ", "_").replace(".", "_").replace("(", "").replace(")", "")
        base_key = f"inline_note_{clause_id}_{safe_ref}"
        # Track per-key occurrence count so duplicate note_refs in the same
        # clause each get a unique suffix (e.g. _0, _1, …)
        if "_inline_note_counts" not in st.session_state:
            st.session_state["_inline_note_counts"] = {}
        count = st.session_state["_inline_note_counts"].get(base_key, 0)
        st.session_state["_inline_note_counts"][base_key] = count + 1
        unique_key = f"{base_key}_{count}"
        if st.button(
            f"📝 {note_ref}",
            key=unique_key,
            help=f"Open appendix note → {target}",
        ):
            navigate_to(target)
            st.rerun()
    else:
        st.markdown(
            f'<span class="note-external" '
            f'title="External appendix note — not in this PDF">📝 {note_ref}</span>',
            unsafe_allow_html=True
        )


def _render_text_with_inline_notes(value: str, pattern, clause: dict):
    """
    Split a text string at (See Note A-...) occurrences and render
    each segment appropriately — text as markdown, notes as buttons.
    """
    import re as _re
    last_end = 0
    segments = []

    for m in pattern.finditer(value):
        # Text before this note
        before = value[last_end:m.start()].strip()
        if before:
            segments.append(("text", before))
        note_ref = m.group(2)
        segments.append(("note", m.group(0), note_ref))
        last_end = m.end()

    # Remaining text after last note
    after = value[last_end:].strip()
    if after:
        segments.append(("text", after))

    # Render all segments
    for seg in segments:
        if seg[0] == "text":
            st.markdown(seg[1])
        elif seg[0] == "note":
            _render_inline_note_button(seg[1], seg[2], clause)


def render_equation_item(latex: str):
    """
    Render a standalone display equation using st.latex().

    Each equation item in content[] corresponds to one <math display="block">
    tag from the source PDF — the parser now emits one item per tag (Fix 1),
    so multi-line piecewise definitions appear as properly separated equations
    rather than one merged unreadable string.

    st.latex() renders centered display math, which is correct for standalone
    equations.  Inline math (variables within sentences) now stays as text
    using $...$ notation and never reaches this function.
    """
    if not latex:
        return
    try:
        st.latex(latex)
    except Exception:
        # Fallback: render as monospace code block if LaTeX is malformed
        st.code(latex, language=None)


def render_figure_item(item: dict):
    """Render a figure with image, caption, and alt text."""
    image_path = item.get("image_path", "")
    caption    = item.get("caption", "")
    alt_text   = item.get("alt_text", "")

    st.markdown('<div class="figure-container">', unsafe_allow_html=True)

    if image_path and Path(image_path).exists():
        st.image(image_path, use_container_width=True)
    else:
        st.info(f"Image not found: {image_path or '(no path)'}")

    if caption:
        st.markdown(f'<div class="figure-caption">{caption}</div>',
                    unsafe_allow_html=True)
    if alt_text:
        # Show truncated alt text — useful for accessibility review
        display_alt = alt_text[:200] + "..." if len(alt_text) > 200 else alt_text
        st.markdown(f'<div class="figure-alttext">{display_alt}</div>',
                    unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Hierarchy helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_hierarchy_label(number: str) -> str:
    """Return display label (Section/Subsection/Article/Sentence) based on number depth."""
    if not number:
        return ""
    depth = len([p for p in number.split('.') if p.strip()])
    return {1: "Part", 2: "Section", 3: "Subsection", 4: "Article", 5: "Sentence"}.get(depth, "")


def get_hierarchy_badge_class(label: str) -> str:
    """Return CSS class for the hierarchy badge colour."""
    return {
        "Section":    "hbadge-section",
        "Subsection": "hbadge-subsection",
        "Article":    "hbadge-article",
        "Sentence":   "hbadge-sentence",
    }.get(label, "hbadge-default")


def find_parent_section(clause_id: str, chapters: list):
    """Return (chapter_dict, section_dict) containing clause_id, or (None, None)."""
    for ch in chapters:
        for sec in ch.get("sections", []):
            for cl in sec.get("clauses", []):
                if cl["id"] == clause_id:
                    return ch, sec
    return None, None


# ─────────────────────────────────────────────────────────────────────────────

def _split_math_segments(s: str):
    """
    Split a string into alternating (text, is_inside_math) segments based on
    $...$ delimiters.  Single-dollar delimiters only (KaTeX inline math).

    Follows standard Markdown/KaTeX delimiter rules to avoid false positives
    on malformed stored values (e.g. comparison operators eaten by the HTML
    stripper producing "$H/D $C_p = -1.0$ for $x..." where "$ for $" would
    otherwise be misidentified as a math block):
      - Opening $: must NOT be immediately followed by whitespace.
      - Closing $: must NOT be immediately preceded by whitespace.

    Returns list of (segment_str, is_math) tuples.
    Already-delimited regions are left exactly as-is so downstream code
    never double-wraps them.
    """
    parts = []
    i = 0
    s = str(s)
    while i < len(s):
        dollar = s.find('$', i)
        if dollar == -1:
            if i < len(s):
                parts.append((s[i:], False))
            break

        # Check valid opening: character after $ must not be whitespace
        next_ch = s[dollar + 1] if dollar + 1 < len(s) else ''
        if next_ch in (' ', '\t', '\n', '\r') or next_ch == '':
            # Not a valid math opener — treat this $ as plain text
            parts.append((s[i:dollar + 1], False))
            i = dollar + 1
            continue

        if dollar > i:
            parts.append((s[i:dollar], False))

        # Find closing $ — must not be preceded by whitespace
        j = dollar + 1
        close = -1
        while j < len(s):
            cand = s.find('$', j)
            if cand == -1:
                break
            if s[cand - 1] not in (' ', '\t', '\n', '\r'):
                close = cand
                break
            j = cand + 1

        if close == -1:
            # Unclosed — treat remainder as plain text
            parts.append((s[dollar:], False))
            break
        parts.append((s[dollar:close + 1], True))   # includes the $...$
        i = close + 1
    return parts


def _esc_html_math(s: str) -> str:
    """
    HTML-escape a string while preserving $...$ math regions intact.

    Standard html.escape() converts '>' to '&gt;' everywhere, which breaks
    KaTeX parsing inside math expressions like '$l_c > (70/C_w^2)$'.
    This function only escapes characters OUTSIDE math delimiters.
    Inside a $...$ region only '&' is escaped (always unsafe in HTML).
    """
    result = []
    i = 0
    s = str(s)
    while i < len(s):
        if s[i] == '$':
            j = s.find('$', i + 1)
            if j == -1:
                result.append('$')
                i += 1
            else:
                # Inside math: only escape & to prevent HTML entity collisions
                math_content = s[i:j + 1].replace('&', '&amp;')
                result.append(math_content)
                i = j + 1
        else:
            c = s[i]
            if c == '&':   result.append('&amp;')
            elif c == '<': result.append('&lt;')
            elif c == '>': result.append('&gt;')
            else:          result.append(c)
            i += 1
    return ''.join(result)


def _wrap_cell_math(cell: str) -> str:
    """
    Wrap LaTeX in a table cell with $...$ so KaTeX renders it.

    If the cell ALREADY contains $...$ delimiters (from the parser's
    inline_math_to_markdown), those regions are left untouched — only the
    plain-text segments between them are scanned for raw LaTeX tokens.
    This prevents double-wrapping like '$$I_s$$' (display math) when the
    input is 'Importance Factor, $I_s$ / ULS'.

    For cells with NO existing $...$ (raw LaTeX from strip_html):
      Strategy 1 — whole-cell wrap for display-style commands (\frac etc.)
      Strategy 2 — token-by-token greedy merge for simple tokens.
    """
    import re as _re
    s = str(cell)
    if not s.strip() or ('\\' not in s and '_' not in s and '^' not in s):
        return s

    D = chr(36)

    DISPLAY_CMDS = _re.compile(
        r'\\(?:frac|sum|int|sqrt|left|right|begin|end|'
        r'overline|underline|hat|tilde|vec|bar|dot)\b'
    )
    COMBINED_RE = _re.compile(
        r'(?:\([^)]*\\[A-Za-z][^)]*\)'
        r'|[A-Za-z0-9_]+\^\{[^}]+\}'
        r'|\d+\^\\[A-Za-z]+'
        r'|\\[A-Za-z]+(?:\{[^}]*\})?'
        r'|[A-Za-z]\^\\[A-Za-z]+'
        r'|[A-Za-z][A-Za-z0-9]*_\{[^}]+\}'
        r'|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9]+(?:\^[0-9]+)?)'
    )
    OP_ONLY = _re.compile(r'^[\s\*/+\-\^=,\.\d\[\]()]+$')

    def _wrap_raw(raw: str) -> str:
        """Apply token-wrapping to a segment that has no existing $...$."""
        if not raw.strip() or ('\\' not in raw and '_' not in raw and '^' not in raw):
            return raw
        if DISPLAY_CMDS.search(raw):
            return D + raw + D
        segs, last = [], 0
        for m in COMBINED_RE.finditer(raw):
            if m.start() > last:
                segs.append(('text', raw[last:m.start()]))
            segs.append(('math', m.group(0)))
            last = m.end()
        if last < len(raw):
            segs.append(('text', raw[last:]))
        merged, i = [], 0
        while i < len(segs):
            seg = segs[i]
            if seg[0] == 'math':
                chain = seg[1]; j = i + 1
                while (j + 1 < len(segs)
                       and segs[j][0] == 'text'
                       and segs[j+1][0] == 'math'
                       and OP_ONLY.match(segs[j][1])):
                    chain += segs[j][1] + segs[j+1][1]
                    j += 2
                merged.append(('math', chain)); i = j
            else:
                merged.append(seg); i += 1
        return ''.join(D + v + D if t == 'math' else v for t, v in merged)

    # If there are no $ signs, apply wrapping to the whole string.
    if D not in s:
        return _wrap_raw(s)

    # String already has $...$ regions — only wrap the plain-text segments
    # between them, leave math regions exactly as-is.
    out = []
    for segment, is_math in _split_math_segments(s):
        if is_math:
            out.append(segment)          # already $...$, leave untouched
        else:
            out.append(_wrap_raw(segment))
    return ''.join(out)


def _fix_cell_subscripts(cell: str) -> str:
    """
    Pre-process table cell text to recover subscript notation lost when the
    parser stripped <sub> tags to spaces.

    Pattern: a single letter followed by a space and 2-5 uppercase letters
    (e.g. "L XC" → "L_{XC}").  Only triggered for uppercase multi-letter
    subscripts — this safely avoids footnote references like "D (2)" (paren,
    not uppercase letter) and single-letter variables like "C b" (b is only
    1 char — handled separately by _wrap_cell_math's COMBINED_RE).
    """
    import re as _re
    # Convert "L XC" → "L_{XC}" (uppercase-only subscripts, 2-5 chars)
    # Negative lookbehind ensures we don't double-convert "_"-prefixed tokens.
    cell = _re.sub(r'(?<![_^{\\])([A-Za-z])\s([A-Z]{2,5})\b', r'\1_{\2}', cell)
    return cell


def _render_cell_content(cell: str) -> str:
    """
    Render a table cell value as HTML, handling inline bullet characters (•).

    Cells from the parser that contain bullet-point lists store them as
    inline '•' characters, e.g.:
      "Ground profile contains • item one , • item two , • item three"

    This function splits on '•', wraps each segment with math-aware
    formatting, and joins them with '<br>' so each bullet appears on its
    own line — matching the PDF layout.
    """
    cell = _fix_cell_subscripts(cell)
    if '\u2022' not in cell:           # no bullet character → fast path
        return _wrap_cell_math(_esc_html_math(cell))

    parts = cell.split('\u2022')
    intro = parts[0].rstrip(' ,')
    bullets = [p.strip().rstrip(' ,') for p in parts[1:] if p.strip()]

    lines = []
    if intro:
        lines.append(_wrap_cell_math(_esc_html_math(intro)))
    for b in bullets:
        lines.append('&#x2022;&nbsp;' + _wrap_cell_math(_esc_html_math(b)))
    return '<br>'.join(lines)


def _build_tbody_with_rowspan(rows: list, n_cols: int) -> str:
    """
    Build <tbody> HTML applying visual rowspan to consecutive identical values
    in the first two columns, mirroring the PDF's use of rowspan for grouped
    rows (e.g. 'Deflection for materials not subject to creep' spanning 3 rows).
    Only merges when 2+ consecutive rows share the same non-empty value.
    """
    if not rows:
        return '<tbody></tbody>'

    # Build span maps for col 0 and col 1
    span_map = [{} for _ in range(min(2, n_cols))]
    for col in range(min(2, n_cols)):
        i = 0
        while i < len(rows):
            val = rows[i][col] if col < len(rows[i]) else ''
            span = 1
            while (i + span < len(rows) and span < 30
                   and (rows[i+span][col] if col < len(rows[i+span]) else '') == val
                   and val.strip()):
                span += 1
            if span > 1:
                span_map[col][i] = span
                for k in range(1, span):
                    span_map[col][i+k] = 0   # 0 = skip (merged into above)
            else:
                span_map[col][i] = 1
            i += span

    html_rows = []
    for ri, row in enumerate(rows):
        padded = list(row) + [''] * max(0, n_cols - len(row))
        cells = []
        for ci, cell in enumerate(padded[:n_cols]):
            content = _render_cell_content(cell)
            if ci < len(span_map):
                sv = span_map[ci].get(ri, 1)
                if sv == 0:
                    continue   # merged into row above
                if sv > 1:
                    cells.append(
                        f'<td rowspan="{sv}" style="vertical-align:middle">'
                        f'{content}</td>'
                    )
                    continue
            cells.append(f'<td>{content}</td>')
        html_rows.append(f'<tr>{"".join(cells)}</tr>')

    return f'<tbody>{"".join(html_rows)}</tbody>'


def _build_hierarchical_thead(headers: list) -> tuple:
    """
    Build a multi-row <thead> when header strings use ' / ' as a hierarchy
    separator produced by the parser (e.g.
    'Factors / Arch and Curved Roofs / $C_a$ Downwind Side').

    Algorithm:
    - Split each header by ' / ' → parts list per column.
    - max_depth = deepest column (number of levels).
    - For each level row:
        - Columns whose last part was already emitted at an earlier level are
          skipped (their cell carries a rowspan).
        - Leaf columns (current level == last level for that column) get
          rowspan = max_depth - level so they fill all remaining header rows.
        - Intermediate columns get colspan if consecutive columns share the
          same path prefix at this level.

    Returns (thead_html_str, num_header_rows).
    """
    if not headers:
        return '<thead><tr></tr></thead>', 1

    parts = [h.split(' / ') for h in headers]
    max_depth = max(len(p) for p in parts)
    n_cols = len(headers)

    if max_depth == 1:
        # No hierarchy — original single-row behaviour
        th_cells = ''.join(
            f'<th>{_wrap_cell_math(_esc_html_math(h))}</th>' for h in headers
        )
        return f'<thead><tr>{th_cells}</tr></thead>', 1

    rows_html = []
    for level in range(max_depth):
        cells = []
        col = 0
        while col < n_cols:
            p = parts[col]
            emit_level = len(p) - 1  # index of last part for this column

            # Already covered by a rowspan from an earlier level → skip
            if level > emit_level:
                col += 1
                continue

            label = p[level]
            is_leaf = (level == emit_level)

            # rowspan: leaf fills all remaining header rows
            rowspan = (max_depth - level) if is_leaf else 1

            # colspan: merge consecutive columns sharing the same path prefix
            # at this level — only for intermediate (non-leaf) cells
            colspan = 1
            if not is_leaf:
                j = col + 1
                while j < n_cols:
                    pj = parts[j]
                    if (len(pj) > level
                            and pj[:level + 1] == p[:level + 1]
                            and len(pj) - 1 > level):
                        colspan += 1
                        j += 1
                    else:
                        break
                col = j
            else:
                col += 1

            content = _wrap_cell_math(_esc_html_math(label))
            style_parts = []
            if colspan > 1:
                style_parts.append('text-align:center')
            if rowspan > 1:
                style_parts.append('vertical-align:middle')
            attrs = ''
            if colspan > 1:
                attrs += f' colspan="{colspan}"'
            if rowspan > 1:
                attrs += f' rowspan="{rowspan}"'
            if style_parts:
                attrs += f' style="{"; ".join(style_parts)}"'
            cells.append(f'<th{attrs}>{content}</th>')

        rows_html.append(f'<tr>{"".join(cells)}</tr>')

    return f'<thead>{"".join(rows_html)}</thead>', max_depth


def _html_table(caption: str, headers: list, rows: list) -> str:
    """
    Build a self-contained HTML document for a table with KaTeX math rendering.

    Improvements over previous version:
    - _wrap_cell_math() wraps raw LaTeX in each cell (\\beta, h_p, \\frac{}{})
      so KaTeX renders them as proper math symbols.
    - _build_tbody_with_rowspan() merges consecutive identical cells in the
      first two columns visually (like PDF rowspan), eliminating repeated text.
    - _build_hierarchical_thead() renders multi-level headers (stored as
      'Level1 / Level2 / Leaf') as stacked <thead> rows with colspan/rowspan
      instead of flattening them with a '/' separator.
    - Height estimate accounts for cell content length to avoid iframe clipping.
    """
    n_cols = len(headers)
    thead_html, n_header_rows = _build_hierarchical_thead(headers)
    caption_html = (
        f'<div class="tbl-caption">{_wrap_cell_math(_esc_html_math(caption))}</div>'
        if caption else ''
    )
    tbody = _build_tbody_with_rowspan(rows, n_cols)

    # Estimate iframe height from content.
    # For hierarchical headers use the longest individual level label (not the
    # full ' / '-joined string) so wrapping is estimated per header row.
    if n_header_rows > 1:
        all_labels = [part for h in headers for part in h.split(' / ')]
        max_h_len = max((len(p) for p in all_labels), default=20)
    else:
        max_h_len = max((len(h) for h in headers), default=20) if headers else 20
    # Approximate chars per line given available width and column count
    approx_col_width_chars = max(8, 80 // max(1, n_cols))
    header_lines = max(1, max_h_len // approx_col_width_chars)
    header_h = max(50, header_lines * 24 + 30) * n_header_rows

    max_len = max((len(str(c)) for row in rows for c in row if c), default=10)
    row_h = 56 if max_len > 80 else 40 if max_len > 30 else 30
    est_height = header_h + max(len(rows), 1) * row_h + (40 if caption else 0) + 16

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script defer
        src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
        onload="renderMathInElement(document.body, {{
          delimiters:[
            {{left:'$$',right:'$$',display:true}},
            {{left:'$', right:'$', display:false}}
          ],
          throwOnError:false
        }})">
</script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 14px;
    color: #1a1a1a;
    background: #ffffff;
    padding: 4px 0;
}}
.tbl-caption {{
    font-weight: 700;
    margin-bottom: 8px;
    font-size: 14px;
    color: #212529;
}}
table {{
    border-collapse: collapse;
    width: 100%;
}}
th {{
    background: #2d3142;
    padding: 8px 12px;
    text-align: left;
    font-weight: 600;
    border-bottom: 2px solid #1a1d2e;
    border-right: 1px solid #3d4157;
    color: #ffffff;
    font-size: 13px;
}}
thead tr:last-child th {{ border-bottom: 2px solid #1a1d2e; }}
th:last-child {{ border-right: none; }}
td {{
    padding: 6px 12px;
    border-bottom: 1px solid #dee2e6;
    border-right: 1px solid #dee2e6;
    vertical-align: top;
    color: #212529;
}}
td:last-child  {{ border-right: none; }}
tr:last-child td {{ border-bottom: none; }}
tr:nth-child(even) td {{ background: #f8f9fa; }}
.katex {{ font-size: 1em; }}
</style>
</head>
<body>
{caption_html}
<table>
{thead_html}
{tbody}
</table>
</body>
</html>
""", est_height


def render_table_item(item: dict, clause: dict):
    """
    Render a table as an HTML table with KaTeX math rendering.

    Replaces st.dataframe() which displayed $...$ notation as literal dollar
    signs in column headers.  The HTML table is rendered with
    st.markdown(unsafe_allow_html=True) inside a KaTeX-enabled page,
    so all $l_c C_w^2$, $C_w$, $I_s$ etc. in headers and cells are
    rendered as proper math symbols by the KaTeX auto-render script.

    Enhancement 4 fallback: merges (continued) table fragments at render
    time if the pipeline was run before the Bug 4 parser fix.
    """
    import re as _re
    _CONT_RE = _re.compile(r'\s*\(continued\)', _re.IGNORECASE)

    table_id = item.get("table_id", "")
    tables   = clause.get("tables", [])
    tbl      = next((t for t in tables if t.get("id") == table_id), None)

    if not tbl:
        st.caption(f"Table {table_id} not found.")
        return

    caption = tbl.get("caption", table_id)
    headers = tbl.get("headers", [])
    rows    = list(tbl.get("rows", []))

    # Enhancement 4: viewer-side merge of (continued) fragments
    if not _CONT_RE.search(caption):
        def _tbl_norm(cap: str) -> str:
            base = _CONT_RE.sub('', cap).strip()
            m = _re.match(r'(?:Table\s+)?([\d\.A-Za-z\-]+)', base, _re.IGNORECASE)
            return _re.sub(r'[.\-\s]', '', m.group(1)).lower() if m else base.lower()
        base_norm = _tbl_norm(caption)
        for other in tables:
            if other.get("id") == table_id:
                continue
            if _CONT_RE.search(other.get("caption", "")):
                if _tbl_norm(other.get("caption", "")) == base_norm:
                    rows.extend(other.get("rows", []))

    display_caption = _CONT_RE.sub('', caption).strip().rstrip('.')

    if not headers and not rows:
        st.caption("Table extracted but no rows found.")
        return

    html_doc, height = _html_table(display_caption, headers, rows)
    st.components.v1.html(html_doc, height=height, scrolling=True)


def _value_with_inline_math(value: str) -> str:
    """
    Convert raw LaTeX commands and subscript variable names in sub-clause text
    to $...$ inline math notation for Streamlit's markdown renderer.

    IMPORTANT: if the value already contains $...$ regions (placed by the
    parser's inline_math_to_markdown), those regions are left completely
    untouched.  Only the plain-text segments BETWEEN existing $...$ blocks
    are scanned for undelimited LaTeX tokens.  This prevents double-wrapping
    artefacts like '$$\\phi R,' or 'a\\leq5 m' that arise when the regex
    matches tokens already inside valid $...$ delimiters.
    """
    import re as _re

    if '\\' not in value and '_' not in value:
        return value

    COMBINED_RE = _re.compile(
        r'(?:'
        r'\([^)]*\\[A-Za-z][^)]*\)'                          # (expr with \cmd in parens)
        r'|[A-Za-z0-9_]+\^\{[^}]+\}'                         # word^{arg}
        r'|\d+\^\\[A-Za-z]+'                                  # 30^\circ
        r'|\\[A-Za-z]+(?:\{[^}]*\})?'                        # \cmd or \cmd{arg}
        r'|[A-Za-z]\^\\[A-Za-z]+'                            # x^\something
        r'|[A-Za-z][A-Za-z0-9]*_\{[^}]+\}'                  # l_{cs}, x_{30}
        r'|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9]+(?:\^[0-9]+)?'  # C_b, C_w^2, l_c
        r')'
    )
    OP_ONLY = _re.compile(r'^[\s\*/+\-\^=,\.\d\[\]()]+$')

    def _wrap_raw_text(text: str) -> str:
        """Wrap undelimited LaTeX tokens in a plain-text segment."""
        if not text or ('\\' not in text and '_' not in text):
            return text
        segments, last = [], 0
        for m in COMBINED_RE.finditer(text):
            if m.start() > last:
                segments.append(('text', text[last:m.start()]))
            segments.append(('math', m.group(0)))
            last = m.end()
        if last < len(text):
            segments.append(('text', text[last:]))
        merged, i = [], 0
        while i < len(segments):
            seg = segments[i]
            if seg[0] == 'math':
                chain = seg[1]; j = i + 1
                while (j + 1 < len(segments)
                       and segments[j][0] == 'text'
                       and segments[j+1][0] == 'math'
                       and OP_ONLY.match(segments[j][1])):
                    chain += segments[j][1] + segments[j+1][1]
                    j += 2
                merged.append(('math', chain)); i = j
            else:
                merged.append(seg); i += 1
        return ''.join(f'${v}$' if t == 'math' else v for t, v in merged)

    # If value has no $ signs at all, apply wrapping to the whole string.
    if '$' not in value:
        return _wrap_raw_text(value)

    # Value already has $...$ regions — only wrap the plain-text segments
    # between them, leave math regions exactly as-is.
    out = []
    for segment, is_math in _split_math_segments(value):
        if is_math:
            out.append(segment)          # already $...$, leave untouched
        else:
            out.append(_wrap_raw_text(segment))
    return ''.join(out)


def render_subclause_item(item: dict):
    """
    Render a sub-clause (lettered/numbered list item).

    FIX (Bug 5): Sub-clause values may contain LaTeX command sequences like
    \\alpha, 30^\\circ, (70^\\circ - \\alpha)/40^\\circ that were previously
    rendered as raw escaped strings via HTML injection.  Now we convert those
    sequences to inline $...$ math so Streamlit renders them as proper symbols.

    The marker (e.g. '(a)', 'i.') is always rendered as plain monospace text.
    The value is rendered as st.markdown so both plain text and inline LaTeX
    display correctly in the same line.
    """
    marker = item.get("marker", "")
    value  = item.get("value", "")

    # [1, 24] keeps the marker column narrow (~4% of width) so the gap between
    # the marker and text stays compact regardless of content area width.
    col1, col2 = st.columns([1, 24])
    with col1:
        st.markdown(
            f'<span class="sc-marker">{marker}</span>',
            unsafe_allow_html=True
        )
    with col2:
        md_value = _value_with_inline_math(value)
        st.markdown(md_value)


# ─────────────────────────────────────────────────────────────────────────────
# Reference rendering (clickable)
# ─────────────────────────────────────────────────────────────────────────────

def render_references(references: list, clause_id: str = ""):
    """
    Render internal references as clickable buttons.
    Resolved references navigate to the target clause via query_params.
    Unresolved references shown as greyed-out badges.

    clause_id is included in every button key to guarantee global uniqueness
    when multiple clauses are rendered simultaneously (e.g. in Browse tabs).
    Without it, two clauses that both reference the same target at the same
    list index i would produce identical key='ref_{target_id}_{i}' and
    Streamlit raises StreamlitDuplicateElementKey.
    """
    if not references:
        return

    st.markdown("**Internal References:**")
    cols = st.columns(min(len(references), 4))

    for i, ref in enumerate(references):
        col = cols[i % len(cols)]
        with col:
            if ref.get("resolved"):
                if st.button(
                    f"↗ {ref['text']}",
                    key=f"ref_{clause_id}_{ref['target_id']}_{i}",
                    help=f"Navigate to {ref['target_id']}",
                    use_container_width=True,
                ):
                    navigate_to(ref["target_id"])
                    st.rerun()
            else:
                st.markdown(
                    f'<span class="ref-unresolved">? {ref["text"]}</span>',
                    unsafe_allow_html=True
                )


# ─────────────────────────────────────────────────────────────────────────────
# Note reference renderer
# ─────────────────────────────────────────────────────────────────────────────

def render_note_refs(note_refs: list, id_index: dict = None, clause_id: str = ""):
    """
    Render appendix note references at the bottom of a clause.

    clause_id is included in every button key to guarantee global uniqueness
    when multiple clauses render simultaneously. Without it, two clauses that
    both have a note_ref resolving to the same target at the same list index i
    produce identical keys and Streamlit raises StreamlitDuplicateElementKey.

    Three states:
      resolved=True, single target  -> one green button  "📝 A-4.1.3.2."
      resolved=True, multi target   -> N green buttons   "📝 A-4.1.3.2.(2)" / "📝 A-4.1.3.2.(4)"
      resolved=False                -> amber badge        "📝 A-4.1.1.3." (external PDF)
    """
    if not note_refs:
        return

    st.markdown("**Appendix Notes:**")

    for i, note in enumerate(note_refs):
        note_ref   = note.get("note_ref", "")
        resolved   = note.get("resolved", False)
        target_ids = note.get("target_ids", [])

        if resolved and target_ids:
            if len(target_ids) == 1:
                target = target_ids[0]
                # Use the stored note_ref as label — it is the precise A- identifier
                # from the source text. Do NOT override from the CL-AUTO clause title:
                # multiple note refs can resolve to the same CL-AUTO clause (when one
                # clause hosts several embedded sub-notes), so the clause title's A-
                # identifier may be a different note entirely.
                label = note_ref
                if st.button(
                    f"📝 {label}",
                    key=f"noteref_{clause_id}_{target}_{i}",
                    help=f"Open appendix note → {target}",
                ):
                    navigate_to(target)
                    st.rerun()
            else:
                cols = st.columns(len(target_ids))
                for j, target in enumerate(target_ids):
                    label = f"{note_ref} [{j+1}]"
                    with cols[j]:
                        if st.button(
                            f"📝 {label}",
                            key=f"noteref_{clause_id}_{target}_{i}_{j}",
                            help=f"Open appendix note → {target}",
                            use_container_width=True,
                        ):
                            navigate_to(target)
                            st.rerun()
        else:
            st.markdown(
                f'<span class="note-external" '
                f'title="External appendix note — located in a different PDF">📝 {note_ref}</span>',
                unsafe_allow_html=True
            )


# ─────────────────────────────────────────────────────────────────────────────
# Main clause renderer
# ─────────────────────────────────────────────────────────────────────────────

def render_clause(clause: dict, flags: dict, show_flag_ui: bool = True,
                  id_index: dict = None):
    """
    Render a complete clause including all ordered content items.
    Content is rendered in document order: text, equation, figure, table, etc.

    id_index is the flat {id -> node} lookup built by build_id_index().
    It is passed through to render_note_refs() so multi-target note buttons
    can display the specific appendix section identifier as their label.
    """
    cid        = clause["id"]
    is_flagged = cid in flags
    pages      = clause.get("page_span", [])
    num        = clause.get("number", "")
    title_text = clause.get("title", "")
    note_refs  = clause.get("note_refs", [])

    if len(pages) == 1:
        page_info = f"p. {pages[0]}"
    elif len(pages) > 1:
        page_info = f"pp. {pages[0]}–{pages[-1]}"
    else:
        page_info = ""

    # Flag warning
    if is_flagged:
        flag = flags[cid]
        st.markdown(
            f'<div class="flag-indicator">⚑ <strong>Flagged:</strong> [{flag["issue_type"]}] '
            f'{flag.get("note","—")} &nbsp;·&nbsp; {flag.get("flagged_at","?")[:10]}</div>',
            unsafe_allow_html=True,
        )

    # Hierarchy badge
    hlabel = get_hierarchy_label(num)
    if hlabel:
        badge_cls = get_hierarchy_badge_class(hlabel)
        st.markdown(
            f'<span class="hierarchy-badge {badge_cls}">{hlabel}</span>',
            unsafe_allow_html=True,
        )

    # Heading row: number + title, with see-note pills alongside
    pill_notes = note_refs  # resolved shown as buttons, unresolved as badges
    max_pills  = min(len(pill_notes), 4)

    if pill_notes:
        head_col, *pill_cols = st.columns([5] + [1.7] * max_pills)
    else:
        head_col  = st.container()
        pill_cols = []

    with head_col:
        st.markdown(
            f'<div class="clause-heading-block">'
            f'<div class="clause-id-tag">{cid}&nbsp;&nbsp;{page_info}</div>'
            f'<div class="clause-num-title">{num}&ensp;{title_text}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    for i, nr in enumerate(pill_notes[:4]):
        with pill_cols[i]:
            note_ref   = nr.get("note_ref", "").rstrip(".")
            resolved   = nr.get("resolved", False)
            target_ids = nr.get("target_ids", [])
            short_lbl  = (note_ref[:20] + "…") if len(note_ref) > 20 else note_ref
            safe_key   = (note_ref.replace(" ", "_").replace(".", "_")
                          .replace("(", "").replace(")", ""))
            if resolved and target_ids:
                if st.button(
                    f"📄 {short_lbl}",
                    key=f"pill_{cid}_{safe_key}_{i}",
                    help=f"Appendix note: {note_ref}",
                    use_container_width=True,
                ):
                    navigate_to(target_ids[0])
                    st.rerun()
            else:
                st.markdown(
                    f'<span class="note-external" '
                    f'title="External appendix note — not in this PDF">'
                    f'📄 {short_lbl}</span>',
                    unsafe_allow_html=True,
                )

    # ── Ordered content rendering ─────────────────────────────────────────────
    content = clause.get("content", [])

    if not content:
        st.caption("_(no content extracted)_")
    else:
        for item in content:
            itype = item.get("type", "")

            if itype == "text":
                render_text_item(item.get("value", ""), clause)

            elif itype == "equation":
                render_equation_item(item.get("latex", ""))

            elif itype == "figure":
                render_figure_item(item)

            elif itype == "table":
                render_table_item(item, clause)

            elif itype == "sub_clause":
                render_subclause_item(item)

    # ── Standard references ───────────────────────────────────────────────────
    references = clause.get("references", [])
    if references:
        render_references(references, clause_id=cid)

    # ── QA flag UI ────────────────────────────────────────────────────────────
    if show_flag_ui:
        with st.expander("⚑ Flag extraction issue", expanded=False):
            c1, c2 = st.columns([1, 2])
            with c1:
                issue_type = st.selectbox(
                    "Issue type",
                    ["Missing text", "Wrong hierarchy", "Table error",
                     "Sub-clause split wrong", "Equation wrong",
                     "Figure missing", "Figure wrong position",
                     "Reference not resolved", "Wrong page number", "Other"],
                    key=f"flag_type_{cid}"
                )
            with c2:
                note = st.text_input("Note (optional)", key=f"flag_note_{cid}")

            b1, b2 = st.columns([1, 4])
            with b1:
                if st.button("Flag", key=f"flag_btn_{cid}"):
                    save_flag(cid, issue_type, note)
                    st.success("Flagged.")
            with b2:
                if is_flagged and st.button("Clear flag", key=f"unflag_{cid}"):
                    remove_flag(cid)
                    st.success("Flag removed.")


# ─────────────────────────────────────────────────────────────────────────────
# Main app
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # Reset per-run inline note button key counters.
    st.session_state["_inline_note_counts"] = {}

    doc   = load_document()
    flags = load_flags()

    if doc is None:
        st.title("Building Code Viewer")
        st.error("No structured document found.")
        st.info("Run:  `python main.py your_building_code.pdf`\n\n"
                f"Expected: `{STRUCTURED_DOC_PATH}`")
        return

    id_index    = build_id_index(doc)
    clause_list = build_clause_list(doc)
    chapters    = doc.get("chapters", [])
    stats       = doc.get("_stats", {})

    # selected_section_id controls which section shows clause sub-items in the sidebar
    if "selected_section_id" not in st.session_state:
        if chapters and chapters[0].get("sections"):
            st.session_state["selected_section_id"] = chapters[0]["sections"][0]["id"]
        else:
            st.session_state["selected_section_id"] = None

    # Handle cross-reference navigation via query params
    nav_target = get_target_clause_id()
    if nav_target:
        node = id_index.get(nav_target)
        if node:
            ntype = node.get("_type")
            if ntype == "clause":
                _, sec = find_parent_section(nav_target, chapters)
                if sec:
                    st.session_state["selected_section_id"] = sec["id"]
                st.session_state["scroll_target"] = nav_target
                st.query_params.clear()
                st.rerun()
            elif ntype == "section":
                st.session_state["selected_section_id"] = nav_target
                st.session_state["scroll_target"] = nav_target
                st.query_params.clear()
                st.rerun()
            elif ntype in ("figure", "table"):
                parent = node.get("_parent_clause_id")
                if parent:
                    _, sec = find_parent_section(parent, chapters)
                    if sec:
                        st.session_state["selected_section_id"] = sec["id"]
                    st.session_state["scroll_target"] = parent
                    st.query_params.clear()
                    st.rerun()
        else:
            st.query_params.clear()

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown(f"**{doc.get('title', 'Building Code')}**")
        st.caption(f"{doc.get('source_pdf', '')}  ·  {doc.get('total_pages', '?')} pages")
        st.divider()

        mode = st.radio(
            "View",
            ["📑 Browse", "🔍 Search", "📊 Stats & Raw"],
            label_visibility="collapsed",
        )
        st.divider()

        if mode == "📑 Browse":
            sel_sec = st.session_state.get("selected_section_id")
            for chapter in chapters:
                part_label = f"Part {chapter['number']} — {chapter['title']}"
                with st.expander(part_label, expanded=True):
                    for section in chapter.get("sections", []):
                        sec_num   = section.get("number", "")
                        sec_title = section.get("title", "")
                        depth     = len([p for p in sec_num.split('.') if p.strip()])
                        indent    = "\u00a0\u00a0\u00a0\u00a0" * max(0, depth - 2)
                        is_sel    = (sel_sec == section["id"])

                        if st.button(
                            f"{indent}{sec_num}  {sec_title[:36]}",
                            key=f"tree_sec_{section['id']}",
                            use_container_width=True,
                            type="primary" if is_sel else "secondary",
                        ):
                            st.session_state["selected_section_id"] = section["id"]
                            st.session_state["scroll_target"] = section["id"]
                            st.rerun()

                        # Expand clause sub-items for the selected section
                        if is_sel:
                            for cl in section.get("clauses", []):
                                cl_num = cl.get("number", "")
                                if not cl_num:
                                    continue
                                cl_depth  = len([p for p in cl_num.split('.') if p.strip()])
                                cl_indent = "\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0" * max(0, cl_depth - 2)
                                cl_title  = cl.get("title", "")[:28]
                                if st.button(
                                    f"{cl_indent}· {cl_num}  {cl_title}",
                                    key=f"tree_cl_{cl['id']}",
                                    use_container_width=True,
                                ):
                                    st.session_state["scroll_target"] = cl["id"]
                                    st.rerun()

    # ═══════════════════════════════════════════════════════════════════════════
    # BROWSE — full document in one continuous scroll
    # ═══════════════════════════════════════════════════════════════════════════
    if mode == "📑 Browse":
        # Top document header bar
        doc_title = doc.get("title", "BC Building Code")
        source    = doc.get("source_pdf", "")
        pages     = doc.get("total_pages", "?")
        st.markdown(
            f'<div class="doc-top-bar">'
            f'<div>'
            f'<div class="doc-title">&#128203; {doc_title}</div>'
            f'<div class="doc-subtitle">{source}&nbsp;&nbsp;·&nbsp;&nbsp;{pages} pages</div>'
            f'</div>'
            f'<div class="doc-nav-hint">Use sidebar to navigate &amp; jump to sections</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Render ALL parts → sections → clauses in one scroll
        for chapter in chapters:
            # Anchor for this part
            st.markdown(f'<div id="anchor-{chapter["id"]}"></div>', unsafe_allow_html=True)

            # Part card
            st.markdown(
                f'<div class="part-card">'
                f'<div class="part-card-badge">Part {chapter["number"]}</div>'
                f'<div class="part-card-title">{chapter["title"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            for section in chapter.get("sections", []):
                sec_id    = section["id"]
                sec_num   = section.get("number", "")
                sec_title = section.get("title", "")
                sec_label = get_hierarchy_label(sec_num)

                # Anchor for this section
                st.markdown(f'<div id="anchor-{sec_id}"></div>', unsafe_allow_html=True)

                # Section header
                st.markdown(
                    f'<div class="section-header">'
                    f'<span class="sec-badge">{sec_label}</span>'
                    f'{sec_num}&nbsp;&nbsp;{sec_title}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                for clause in section.get("clauses", []):
                    cid = clause["id"]
                    st.markdown(f'<div id="anchor-{cid}"></div>', unsafe_allow_html=True)
                    render_clause(clause, flags, show_flag_ui=False, id_index=id_index)
                    st.markdown(
                        '<hr style="margin:6px 0 14px 0;border:none;border-top:1px solid #f0f0f0;">',
                        unsafe_allow_html=True,
                    )

        # Inject JS scroll to the requested anchor
        scroll_target = st.session_state.pop("scroll_target", None)
        if scroll_target:
            st.components.v1.html(
                f"""<script>
                setTimeout(function() {{
                    var el = window.parent.document.getElementById('anchor-{scroll_target}');
                    if (el) el.scrollIntoView({{behavior:'smooth', block:'start'}});
                }}, 400);
                </script>""",
                height=0,
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # SEARCH
    # ═══════════════════════════════════════════════════════════════════════════
    elif mode == "🔍 Search":
        st.title("🔍 Search Clauses")
        query = st.text_input(
            "Search term",
            placeholder="e.g. snow drift, 4.1.6.5, fire resistance"
        )

        if query:
            term    = query.lower()
            results = []
            for cl in clause_list:
                content_text = " ".join(
                    item.get("value", "") + item.get("latex", "")
                    for item in cl.get("content", [])
                )
                haystack = (f"{cl.get('number','')} "
                            f"{cl.get('title','')} "
                            f"{content_text}").lower()
                if term in haystack:
                    results.append(cl)

            st.markdown(f"**{len(results)} result(s)** for `{query}`")

            if not results:
                st.info("No matches. Try a different term.")
            else:
                for cl in results[:30]:
                    eq_count  = len(cl.get("equations", []))
                    fig_count = len(cl.get("figures", []))
                    badges    = []
                    if eq_count:
                        badges.append(f"⚡ {eq_count} eq")
                    if fig_count:
                        badges.append(f"🖼 {fig_count} fig")
                    badge_str = "  ".join(badges)

                    label = (
                        f"**{cl.get('number','?')}** — {cl.get('title','')}  "
                        f"*(Part {cl['_chapter_number']} › {cl['_section_number']})*"
                        f"  {badge_str}"
                    )
                    with st.expander(label):
                        render_clause(cl, flags, id_index=id_index)

    # ═══════════════════════════════════════════════════════════════════════════
    # STATS & RAW
    # ═══════════════════════════════════════════════════════════════════════════
    elif mode == "📊 Stats & Raw":
        st.title("📊 Extraction Statistics")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Pages",    doc.get("total_pages", "?"))
        c2.metric("Parts",    len(chapters))
        c3.metric("Sections", sum(len(ch.get("sections", [])) for ch in chapters))
        c4.metric("Clauses",  len(clause_list))

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Equations", sum(len(cl.get("equations", [])) for cl in clause_list))
        c2.metric("Figures",   sum(len(cl.get("figures",   [])) for cl in clause_list))
        c3.metric("Tables",    sum(len(cl.get("tables",    [])) for cl in clause_list))
        c4.metric("🚩 Flagged", len(flags))

        st.divider()

        if stats:
            st.subheader("Reference Resolution")
            total    = stats.get("total_references", 0)
            resolved = stats.get("resolved_references", 0)
            rate     = stats.get("resolution_rate_pct", 0)
            c1, c2, c3 = st.columns(3)
            c1.metric("Found", total)
            c2.metric("Resolved", resolved)
            c3.metric("Rate", f"{rate}%")
            st.progress(int(rate) / 100)

            total_notes    = stats.get("total_note_refs", 0)
            resolved_notes = stats.get("resolved_note_refs", 0)
            note_rate      = stats.get("note_resolution_rate_pct", 0)
            if total_notes > 0:
                st.markdown(
                    f"**Appendix note refs:** {resolved_notes}/{total_notes} "
                    f"in this PDF ({note_rate}%) — "
                    f"{total_notes - resolved_notes} are external (in other PDFs)"
                )

            unresolved = [
                {"Clause": cl.get("number"), "Ref Text": r.get("text"),
                 "Kind": r.get("kind"), "Target": r.get("target_id", "—")}
                for cl in clause_list
                for r in cl.get("references", [])
                if not r.get("resolved")
            ]
            if unresolved:
                st.markdown(f"**{len(unresolved)} unresolved** "
                            "(external standards or appendices):")
                st.dataframe(pd.DataFrame(unresolved),
                             use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Per-Part Breakdown")
        rows = []
        for ch in chapters:
            secs = ch.get("sections", [])
            cls  = [cl for s in secs for cl in s.get("clauses", [])]
            rows.append({
                "Part":      f"{ch['number']} — {ch['title']}",
                "Sections":  len(secs),
                "Clauses":   len(cls),
                "Equations": sum(len(cl.get("equations", [])) for cl in cls),
                "Figures":   sum(len(cl.get("figures",   [])) for cl in cls),
                "Tables":    sum(len(cl.get("tables",    [])) for cl in cls),
                "Flagged":   sum(1 for cl in cls if cl["id"] in flags),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Downloads")
        st.download_button(
            "⬇ Download structured_document.json",
            data=json.dumps(doc, indent=2),
            file_name="structured_document.json",
            mime="application/json",
        )

        raw_path = Path("storage") / f"raw_{Path(doc.get('source_pdf', '')).stem}.json"
        if raw_path.exists():
            raw_text = raw_path.read_text(encoding="utf-8")
            st.download_button(
                f"⬇ Download {raw_path.name}",
                data=raw_text,
                file_name=raw_path.name,
                mime="application/json",
            )
            with st.expander("Preview raw JSON (first 200 lines)"):
                lines = raw_text.splitlines()
                st.code(
                    "\n".join(lines[:200]) + ("\n..." if len(lines) > 200 else ""),
                    language="json",
                )
        else:
            st.info(f"Raw cache not found at `{raw_path}`.")


if __name__ == "__main__":
    main()