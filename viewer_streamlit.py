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
      - Chapters  (CH-)
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
.block-container { padding-top: 1rem; padding-bottom: 1rem; }

.clause-header {
    background: #f0f4ff;
    border-left: 4px solid #3b5bdb;
    padding: 10px 14px;
    border-radius: 0 6px 6px 0;
    margin-bottom: 12px;
}
.clause-id    { font-family: monospace; color: #3b5bdb; font-size: 0.85rem; }
.clause-title { font-size: 1.1rem; font-weight: 700; color: #1a1a2e; margin: 2px 0 0 0; }

.where-block {
    background: #f8f9fa;
    border-left: 3px solid #adb5bd;
    padding: 8px 14px;
    margin: 4px 0 4px 20px;
    font-size: 0.9rem;
    color: #495057;
}

.subclause-row {
    display: flex; gap: 12px; padding: 4px 0;
    font-size: 0.92rem;
}
.sc-marker {
    font-family: monospace; font-weight: 700;
    color: #3b5bdb; min-width: 36px; padding-top: 2px;
}

.figure-container {
    border: 1px solid #dee2e6;
    border-radius: 8px;
    padding: 12px;
    margin: 12px 0;
    background: #f8f9fa;
}
.figure-caption {
    text-align: center;
    font-weight: 600;
    color: #495057;
    font-size: 0.9rem;
    margin-top: 8px;
}
.figure-alttext {
    font-size: 0.78rem;
    color: #868e96;
    font-style: italic;
    margin-top: 4px;
    text-align: center;
}

.flag-indicator {
    background: #fff3cd;
    border-left: 3px solid #f59e0b;
    padding: 6px 10px;
    border-radius: 0 4px 4px 0;
    font-size: 0.8rem;
    color: #78350f;
    margin-bottom: 8px;
}

.ref-resolved {
    display: inline-block;
    background: #eff6ff; color: #1d4ed8;
    font-size: 0.8rem; font-family: monospace;
    padding: 1px 6px; border-radius: 3px;
    border: 1px solid #bfdbfe; margin: 1px;
    cursor: pointer;
}
.ref-unresolved {
    display: inline-block;
    background: #f9fafb; color: #9ca3af;
    font-size: 0.8rem; font-family: monospace;
    padding: 1px 6px; border-radius: 3px;
    border: 1px solid #e5e7eb; margin: 1px;
}
/* Note reference badges — appendix links */
.note-resolved {
    display: inline-block;
    background: #f0fdf4; color: #166534;
    font-size: 0.8rem; font-family: monospace;
    padding: 1px 6px; border-radius: 3px;
    border: 1px solid #bbf7d0; margin: 1px;
    cursor: pointer;
}
.note-external {
    display: inline-block;
    background: #fefce8; color: #854d0e;
    font-size: 0.8rem; font-family: monospace;
    padding: 1px 6px; border-radius: 3px;
    border: 1px solid #fde68a; margin: 1px;
    cursor: default;
    font-style: italic;
}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Content item renderers
# ─────────────────────────────────────────────────────────────────────────────

def render_text_item(value: str, clause: dict = None):
    """
    Render a plain text content item.

    Handles four cases:
      1. 'where' / 'where:' lines      -> indented italic block
      2. Variable definition lines      -> st.latex symbol + markdown description
      3. Text containing (See Note A-.) -> split into text + note button(s)
      4. Plain text                     -> st.markdown
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

    # Variable definition lines with LaTeX symbols
    var_def = _re.match(
        r'^([A-Za-z\\][A-Za-z0-9_{}\^\\]+)\s*=\s*(.+)',
        value.strip()
    )
    if var_def and any(c in var_def.group(1) for c in ('_', '^', '{', '\\')):
        symbol = var_def.group(1)
        rest   = var_def.group(2)
        # Check if the rest contains a note ref
        note_match = _re.search(
            r'\(See Note\s+(A-(?:Table\s+)?[\d\.]+(?:\.\(\d+\))?(?:\s+and\s+\(\d+\))*\.?)\)',
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
                # Render text before note + note button
                before = rest[:note_match.start()].strip()
                if before:
                    st.markdown(f"= {before}")
                _render_inline_note_button(
                    note_match.group(0),
                    note_match.group(1),
                    clause
                )
            else:
                st.markdown(f"= {rest}")
        return

    # Check for inline (See Note A-...) pattern in plain text
    RE_NOTE_INLINE = _re.compile(
        r'(\(See Note\s+)(A-(?:Table\s+)?[\d\.]+(?:\.\(\d+\))?(?:\s+and\s+\(\d+\))*\.?)(\))',
        _re.IGNORECASE
    )
    if RE_NOTE_INLINE.search(value) and clause is not None:
        _render_text_with_inline_notes(value, RE_NOTE_INLINE, clause)
        return

    # Plain text fallback
    st.markdown(value)


def _render_inline_note_button(raw: str, note_ref: str, clause: dict):
    """
    Render a single (See Note A-...) as a styled button or badge.
    Looks up the note in clause.note_refs[] to get resolution status.
    """
    note_refs = clause.get("note_refs", [])
    match     = next(
        (n for n in note_refs if n.get("note_ref", "").rstrip('.') == note_ref.rstrip('.')),
        None
    )
    resolved    = match.get("resolved", False) if match else False
    target_ids  = match.get("target_ids", []) if match else []

    if resolved and target_ids:
        # Use first target for navigation (most specific match)
        target = target_ids[0]
        if st.button(
            f"📝 {note_ref}",
            key=f"note_{target}_{id(raw)}",
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
    """Render an equation using st.latex() for proper math rendering."""
    if not latex:
        return
    try:
        st.latex(latex)
    except Exception:
        # Fallback: render as code block if LaTeX is malformed
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


def render_table_item(item: dict, clause: dict):
    """Render a table by looking up the table data from clause.tables[]."""
    table_id = item.get("table_id", "")
    tables   = clause.get("tables", [])
    tbl      = next((t for t in tables if t.get("id") == table_id), None)

    if not tbl:
        st.caption(f"Table {table_id} not found.")
        return

    caption = tbl.get("caption", table_id)
    st.markdown(f"**{caption}**")

    headers = tbl.get("headers", [])
    rows    = tbl.get("rows", [])

    if headers and rows:
        padded = [r + [""] * max(0, len(headers) - len(r)) for r in rows]
        st.dataframe(pd.DataFrame(padded, columns=headers),
                     use_container_width=True, hide_index=True)
    elif rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("Table extracted but no rows found.")


def render_subclause_item(item: dict):
    """Render a sub-clause (numbered list item)."""
    marker = item.get("marker", "")
    value  = item.get("value", "")
    st.markdown(
        f'<div class="subclause-row">'
        f'<span class="sc-marker">{marker}</span>'
        f'<span>{value}</span></div>',
        unsafe_allow_html=True
    )


# ─────────────────────────────────────────────────────────────────────────────
# Reference rendering (clickable)
# ─────────────────────────────────────────────────────────────────────────────

def render_references(references: list):
    """
    Render internal references as clickable buttons.
    Resolved references navigate to the target clause via query_params.
    Unresolved references shown as greyed-out badges.
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
                    key=f"ref_{ref['target_id']}_{i}",
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

def render_note_refs(note_refs: list):
    """
    Render appendix note references at the bottom of a clause.

    Two states:
      resolved=True  -> green clickable button -> navigates to appendix clause
      resolved=False -> amber badge with tooltip -> external note in another PDF
    """
    if not note_refs:
        return

    st.markdown("**Appendix Notes:**")
    cols = st.columns(min(len(note_refs), 4))

    for i, note in enumerate(note_refs):
        col        = cols[i % len(cols)]
        note_ref   = note.get("note_ref", "")
        resolved   = note.get("resolved", False)
        target_ids = note.get("target_ids", [])

        with col:
            if resolved and target_ids:
                target = target_ids[0]
                if st.button(
                    f"📝 {note_ref}",
                    key=f"noteref_{target}_{i}",
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

def render_clause(clause: dict, flags: dict, show_flag_ui: bool = True):
    """
    Render a complete clause including all ordered content items.
    Content is rendered in document order: text, equation, figure, table, etc.
    """
    cid        = clause["id"]
    is_flagged = cid in flags
    pages      = clause.get("page_span", [])

    if len(pages) == 1:
        page_info = f"Page {pages[0]}"
    elif len(pages) > 1:
        page_info = f"Pages {pages[0]}–{pages[-1]}  (spans {len(pages)} pages)"
    else:
        page_info = ""

    # Header
    st.markdown(f"""
    <div class="clause-header">
        <div class="clause-id">{cid} &nbsp;·&nbsp; {page_info}</div>
        <div class="clause-title">{clause.get('number','')} &nbsp; {clause.get('title','')}</div>
    </div>
    """, unsafe_allow_html=True)

    # Flag warning
    if is_flagged:
        flag = flags[cid]
        st.markdown(f"""
        <div class="flag-indicator">
            ⚑ <strong>Flagged:</strong> [{flag['issue_type']}] {flag.get('note','—')}
            &nbsp;·&nbsp; {flag.get('flagged_at','?')[:10]}
        </div>
        """, unsafe_allow_html=True)

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
        render_references(references)

    # ── Appendix note references ──────────────────────────────────────────────
    note_refs = clause.get("note_refs", [])
    if note_refs:
        render_note_refs(note_refs)

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
    doc   = load_document()
    flags = load_flags()

    if doc is None:
        st.title("📋 Building Code Viewer")
        st.error("No structured document found.")
        st.info("Run:  `python main.py your_building_code.pdf`\n\n"
                f"Expected: `{STRUCTURED_DOC_PATH}`")
        return

    id_index    = build_id_index(doc)
    clause_list = build_clause_list(doc)
    chapters    = doc.get("chapters", [])
    stats       = doc.get("_stats", {})

    # Check if we have a navigation target from query params
    nav_target = get_target_clause_id()

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown(f"### 📋 {doc.get('title', 'Building Code')}")
        st.caption(f"{doc.get('source_pdf', '')}  ·  "
                   f"{doc.get('total_pages', '?')} pages")
        st.divider()

        total_sections   = sum(len(ch.get("sections", [])) for ch in chapters)
        total_clauses    = len(clause_list)
        total_figures    = sum(len(cl.get("figures", [])) for cl in clause_list)
        total_equations  = sum(len(cl.get("equations", [])) for cl in clause_list)
        total_tables     = sum(len(cl.get("tables", [])) for cl in clause_list)
        flagged_count    = len(flags)

        c1, c2 = st.columns(2)
        c1.metric("Chapters",  len(chapters))
        c2.metric("Sections",  total_sections)
        c1.metric("Clauses",   total_clauses)
        c2.metric("Tables",    total_tables)
        c1.metric("Figures",   total_figures)
        c2.metric("Equations", total_equations)

        if flagged_count:
            st.warning(f"🚩 {flagged_count} flagged")
        else:
            st.success("No issues flagged")

        if stats:
            rate = stats.get("resolution_rate_pct", 0)
            st.metric("Ref resolution", f"{rate}%",
                      f"{stats.get('resolved_references')}/"
                      f"{stats.get('total_references')} refs")

        st.divider()

        # Clear navigation
        if nav_target and st.button("✕ Clear navigation", use_container_width=True):
            st.query_params.clear()
            st.rerun()

        mode = st.radio(
            "View",
            ["📑 Browse", "🔍 Search", "🚩 Flagged Issues", "📊 Stats & Raw"],
            label_visibility="collapsed"
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # If we have a navigation target, jump to that clause
    # ═══════════════════════════════════════════════════════════════════════════
    if nav_target:
        node = id_index.get(nav_target)
        if node and node.get("_type") == "clause":
            # Direct clause navigation
            st.subheader(f"↗ {nav_target}")
            breadcrumb = (f"Chapter {node.get('_chapter_number','?')} › "
                          f"Section {node.get('_section_number','?')}")
            st.caption(breadcrumb)
            render_clause(node, flags)
            return

        elif node and node.get("_type") == "section":
            # Section navigation — show all its clauses
            st.subheader(f"↗ Section {node.get('number','?')} — {node.get('title','')}")
            st.caption(f"Chapter {node.get('_chapter_number','?')}")
            clauses = node.get("clauses", [])
            if not clauses:
                st.info("No clauses in this section.")
            for cl in clauses:
                with st.expander(
                    f"{cl.get('number','?')} — {cl.get('title','')}",
                    expanded=len(clauses) == 1
                ):
                    render_clause(cl, flags)
            return

        elif node and node.get("_type") in ("figure", "table"):
            # Figure/Table navigation — jump to the parent clause
            # and scroll to the relevant item
            parent_id = node.get("_parent_clause_id", "")
            parent_cl = id_index.get(parent_id)
            item_type = node.get("_type").capitalize()
            item_id   = node.get("id", nav_target)

            if parent_cl:
                st.subheader(f"↗ {item_type} {item_id}")
                cap = node.get("caption", "") or node.get("id", "")
                st.caption(
                    f"{cap}  ·  "
                    f"Chapter {node.get('_chapter_number','?')} › "
                    f"Section {node.get('_section_number','?')}"
                )
                st.info(
                    f"This {item_type.lower()} is inside clause "
                    f"**{parent_cl.get('number','?')}**. "
                    f"It is shown highlighted below."
                )
                render_clause(parent_cl, flags)
            else:
                st.warning(f"{item_type} `{item_id}` found but parent clause is missing.")
            return

        else:
            # Truly not found — clear param and show browse
            st.warning(f"Navigation target `{nav_target}` not found in document.")
            st.query_params.clear()

    # ═══════════════════════════════════════════════════════════════════════════
    # BROWSE
    # ═══════════════════════════════════════════════════════════════════════════
    if mode == "📑 Browse":
        st.title("📑 Document Browser")

        if not chapters:
            st.warning("No chapters found.")
            return

        ch_opts = {f"Chapter {ch['number']} — {ch['title']}": ch
                   for ch in chapters}
        chapter  = ch_opts[st.selectbox("Chapter", list(ch_opts.keys()))]
        sections = chapter.get("sections", [])

        if not sections:
            st.warning("No sections in this chapter.")
            return

        sec_opts = {f"Section {s['number']} — {s['title']}": s
                    for s in sections}
        section = sec_opts[st.selectbox("Section", list(sec_opts.keys()))]
        clauses = section.get("clauses", [])

        if not clauses:
            st.info("No clauses in this section.")
            return

        # Count content items for context
        total_eq  = sum(len(cl.get("equations", [])) for cl in clauses)
        total_fig = sum(len(cl.get("figures", [])) for cl in clauses)
        total_tbl = sum(len(cl.get("tables", [])) for cl in clauses)

        st.markdown(
            f"**{len(clauses)} clause(s)** in section {section['number']}  "
            f"· {total_eq} equations · {total_fig} figures · {total_tbl} tables"
        )
        st.divider()

        if len(clauses) <= 6:
            tabs = st.tabs([cl.get("number") or cl["id"] for cl in clauses])
            for tab, cl in zip(tabs, clauses):
                with tab:
                    render_clause(cl, flags)
        else:
            cl_opts = {f"{cl.get('number','?')} — {cl.get('title','')}": cl
                       for cl in clauses}
            sel_cl = st.selectbox("Clause", list(cl_opts.keys()))
            render_clause(cl_opts[sel_cl], flags)

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
                # Search in number, title, and all text content items
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
                        f"*(Ch {cl['_chapter_number']} › {cl['_section_number']})*"
                        f"  {badge_str}"
                    )
                    with st.expander(label):
                        render_clause(cl, flags)
        else:
            st.info("Type a search term above.")

    # ═══════════════════════════════════════════════════════════════════════════
    # FLAGGED ISSUES
    # ═══════════════════════════════════════════════════════════════════════════
    elif mode == "🚩 Flagged Issues":
        st.title("🚩 Flagged Extraction Issues")

        if not flags:
            st.success("No issues flagged yet.")
            return

        st.markdown(f"**{len(flags)} issue(s) flagged.**")
        st.download_button(
            "⬇ Download flagged_issues.json",
            data=json.dumps(flags, indent=2),
            file_name="flagged_issues.json",
            mime="application/json"
        )
        st.divider()

        by_type = {}
        for cid, flag in flags.items():
            t = flag.get("issue_type", "Other")
            by_type.setdefault(t, []).append((cid, flag))

        for issue_type, items in sorted(by_type.items()):
            st.markdown(f"#### {issue_type} ({len(items)})")
            for cid, flag in items:
                node = id_index.get(cid)
                if node:
                    with st.expander(
                        f"**{node.get('number', cid)}** — {node.get('title', '')}"
                    ):
                        st.markdown(
                            f"**Note:** {flag.get('note','—')}  "
                            f"|  **Flagged:** {flag.get('flagged_at','?')[:10]}"
                        )
                        render_clause(node, flags, show_flag_ui=True)
                else:
                    st.warning(f"`{cid}` not in current document.")

    # ═══════════════════════════════════════════════════════════════════════════
    # STATS & RAW
    # ═══════════════════════════════════════════════════════════════════════════
    elif mode == "📊 Stats & Raw":
        st.title("📊 Extraction Statistics")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Pages",     doc.get("total_pages", "?"))
        c2.metric("Chapters",  len(chapters))
        c3.metric("Sections",  sum(len(ch.get("sections", [])) for ch in chapters))
        c4.metric("Clauses",   len(clause_list))

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Equations", sum(len(cl.get("equations",[])) for cl in clause_list))
        c2.metric("Figures",   sum(len(cl.get("figures",[])) for cl in clause_list))
        c3.metric("Tables",    sum(len(cl.get("tables",[])) for cl in clause_list))
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

            # Note references
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
        st.subheader("Per-Chapter Breakdown")
        rows = []
        for ch in chapters:
            secs = ch.get("sections", [])
            cls  = [cl for s in secs for cl in s.get("clauses", [])]
            rows.append({
                "Chapter":   f"{ch['number']} — {ch['title']}",
                "Sections":  len(secs),
                "Clauses":   len(cls),
                "Equations": sum(len(cl.get("equations",[])) for cl in cls),
                "Figures":   sum(len(cl.get("figures",[])) for cl in cls),
                "Tables":    sum(len(cl.get("tables",[])) for cl in cls),
                "Flagged":   sum(1 for cl in cls if cl["id"] in flags),
            })
        st.dataframe(pd.DataFrame(rows),
                     use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Downloads")
        st.download_button(
            "⬇ Download structured_document.json",
            data=json.dumps(doc, indent=2),
            file_name="structured_document.json",
            mime="application/json"
        )

        raw_path = Path("storage") / \
                   f"raw_{Path(doc.get('source_pdf', '')).stem}.json"
        if raw_path.exists():
            raw_text = raw_path.read_text(encoding="utf-8")
            st.download_button(
                f"⬇ Download {raw_path.name}",
                data=raw_text,
                file_name=raw_path.name,
                mime="application/json"
            )
            with st.expander("Preview raw JSON (first 200 lines)"):
                lines = raw_text.splitlines()
                st.code(
                    "\n".join(lines[:200]) +
                    ("\n..." if len(lines) > 200 else ""),
                    language="json"
                )
        else:
            st.info(f"Raw cache not found at `{raw_path}`.")


if __name__ == "__main__":
    main()