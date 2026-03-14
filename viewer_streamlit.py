"""
viewer_streamlit.py
====================
Streamlit-based document viewer for reviewing structured building code output.

Focused on EXTRACTION ACCURACY:
  - Browse the full chapter/section/clause hierarchy
  - See every field the parser extracted (IDs, page spans, sub-clauses, tables, equations, references)
  - Flag any clause where extraction looks wrong (saved to flagged_issues.json)
  - Search across all clauses
  - Compare raw extracted text vs structured output side-by-side
  - View extraction stats and reference resolution rate

Run with:
    streamlit run viewer_streamlit.py

Requirements:
    pip install streamlit
    (The structured_document.json must already exist — run main.py first)
"""

import json
import os
import streamlit as st
from pathlib import Path
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# Page config — must be the very first Streamlit call
# ─────────────────────────────────────────────────────────────────────────────
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
RAW_OUTPUT_PATH     = Path("storage/raw_output.json")
FLAGS_PATH          = Path("storage/output/flagged_issues.json")


# ─────────────────────────────────────────────────────────────────────────────
# Data loading (cached so it doesn't reload on every interaction)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data
def load_document():
    if not STRUCTURED_DOC_PATH.exists():
        return None
    with open(STRUCTURED_DOC_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def load_raw_output():
    if not RAW_OUTPUT_PATH.exists():
        return None
    with open(RAW_OUTPUT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_flags() -> dict:
    """Load existing flagged issues (not cached — needs to reflect saves)."""
    if not FLAGS_PATH.exists():
        return {}
    with open(FLAGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_flag(clause_id: str, issue_type: str, note: str):
    """Save a QA flag for a clause."""
    flags = load_flags()
    flags[clause_id] = {
        "clause_id": clause_id,
        "issue_type": issue_type,
        "note": note,
        "flagged_at": datetime.utcnow().isoformat() + "Z",
    }
    FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FLAGS_PATH, "w", encoding="utf-8") as f:
        json.dump(flags, f, indent=2)


def remove_flag(clause_id: str):
    """Remove a QA flag."""
    flags = load_flags()
    flags.pop(clause_id, None)
    with open(FLAGS_PATH, "w", encoding="utf-8") as f:
        json.dump(flags, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Flatten the document tree into a lookup dict  {id → node}
# ─────────────────────────────────────────────────────────────────────────────
def build_index(doc: dict) -> dict:
    index = {}
    for ch in doc.get("chapters", []):
        index[ch["id"]] = {**ch, "_type": "chapter"}
        for sec in ch.get("sections", []):
            index[sec["id"]] = {**sec, "_type": "section",
                                "_chapter_title": ch["title"],
                                "_chapter_number": ch["number"]}
            for cl in sec.get("clauses", []):
                index[cl["id"]] = {**cl, "_type": "clause",
                                   "_section_id": sec["id"],
                                   "_section_number": sec["number"],
                                   "_section_title": sec["title"],
                                   "_chapter_number": ch["number"],
                                   "_chapter_title": ch["title"]}
    return index


# ─────────────────────────────────────────────────────────────────────────────
# Build a flat list of all clauses for search
# ─────────────────────────────────────────────────────────────────────────────
def build_clause_list(doc: dict) -> list:
    clauses = []
    for ch in doc.get("chapters", []):
        for sec in ch.get("sections", []):
            for cl in sec.get("clauses", []):
                clauses.append({
                    **cl,
                    "_chapter_number": ch["number"],
                    "_chapter_title": ch["title"],
                    "_section_number": sec["number"],
                    "_section_title": sec["title"],
                })
    return clauses


# ─────────────────────────────────────────────────────────────────────────────
# CSS — minimal, clean, focused on readability
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Tighten up Streamlit's default padding */
.block-container { padding-top: 1rem; padding-bottom: 1rem; }

/* Clause header styling */
.clause-header {
    background: #f0f4ff;
    border-left: 4px solid #3b5bdb;
    padding: 10px 14px;
    border-radius: 0 6px 6px 0;
    margin-bottom: 12px;
}
.clause-number { font-family: monospace; color: #3b5bdb; font-size: 0.85rem; }
.clause-title  { font-size: 1.15rem; font-weight: 700; color: #1a1a2e; margin: 2px 0 0 0; }

/* ID badges */
.id-badge {
    display: inline-block;
    background: #e8f0fe;
    color: #1a56db;
    font-family: monospace;
    font-size: 0.75rem;
    padding: 2px 7px;
    border-radius: 4px;
    border: 1px solid #c7d7f9;
    margin-right: 4px;
}
.id-badge.warn { background: #fff3cd; color: #856404; border-color: #ffc107; }
.id-badge.ok   { background: #d1fae5; color: #065f46; border-color: #6ee7b7; }
.id-badge.err  { background: #fee2e2; color: #991b1b; border-color: #fca5a5; }

/* Sub-clause list */
.subclause-row { display: flex; gap: 12px; padding: 4px 0; border-bottom: 1px solid #f0f0f0; }
.sc-marker     { font-family: monospace; font-weight: 700; color: #3b5bdb; min-width: 28px; }

/* Reference links */
.ref-link {
    display: inline-block;
    background: #eff6ff;
    color: #1d4ed8;
    font-size: 0.78rem;
    font-family: monospace;
    padding: 2px 8px;
    border-radius: 4px;
    border: 1px solid #bfdbfe;
    margin: 2px;
}
.ref-link.unresolved { background: #f9fafb; color: #9ca3af; border-color: #e5e7eb; }

/* Equation block */
.eq-block {
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-radius: 6px;
    padding: 8px 14px;
    font-family: monospace;
    font-size: 0.92rem;
    color: #78350f;
    margin: 6px 0;
}

/* Flag indicator */
.flag-indicator {
    background: #fff3cd;
    border-left: 3px solid #f59e0b;
    padding: 6px 10px;
    border-radius: 0 4px 4px 0;
    font-size: 0.8rem;
    color: #78350f;
    margin-bottom: 8px;
}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Render a single clause (all fields)
# ─────────────────────────────────────────────────────────────────────────────
def render_clause(clause: dict, flags: dict, show_flag_ui: bool = True):
    cid = clause["id"]
    is_flagged = cid in flags

    # Header
    page_info = ""
    if clause.get("page_span"):
        pages = clause["page_span"]
        page_info = f"Page {pages[0]}" if len(pages) == 1 else f"Pages {pages[0]}–{pages[-1]} (spans {len(pages)})"

    st.markdown(f"""
    <div class="clause-header">
        <div class="clause-number">{cid} &nbsp;·&nbsp; {page_info}</div>
        <div class="clause-title">{clause.get('number','')} &nbsp; {clause.get('title','')}</div>
    </div>
    """, unsafe_allow_html=True)

    # Flag warning banner
    if is_flagged:
        flag = flags[cid]
        st.markdown(f"""
        <div class="flag-indicator">
            ⚑ <strong>Flagged:</strong> [{flag['issue_type']}] {flag['note']}
            &nbsp;·&nbsp; {flag['flagged_at'][:10]}
        </div>
        """, unsafe_allow_html=True)

    # Main text
    if clause.get("text"):
        st.markdown(clause["text"])
    else:
        st.caption("_(no body text extracted)_")

    # Sub-clauses
    sub_clauses = clause.get("sub_clauses", [])
    if sub_clauses:
        st.markdown("**Sub-clauses:**")
        for sc in sub_clauses:
            st.markdown(f"""
            <div class="subclause-row">
                <span class="sc-marker">{sc.get('marker','')}</span>
                <span>{sc.get('text','')}</span>
            </div>
            """, unsafe_allow_html=True)
        st.markdown("")

    # Equations
    equations = clause.get("equations", [])
    if equations:
        st.markdown("**Equations:**")
        for eq in equations:
            st.markdown(f"""
            <div class="eq-block">
                <span class="id-badge warn">{eq['id']}</span>
                &nbsp; {eq.get('raw_text', '')}
            </div>
            """, unsafe_allow_html=True)

    # Tables
    tables = clause.get("tables", [])
    if tables:
        for table in tables:
            st.markdown(f"**{table.get('caption', table['id'])}**")
            headers = table.get("headers", [])
            rows    = table.get("rows", [])
            if headers and rows:
                import pandas as pd
                df = pd.DataFrame(rows, columns=headers if len(headers) == len(rows[0]) else None)
                st.dataframe(df, use_container_width=True, hide_index=True)
            elif headers:
                st.caption("Table has headers but no rows.")
            else:
                st.caption("Table structure could not be parsed.")

    # References
    references = clause.get("references", [])
    if references:
        st.markdown("**Internal References:**")
        ref_html = ""
        for ref in references:
            css_class = "ref-link" if ref.get("resolved") else "ref-link unresolved"
            icon = "↗" if ref.get("resolved") else "?"
            target = ref.get("target_id", "—")
            ref_html += f'<span class="{css_class}">{icon} {ref["text"]} → {target}</span>'
        st.markdown(ref_html, unsafe_allow_html=True)

    # QA flagging UI
    if show_flag_ui:
        with st.expander("⚑ Flag extraction issue", expanded=False):
            col1, col2 = st.columns([1, 2])
            with col1:
                issue_type = st.selectbox(
                    "Issue type",
                    ["Missing text", "Wrong hierarchy", "Table error",
                     "Reference not resolved", "Sub-clause split wrong",
                     "Equation garbled", "Wrong page number", "Other"],
                    key=f"flag_type_{cid}"
                )
            with col2:
                note = st.text_input("Note (optional)", key=f"flag_note_{cid}")

            c1, c2 = st.columns([1, 4])
            with c1:
                if st.button("🚩 Flag this clause", key=f"flag_btn_{cid}"):
                    save_flag(cid, issue_type, note)
                    st.success("Flagged! Refresh to see it.")
            with c2:
                if is_flagged:
                    if st.button("✓ Clear flag", key=f"unflag_btn_{cid}"):
                        remove_flag(cid)
                        st.success("Flag removed.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────────────────────────────────────
def main():
    doc = load_document()
    flags = load_flags()

    # ── No document yet ───────────────────────────────────────────────────────
    if doc is None:
        st.title("📋 Building Code Viewer")
        st.error("No structured document found.")
        st.info(
            "Run the extraction pipeline first:\n\n"
            "```bash\npython main.py your_building_code.pdf\n```\n\n"
            f"Expected file: `{STRUCTURED_DOC_PATH}`"
        )
        return

    # ── Build indexes ─────────────────────────────────────────────────────────
    id_index     = build_index(doc)
    clause_list  = build_clause_list(doc)
    stats        = doc.get("_stats", {})
    chapters     = doc.get("chapters", [])

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown(f"### 📋 {doc.get('title', 'Building Code')}")
        st.caption(f"{doc.get('source_pdf', '')} · {doc.get('total_pages', '?')} pages")
        st.divider()

        # Stats summary
        total_sections = sum(len(ch.get("sections", [])) for ch in chapters)
        total_clauses  = len(clause_list)
        flagged_count  = len(flags)

        c1, c2 = st.columns(2)
        c1.metric("Chapters",  len(chapters))
        c2.metric("Sections",  total_sections)
        c1.metric("Clauses",   total_clauses)
        c2.metric("🚩 Flagged", flagged_count)

        if stats:
            rate = stats.get("resolution_rate_pct", 0)
            color = "normal" if rate >= 80 else "inverse"
            st.metric(
                "Ref resolution",
                f"{rate}%",
                f"{stats.get('resolved_references')}/{stats.get('total_references')} refs",
                delta_color=color
            )

        st.divider()

        # Navigation mode
        mode = st.radio(
            "View",
            ["📑 Browse", "🔍 Search", "🚩 Flagged Issues", "📊 Stats & Raw"],
            label_visibility="collapsed"
        )

    # ═════════════════════════════════════════════════════════════════════════
    # MODE: BROWSE
    # ═════════════════════════════════════════════════════════════════════════
    if mode == "📑 Browse":
        st.title("📑 Document Browser")

        # Chapter selector
        chapter_options = {f"Chapter {ch['number']} — {ch['title']}": ch for ch in chapters}
        selected_ch_label = st.selectbox("Select Chapter", list(chapter_options.keys()))
        selected_chapter  = chapter_options[selected_ch_label]

        sections = selected_chapter.get("sections", [])
        if not sections:
            st.warning("No sections found in this chapter.")
            return

        # Section selector
        section_options = {f"{sec['number']} — {sec['title']}": sec for sec in sections}
        selected_sec_label = st.selectbox("Select Section", list(section_options.keys()))
        selected_section   = section_options[selected_sec_label]

        clauses = selected_section.get("clauses", [])
        st.markdown(f"**{len(clauses)} clause(s)** in section {selected_section['number']}")
        st.divider()

        if not clauses:
            st.info("No clauses found in this section.")
            return

        # Clause selector (tabs if ≤ 8, otherwise selectbox)
        if len(clauses) <= 8:
            tab_labels = [f"{cl['number']}" for cl in clauses]
            tabs = st.tabs(tab_labels)
            for tab, clause in zip(tabs, clauses):
                with tab:
                    render_clause(clause, flags)
        else:
            clause_options = {f"{cl['number']} — {cl.get('title','')}": cl for cl in clauses}
            selected_cl_label = st.selectbox("Select Clause", list(clause_options.keys()))
            render_clause(clause_options[selected_cl_label], flags)

    # ═════════════════════════════════════════════════════════════════════════
    # MODE: SEARCH
    # ═════════════════════════════════════════════════════════════════════════
    elif mode == "🔍 Search":
        st.title("🔍 Search Clauses")

        query = st.text_input("Search term", placeholder="e.g. dead load, fire resistance, 3.1.2")

        if query:
            term = query.lower()
            results = []
            for cl in clause_list:
                haystack = f"{cl.get('number','')} {cl.get('title','')} {cl.get('text','')}".lower()
                if term in haystack:
                    results.append(cl)

            st.markdown(f"**{len(results)} result(s)** for `{query}`")

            if not results:
                st.info("No clauses matched. Try a different keyword.")
                return

            for cl in results[:30]:
                with st.expander(
                    f"**{cl['number']}** — {cl.get('title','')}  "
                    f"*(Ch {cl['_chapter_number']} › {cl['_section_number']})*"
                ):
                    render_clause(cl, flags)
        else:
            st.info("Type a search term above to find clauses.")

    # ═════════════════════════════════════════════════════════════════════════
    # MODE: FLAGGED ISSUES
    # ═════════════════════════════════════════════════════════════════════════
    elif mode == "🚩 Flagged Issues":
        st.title("🚩 Flagged Extraction Issues")

        if not flags:
            st.success("No issues flagged yet. Use the 'Flag extraction issue' button on any clause.")
            return

        st.markdown(f"**{len(flags)} issue(s) flagged** — review and fix in the parser.")

        # Export button
        flags_json = json.dumps(flags, indent=2)
        st.download_button(
            "⬇ Download flagged_issues.json",
            data=flags_json,
            file_name="flagged_issues.json",
            mime="application/json"
        )

        st.divider()

        # Group by issue type
        by_type = {}
        for cid, flag in flags.items():
            t = flag.get("issue_type", "Other")
            by_type.setdefault(t, []).append((cid, flag))

        for issue_type, items in sorted(by_type.items()):
            st.markdown(f"#### {issue_type} ({len(items)})")
            for cid, flag in items:
                clause = id_index.get(cid)
                if clause:
                    with st.expander(f"**{clause.get('number',cid)}** — {clause.get('title','')}"):
                        st.markdown(f"**Note:** {flag.get('note','—')}")
                        st.markdown(f"**Flagged at:** {flag.get('flagged_at','?')[:10]}")
                        render_clause(clause, flags, show_flag_ui=True)
                else:
                    st.warning(f"Clause `{cid}` not found in document (may have been re-extracted).")

    # ═════════════════════════════════════════════════════════════════════════
    # MODE: STATS & RAW
    # ═════════════════════════════════════════════════════════════════════════
    elif mode == "📊 Stats & Raw":
        st.title("📊 Extraction Statistics")

        # ── Extraction summary ────────────────────────────────────────────────
        st.subheader("Document Summary")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Pages",    doc.get("total_pages", "?"))
        col2.metric("Chapters",       len(chapters))
        col3.metric("Sections",       sum(len(ch.get("sections", [])) for ch in chapters))
        col4.metric("Clauses",        len(clause_list))

        st.divider()

        # ── Reference resolution ──────────────────────────────────────────────
        if stats:
            st.subheader("Reference Resolution")
            total  = stats.get("total_references", 0)
            resolved = stats.get("resolved_references", 0)
            rate   = stats.get("resolution_rate_pct", 0)

            col1, col2, col3 = st.columns(3)
            col1.metric("References Found",    total)
            col2.metric("Resolved",            resolved)
            col3.metric("Resolution Rate",     f"{rate}%")

            st.progress(int(rate) / 100, text=f"{rate}% of internal cross-references resolved")

            # List unresolved references
            unresolved = []
            for cl in clause_list:
                for ref in cl.get("references", []):
                    if not ref.get("resolved"):
                        unresolved.append({
                            "Clause": cl.get("number"),
                            "Reference Text": ref.get("text"),
                            "Target ID": ref.get("target_id", "—"),
                        })
            if unresolved:
                st.markdown(f"**Unresolved references ({len(unresolved)})** — these may point to appendices or external standards:")
                import pandas as pd
                st.dataframe(pd.DataFrame(unresolved), use_container_width=True, hide_index=True)

        st.divider()

        # ── Per-chapter stats ─────────────────────────────────────────────────
        st.subheader("Per-Chapter Breakdown")
        import pandas as pd
        chapter_data = []
        for ch in chapters:
            secs = ch.get("sections", [])
            cls  = [cl for sec in secs for cl in sec.get("clauses", [])]
            tables_count = sum(len(cl.get("tables", [])) for cl in cls)
            eq_count     = sum(len(cl.get("equations", [])) for cl in cls)
            flagged_in_ch = sum(1 for cl in cls if cl["id"] in flags)
            chapter_data.append({
                "Chapter": f"{ch['number']} — {ch['title']}",
                "Sections": len(secs),
                "Clauses":  len(cls),
                "Tables":   tables_count,
                "Equations": eq_count,
                "🚩 Flagged": flagged_in_ch,
            })
        st.dataframe(pd.DataFrame(chapter_data), use_container_width=True, hide_index=True)

        st.divider()

        # ── Raw Datalab output ────────────────────────────────────────────────
        st.subheader("Raw Datalab Output")
        raw = load_raw_output()
        if raw:
            st.caption(f"File: `{RAW_OUTPUT_PATH}`")
            with st.expander("View raw JSON (first 200 lines)"):
                raw_str = json.dumps(raw, indent=2)
                lines   = raw_str.splitlines()
                st.code("\n".join(lines[:200]) + ("\n..." if len(lines) > 200 else ""), language="json")
            st.download_button(
                "⬇ Download raw_output.json",
                data=json.dumps(raw, indent=2),
                file_name="raw_output.json",
                mime="application/json"
            )
        else:
            st.info(f"Raw output not found at `{RAW_OUTPUT_PATH}`. Run the pipeline first.")

        # ── Structured document download ──────────────────────────────────────
        st.divider()
        st.subheader("Structured Document")
        st.download_button(
            "⬇ Download structured_document.json",
            data=json.dumps(doc, indent=2),
            file_name="structured_document.json",
            mime="application/json"
        )


if __name__ == "__main__":
    main()