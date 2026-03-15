"""
viewer_streamlit.py - Streamlit viewer for reviewing structured building code output.

Run with:  streamlit run viewer_streamlit.py
Reads:     storage/output/structured_document.json
"""

import json
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime

st.set_page_config(page_title="Building Code Viewer", page_icon="📋",
                   layout="wide", initial_sidebar_state="expanded")

STRUCTURED_DOC_PATH = Path("storage/output/structured_document.json")
FLAGS_PATH          = Path("storage/output/flagged_issues.json")

@st.cache_data
def load_document():
    if not STRUCTURED_DOC_PATH.exists():
        return None
    with open(STRUCTURED_DOC_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_flags():
    if not FLAGS_PATH.exists():
        return {}
    with open(FLAGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_flag(clause_id, issue_type, note):
    flags = load_flags()
    flags[clause_id] = {"clause_id": clause_id, "issue_type": issue_type,
                        "note": note, "flagged_at": datetime.utcnow().isoformat() + "Z"}
    FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FLAGS_PATH, "w", encoding="utf-8") as f:
        json.dump(flags, f, indent=2)

def remove_flag(clause_id):
    flags = load_flags()
    flags.pop(clause_id, None)
    with open(FLAGS_PATH, "w", encoding="utf-8") as f:
        json.dump(flags, f, indent=2)

def build_id_index(doc):
    index = {}
    for ch in doc.get("chapters", []):
        index[ch["id"]] = {**ch, "_type": "chapter"}
        for sec in ch.get("sections", []):
            index[sec["id"]] = {**sec, "_type": "section",
                                "_chapter_number": ch["number"], "_chapter_title": ch["title"]}
            for cl in sec.get("clauses", []):
                index[cl["id"]] = {**cl, "_type": "clause",
                                   "_section_number": sec["number"], "_section_title": sec["title"],
                                   "_chapter_number": ch["number"], "_chapter_title": ch["title"]}
    return index

def build_clause_list(doc):
    clauses = []
    for ch in doc.get("chapters", []):
        for sec in ch.get("sections", []):
            for cl in sec.get("clauses", []):
                clauses.append({**cl,
                    "_chapter_number": ch["number"], "_chapter_title": ch["title"],
                    "_section_number": sec["number"], "_section_title": sec["title"]})
    return clauses

st.markdown("""<style>
.block-container{padding-top:1rem;padding-bottom:1rem}
.clause-header{background:#f0f4ff;border-left:4px solid #3b5bdb;padding:10px 14px;border-radius:0 6px 6px 0;margin-bottom:12px}
.clause-id{font-family:monospace;color:#3b5bdb;font-size:.85rem}
.clause-title{font-size:1.1rem;font-weight:700;color:#1a1a2e;margin:2px 0 0 0}
.id-badge{display:inline-block;background:#e8f0fe;color:#1a56db;font-family:monospace;font-size:.75rem;padding:2px 7px;border-radius:4px;border:1px solid #c7d7f9;margin-right:4px}
.subclause-row{display:flex;gap:12px;padding:4px 0;border-bottom:1px solid #f0f0f0}
.sc-marker{font-family:monospace;font-weight:700;color:#3b5bdb;min-width:32px}
.ref-link{display:inline-block;background:#eff6ff;color:#1d4ed8;font-size:.78rem;font-family:monospace;padding:2px 8px;border-radius:4px;border:1px solid #bfdbfe;margin:2px}
.ref-link.unresolved{background:#f9fafb;color:#9ca3af;border-color:#e5e7eb}
.eq-block{background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:8px 14px;font-family:monospace;font-size:.92rem;color:#78350f;margin:6px 0}
.flag-indicator{background:#fff3cd;border-left:3px solid #f59e0b;padding:6px 10px;border-radius:0 4px 4px 0;font-size:.8rem;color:#78350f;margin-bottom:8px}
</style>""", unsafe_allow_html=True)

def render_clause(clause, flags, show_flag_ui=True):
    cid        = clause["id"]
    is_flagged = cid in flags
    pages      = clause.get("page_span", [])
    page_info  = (f"Page {pages[0]}" if len(pages)==1
                  else f"Pages {pages[0]}-{pages[-1]} (spans {len(pages)})" if pages else "")

    st.markdown(f"""<div class="clause-header">
        <div class="clause-id">{cid} &nbsp;·&nbsp; {page_info}</div>
        <div class="clause-title">{clause.get("number","")} &nbsp; {clause.get("title","")}</div>
    </div>""", unsafe_allow_html=True)

    if is_flagged:
        flag = flags[cid]
        st.markdown(f"""<div class="flag-indicator">
            Flag: [{flag["issue_type"]}] {flag["note"]} &nbsp;·&nbsp; {flag["flagged_at"][:10]}
        </div>""", unsafe_allow_html=True)

    body = clause.get("text", "").strip()
    if body:
        st.markdown(body)
    else:
        st.caption("_(no body text extracted)_")

    sub_clauses = clause.get("sub_clauses", [])
    if sub_clauses:
        st.markdown("**Sub-clauses:**")
        for sc in sub_clauses:
            st.markdown(f"""<div class="subclause-row">
                <span class="sc-marker">{sc.get("marker","")}</span>
                <span>{sc.get("text","")}</span></div>""", unsafe_allow_html=True)
        st.markdown("")

    for eq in clause.get("equations", []):
        st.markdown(f"""<div class="eq-block">
            <span class="id-badge">{eq["id"]}</span>&nbsp; {eq.get("raw_text","")}</div>""",
            unsafe_allow_html=True)

    for table in clause.get("tables", []):
        st.markdown(f"**{table.get('caption', table['id'])}**")
        headers = table.get("headers", [])
        rows    = table.get("rows", [])
        if headers and rows:
            padded = [r + [""]*(len(headers)-len(r)) for r in rows]
            st.dataframe(pd.DataFrame(padded, columns=headers), use_container_width=True, hide_index=True)
        elif rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("Table extracted but no rows found.")

    references = clause.get("references", [])
    if references:
        st.markdown("**Internal References:**")
        ref_html = "".join(
            f'<span class="{"ref-link" if r.get("resolved") else "ref-link unresolved"}">'
            f'{"\u2197" if r.get("resolved") else "?"} {r["text"]} \u2192 {r.get("target_id","\u2014")}</span>'
            for r in references)
        st.markdown(ref_html, unsafe_allow_html=True)

    if show_flag_ui:
        with st.expander("Flag extraction issue", expanded=False):
            c1, c2 = st.columns([1,2])
            with c1:
                issue_type = st.selectbox("Issue type",
                    ["Missing text","Wrong hierarchy","Table error","Sub-clause split wrong",
                     "Reference not resolved","Equation garbled","Wrong page number","Other"],
                    key=f"flag_type_{cid}")
            with c2:
                note = st.text_input("Note (optional)", key=f"flag_note_{cid}")
            b1, b2 = st.columns([1,4])
            with b1:
                if st.button("Flag", key=f"flag_btn_{cid}"):
                    save_flag(cid, issue_type, note); st.success("Flagged.")
            with b2:
                if is_flagged and st.button("Clear flag", key=f"unflag_{cid}"):
                    remove_flag(cid); st.success("Flag removed.")

def main():
    doc   = load_document()
    flags = load_flags()

    if doc is None:
        st.title("Building Code Viewer")
        st.error("No structured document found.")
        st.info("Run:  python main.py your_building_code.pdf")
        return

    id_index    = build_id_index(doc)
    clause_list = build_clause_list(doc)
    chapters    = doc.get("chapters", [])
    stats       = doc.get("_stats", {})

    with st.sidebar:
        st.markdown(f"### {doc.get('title','Building Code')}")
        st.caption(f"{doc.get('source_pdf','')}  ·  {doc.get('total_pages','?')} pages")
        st.divider()

        total_sections   = sum(len(ch.get("sections",[])) for ch in chapters)
        total_clauses    = len(clause_list)
        total_subclauses = sum(len(cl.get("sub_clauses",[])) for cl in clause_list)
        flagged_count    = len(flags)

        c1, c2 = st.columns(2)
        c1.metric("Chapters",    len(chapters))
        c2.metric("Sections",    total_sections)
        c1.metric("Clauses",     total_clauses)
        c2.metric("Sub-clauses", total_subclauses)
        st.warning(f"Flagged: {flagged_count}") if flagged_count else st.success("No issues flagged")

        if stats:
            rate = stats.get("resolution_rate_pct", 0)
            st.metric("Ref resolution", f"{rate}%",
                      f"{stats.get('resolved_references')}/{stats.get('total_references')} refs")
        st.divider()
        mode = st.radio("View",
            ["Browse", "Search", "Flagged Issues", "Stats & Raw"],
            label_visibility="collapsed")

    if mode == "Browse":
        st.title("Document Browser")
        if not chapters:
            st.warning("No chapters found."); return

        ch_opts = {f"Chapter {ch['number']} - {ch['title']}": ch for ch in chapters}
        chapter = ch_opts[st.selectbox("Chapter", list(ch_opts.keys()))]
        sections = chapter.get("sections", [])
        if not sections:
            st.warning("No sections in this chapter."); return

        sec_opts = {f"{s['number']} - {s['title']}": s for s in sections}
        section  = sec_opts[st.selectbox("Section", list(sec_opts.keys()))]
        clauses  = section.get("clauses", [])
        st.markdown(f"**{len(clauses)} clause(s)** in section {section['number']}")
        st.divider()
        if not clauses:
            st.info("No clauses in this section."); return

        if len(clauses) <= 8:
            for tab, cl in zip(st.tabs([c["number"] or c["id"] for c in clauses]), clauses):
                with tab: render_clause(cl, flags)
        else:
            cl_opts = {f"{c['number']} - {c.get('title','')}": c for c in clauses}
            render_clause(cl_opts[st.selectbox("Clause", list(cl_opts.keys()))], flags)

    elif mode == "Search":
        st.title("Search Clauses")
        query = st.text_input("Search term", placeholder="e.g. heritage, fire separation, 1.1.1.1")
        if query:
            term = query.lower()
            results = [cl for cl in clause_list
                       if term in f"{cl.get('number','')} {cl.get('title','')} {cl.get('text','')} "
                                  f"{chr(32).join(sc.get('text','') for sc in cl.get('sub_clauses',[]))}".lower()]
            st.markdown(f"**{len(results)} result(s)** for `{query}`")
            if not results:
                st.info("No matches. Try a different term.")
            else:
                for cl in results[:30]:
                    with st.expander(f"**{cl['number']}** - {cl.get('title','')}  "
                                     f"*(Ch {cl['_chapter_number']} > {cl['_section_number']})*"):
                        render_clause(cl, flags)
        else:
            st.info("Type a search term above.")

    elif mode == "Flagged Issues":
        st.title("Flagged Extraction Issues")
        if not flags:
            st.success("No issues flagged yet."); return
        st.markdown(f"**{len(flags)} issue(s) flagged.**")
        st.download_button("Download flagged_issues.json", data=json.dumps(flags, indent=2),
                           file_name="flagged_issues.json", mime="application/json")
        st.divider()
        by_type = {}
        for cid, flag in flags.items():
            by_type.setdefault(flag.get("issue_type","Other"), []).append((cid,flag))
        for issue_type, items in sorted(by_type.items()):
            st.markdown(f"#### {issue_type} ({len(items)})")
            for cid, flag in items:
                node = id_index.get(cid)
                if node:
                    with st.expander(f"**{node.get('number',cid)}** - {node.get('title','')}"):
                        st.markdown(f"**Note:** {flag.get('note','—')}  |  **Flagged:** {flag.get('flagged_at','?')[:10]}")
                        render_clause(node, flags, show_flag_ui=True)
                else:
                    st.warning(f"Clause `{cid}` not found in current document.")

    elif mode == "Stats & Raw":
        st.title("Extraction Statistics")
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Pages",    doc.get("total_pages","?"))
        c2.metric("Chapters", len(chapters))
        c3.metric("Sections", sum(len(ch.get("sections",[])) for ch in chapters))
        c4.metric("Clauses",  len(clause_list))
        st.divider()

        if stats:
            st.subheader("Reference Resolution")
            total=stats.get("total_references",0); resolved=stats.get("resolved_references",0)
            rate=stats.get("resolution_rate_pct",0)
            c1,c2,c3=st.columns(3)
            c1.metric("Found",total); c2.metric("Resolved",resolved); c3.metric("Rate",f"{rate}%")
            st.progress(int(rate)/100)
            unresolved=[{"Clause":cl.get("number"),"Reference Text":r.get("text"),"Target ID":r.get("target_id","—")}
                        for cl in clause_list for r in cl.get("references",[]) if not r.get("resolved")]
            if unresolved:
                st.markdown(f"**{len(unresolved)} unresolved** (may point to appendices or external standards):")
                st.dataframe(pd.DataFrame(unresolved), use_container_width=True, hide_index=True)
        st.divider()

        st.subheader("Per-Chapter Breakdown")
        rows=[]
        for ch in chapters:
            secs=ch.get("sections",[]); cls=[cl for s in secs for cl in s.get("clauses",[])]
            rows.append({"Chapter":f"{ch['number']} - {ch['title']}","Sections":len(secs),
                         "Clauses":len(cls),"Sub-clauses":sum(len(cl.get("sub_clauses",[])) for cl in cls),
                         "Tables":sum(len(cl.get("tables",[])) for cl in cls),
                         "Flagged":sum(1 for cl in cls if cl["id"] in flags)})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.divider()

        st.subheader("Downloads")
        st.download_button("Download structured_document.json", data=json.dumps(doc,indent=2),
                           file_name="structured_document.json", mime="application/json")
        raw_path = Path("storage")/f"raw_{Path(doc.get('source_pdf','')).stem}.json"
        if raw_path.exists():
            raw_text=raw_path.read_text(encoding="utf-8")
            st.download_button(f"Download {raw_path.name}", data=raw_text,
                               file_name=raw_path.name, mime="application/json")
            with st.expander("Preview raw JSON (first 200 lines)"):
                lines=raw_text.splitlines()
                st.code("\n".join(lines[:200])+("\n..." if len(lines)>200 else ""), language="json")
        else:
            st.info(f"Raw cache not found at {raw_path}.")

if __name__ == "__main__":
    main()