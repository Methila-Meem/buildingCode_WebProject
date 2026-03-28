# Building Code Web Project — Context for Claude

## Project Purpose
A pipeline that extracts, structures, and serves **building code PDFs** (e.g. BCBC) so they can be browsed, searched, and cross-referenced via a web interface.

---

## Tech Stack
| Layer | Technology |
|---|---|
| PDF Extraction | Datalab Marker API (async job, JSON output) |
| AI Enhancement | Anthropic Claude (`claude-sonnet-4-20250514`) via `anthropic` SDK |
| Backend API | FastAPI + Uvicorn (port 8000) |
| Viewer | Streamlit (`viewer_streamlit.py`) on port 8501 |
| Storage | JSON files (`storage/output/structured_document.json`) + JPEG figures (`storage/figures/`) |
| Env | Python venv (`.venv`), secrets in `.env` |

**Dependencies** (`requirements.txt`): `requests`, `anthropic`, `pdfplumber`, `pymupdf`, `fastapi`, `uvicorn`, `python-multipart`, `python-dotenv`, `streamlit`, `pandas`

> **Note:** `pdfplumber` and `pymupdf` are listed in `requirements.txt` but are **not imported anywhere** in the current codebase. The pipeline uses the Datalab API for PDF extraction. These are legacy dependencies from an earlier approach and can be removed.

---

## Project File Map
```
buildingCodeWebProject/
├── main.py                        # CLI pipeline entry point
├── viewer_streamlit.py            # Streamlit document viewer/QA tool
├── .env                           # DATALAB_API_KEY, ANTHROPIC_API_KEY
├── requirements.txt
├── bcbc_2024_Part4-509-654.pdf    # Sample BCBC Part 4 PDF (pages 509-654)
│
├── docs/
│   ├── GUIDE.docx                 # Project usage guide
│   └── GUIDE.html                 # HTML version of guide
│
├── ingestion/
│   └── datalab_client.py          # Submit PDF → poll Datalab API → cache → return JSON
│
├── parser/
│   ├── structure_parser.py        # Datalab JSON → Document tree (dataclasses)
│   ├── reference_linker.py        # Resolve cross-references + appendix note refs
│   └── ai_enhancer.py             # Claude calls for table labeling, block classification
│
├── storage/
│   ├── document_store.py          # save_document / load_document / build_search_index
│   ├── raw_{pdf_stem}.json        # Cached raw Datalab API response
│   ├── figures/                   # Extracted images saved as JPEG (hash-named)
│   └── output/
│       ├── structured_document.json   # Final processed document
│       └── flagged_issues.json        # QA flags from Streamlit viewer
│
└── api/
    └── main.py                    # FastAPI app — serves structured document via REST
```

---

## Data Model (Document Hierarchy)

Clauses use an **ordered `content[]` array** to preserve PDF reading sequence. Sub-clauses, equations, figures, and tables are all `ContentItem` entries within `content[]` — they are **not** stored in separate top-level fields.

```
Document
  title, source_pdf, total_pages, extracted_at, _stats
  └── Chapter  (id: CH-4, number: "4", title: "Structural Design")
        └── Section  (id: SEC-4-1, number: "4.1", title: "Loads")
              └── Clause  (id: CL-4-1-6-5, number: "4.1.6.5", title: "...", page_span: [int, ...])
                    ├── content[]   — ordered list of ContentItems:
                    │     { type: "text",       value: "..." }
                    │     { type: "sub_clause",  marker: "(a)", value: "..." }
                    │     { type: "equation",   latex: "..." }
                    │     { type: "figure",     figure_id: "FIG-1", image_path: "...", caption: "..." }
                    │     { type: "table",      table_id: "TBL-1", value: "caption text" }
                    ├── tables[]    [{ id: "TBL-n", caption, headers[], rows[][], page, column_semantics[] }]
                    ├── figures[]   [{ id: "FIG-n", caption, alt_text, image_key, image_path, page }]
                    ├── equations[] [{ id: "EQ-n", latex, page }]
                    ├── references[] [{ text, kind, target_id, resolved: bool }]
                    └── note_refs[] [{ raw, note_ref, target_ids: [...], resolved: bool }]
                        ↑ added dynamically by reference_linker.py — NOT in the Clause dataclass
```

**`_stats` on Document (added by `reference_linker.py`):**
```python
{
    "total_references": 150,        # Sentence/Article/Section/Table/Figure refs
    "resolved_references": 140,
    "resolution_rate_pct": 93.3,
    "total_note_refs": 45,          # "See Note A-..." refs
    "resolved_note_refs": 12,       # Only counts notes found in this same PDF
    "note_resolution_rate_pct": 26.7,
}
```

---

## Pipeline Steps (`main.py run_pipeline`)
1. **Ingest** — `ingestion/datalab_client.extract_pdf(pdf_path, force_extract)` → submits PDF, polls until done, saves `storage/raw_{pdf_stem}.json` (cached; skipped on re-runs unless `--force-extract`)
2. **Parse** — `parser/structure_parser.parse_datalab_output(result, source_pdf, figures_dir)` → builds Document tree; extracts images to `storage/figures/`
3. **Link** — `parser/reference_linker.link_references(doc)` → regex-scans clause content, resolves cross-references and `See Note A-...` refs, writes `_stats`
4. **Enhance** *(optional, `--ai` flag)* — `parser/ai_enhancer.enhance_document(doc)` → Claude labels table columns semantically, storing `column_semantics[]` on each table
5. **Save** — `storage/document_store.save_document(doc)` → writes `structured_document.json`

---

## Datalab Client (`ingestion/datalab_client.py`)

**API parameters sent:**
```python
{ "output_format": "json", "use_llm": "true", "extract_images": "false" }
```
- Endpoint: `https://www.datalab.to/api/v1/marker`
- Poll interval: 5 s, max wait: 300 s, submit timeout: 60 s
- Cache path: `storage/raw_{pdf_stem}.json` — skip with `--force-extract`
- Raises `EnvironmentError` if `DATALAB_API_KEY` missing or placeholder
- Raises `TimeoutError` if polling exceeds max_wait

---

## Structure Parser (`parser/structure_parser.py`)

### Key HTML Processing Functions

| Function | Purpose |
|---|---|
| `inline_math_to_markdown(html)` | Converts `<math>` tags to `$...$` inline notation; strips remaining HTML → single markdown string |
| `extract_math(html)` | Returns **list** of LaTeX strings (one per `<math>` tag) — each becomes a separate Equation ContentItem |
| `listgroup_to_lines(html)` | Preserves `<math>` as `$...$`, strips other HTML, converts `</li>` to newlines |
| `parse_table_html(html)` | Parses HTML tables with multi-row `<thead>` colspan/rowspan, `<tbody>` rowspan carry, bbox-based empty-cell carry, final-row sub-label skip, and spanning-rows last-row exception |
| `strip_html(html)` | Removes tags, decodes entities, normalizes whitespace |
| `_strip_html_keep_text(html)` | Strips all HTML **except** `<math>` markers; used when splitting inline-math blocks |
| `split_inline_math(html)` | Legacy compatibility shim — calls `inline_math_to_markdown()` and returns `[{type:"text", value:...}]` |
| `extract_alt_text(html)` | Extracts the `alt` attribute from an `<img>` tag; falls back to `strip_html()` |
| `parse_heading(html)` | Extracts `(level: int, plain_text: str)` from `<h1>–<h6>` tags |
| `save_image(image_key, b64, figures_dir)` | Decodes base64, saves as JPEG to `storage/figures/{image_key}` |

### `StructureParser` Class

**Attributes:**
- `source_pdf`, `figures_dir` — set in `__init__`
- `_chapter_counter`, `_auto_clause_counter`, `_table_counter`, `_equation_counter`, `_figure_counter` — global counters
- `_images_dict` — populated from `datalab_result["images"]` before flattening

**Key Methods:**
| Method | Purpose |
|---|---|
| `parse(datalab_result)` | Main entry; calls `_flatten_blocks()` then `_build_hierarchy()` |
| `_flatten_blocks(datalab_result)` | Produces flat ordered block list from Datalab pages |
| `_build_hierarchy(blocks)` | Builds Chapter→Section→Clause tree; contains nested `add_text()` helper |
| `_find_figure_caption(siblings, fig_idx, alt_text)` | 4-step bidirectional caption search |
| `_flatten_legacy(datalab_result)` | Fallback for old Datalab format or markdown-only responses |
| `_detect_title(blocks)` | Returns first h1 heading text, or `"Building Code Document"` |
| `_parse_part_heading(text)` | Parses Part/Chapter number and title from h1 text; increments `_chapter_counter` |
| `_make_clause(number, title, page, section)` | Creates a Clause and appends it to `section.clauses` |
| `_clause_id_for(number)` | Returns `CL-{number}` for numbered clauses; `CL-AUTO-{n}` (using `_auto_clause_counter`) for unnumbered |
| `_remove_empty_clauses(chapters)` | Drops clauses with no content, figures, tables, or equations |
| `_merge_continued_tables(chapters)` | Merges cross-page `(continued)` table fragments; applies cross-page rowspan carry |
| `to_dict(document)` | Thin wrapper — calls `asdict(document)` to return JSON-serializable dict |

**`_flatten_blocks()`** produces flat ordered block list from Datalab pages:
- `SectionHeader` h1-h6 → heading entry
- `ListGroup` → sub-clause lines via `listgroup_to_lines()`
- `Equation` → one entry **per `<math>` tag** via `extract_math()` (list); falls back to `strip_html()` if no math tags
- `Text` with `<math>` → marked `has_inline_math=True`, raw HTML preserved for `inline_math_to_markdown()`
- `Figure`/`Picture` → bidirectional caption via `_find_figure_caption()`; decorative images skipped (see below)
- `Caption` → math-aware: uses `inline_math_to_markdown()` if `<math>` present, else `strip_html()`; buffered for next table
- `PageHeader`/`PageFooter` → skipped

**Decorative image filtering — two stages with different keyword sets:**

In `_flatten_blocks`: skips Figure/Picture blocks where `alt_text.lower()` is an **exact match** for any of: `"horizontal line"`, `"vertical line"`, `"divider"`, `"line"`, `"rule"`, `"separator"`. No length check.

In `_build_hierarchy`: skips figure blocks where alt text is **< 60 characters** AND the text starts with or exactly matches one of: `"horizontal line"`, `"vertical line"`, `"divider"`, `"separator"`, `"solid black line"`, `"decorative"`. The figure counter is decremented when a decorative image is skipped in either stage.

**`_find_figure_caption()` — 4-step search:**
1. Check block immediately **before** — if `Caption`, use it
2. Check block immediately **after** — if `Caption`, use it
3. Check block after for `SectionHeader` matching `"Notes to Figure X"` pattern
4. Fallback: extract figure number from alt text via `RE_FIGURE_NUM`

**`_build_hierarchy()` heading rules:**
- h1 → new Chapter (via `_parse_part_heading()`)
- h2 → new Section if `RE_SECTION` matches; else orphan (skipped)
- h3 → new Section if `RE_ARTICLE` (3-part number) matches; else plain title → label clause under current section
- h4 → check 4-part (`RE_SENTENCE`) **before** 3-part (`RE_ARTICLE`); both create Clauses; plain title → unnumbered clause
- h5 → always new clause (Notes to Table/Figure headings + Appendix entries → `CL-AUTO-N`)
- h6 → if `current_clause` exists → append `**text**` as bold text item in content; else if `current_section` → new clause

**Text block auto-detection** (in addition to heading-based hierarchy):
Plain `Text` blocks whose first line matches a structural number pattern are promoted:
- 4-part number → new Clause (guarded: skipped if that `CL-ID` already exists)
- 3-part number → new Section (guarded: skipped if that `SEC-ID` already exists)

**Orphaned figure handling:** When a `figure` block arrives with no `current_clause`, a minimal holder clause is created (titled with the caption or alt text) and appended to `current_section`.

**`add_text()` nested helper** (defined inside `_build_hierarchy()`):
If `has_inline_math`, runs `inline_math_to_markdown()` on raw HTML first; then splits by lines; detects sub-clause markers `(a)`, `a)`, `i.` etc.; creates `ContentItem(type="text")` or `ContentItem(type="sub_clause")`.

**Post-processing:**
- `_remove_empty_clauses()` — drops clauses with no content, figures, tables, or equations
- `_merge_continued_tables()` — merges cross-page `(continued)` table fragments into base table; applies cross-page rowspan carry (sandwich detection for 2-col use/load tables)

**`parse_table_html()` — special header collapse rules:**
- **Final-row sub-label skip**: In the column-name collapse loop, when `col == 0` AND `row_i == n_rows - 1` AND the label is ≤4 chars AND matches `^[0-9A-Z]+$` AND the column already has a longer label, the label is skipped. Only applies to col 0 — data columns always keep their final-row labels. Handles Datalab underreporting rowspan (e.g. Table 4.1.7.6).
- **Spanning-rows detection**: Rows with a single new cell whose colspan > 1 are recorded in a `spanning_rows` set. However, this set is **not** used to restrict labels to col 0 — spanning row labels appear in all their columns via the normal label-grid collapse. The `spanning_rows` set is computed for documentation purposes; the actual filtering is only the final-row sub-label skip above.
- **Spanning-rows last-row exception**: The spanning subheader detection skips `row_i == n_rows - 1`. The last header row is never treated as a spanning subheader — it is the primary data descriptor and must appear in all columns (e.g. "Value of C_b" in Table 4.1.6.2.-B).

### Regex Patterns
```python
RE_PART      = r'^Part\s*(\d+)\s*(.*)'         # re.IGNORECASE
RE_SECTION   = r'^(?:Section\s+)?(\d+\.\d+)\.?\s*(.*)'   # re.IGNORECASE
RE_ARTICLE   = r'^(\d+\.\d+\.\d+)\.?\s*(.*)'
RE_SENTENCE  = r'^(\d+\.\d+\.\d+\.\d+)\.?\s*(.*)'        # checked before RE_ARTICLE
RE_SUBCLAUSE = r'^\s*(\([a-z]+\)|[a-z]\)|[ivxlcdm]+\.)\s+(.+)'  # re.IGNORECASE
RE_FIGURE_NUM = r'Figure\s+([\d\.]+[\w\.\-]*)'  # caption fallback from alt text
```

### Public Entry Point
```python
parse_datalab_output(datalab_result, source_pdf="unknown.pdf",
                     figures_dir="storage/figures") -> dict
```
Creates a `StructureParser`, calls `parse()`, then `to_dict()` → returns JSON-serializable document tree.

---

## Reference Linker (`parser/reference_linker.py`)

**Standard cross-references** → `clause.references[]`:
- Kinds: `Sentence`, `Article`, `Subsection`, `Section`, `Clause`, `Table`, `Figure`
- `Subsection`/`Section` always maps to `SEC-...`; others map to `CL-...`
- Table/Figure: normalized caption lookup first (strips dots/hyphens), then fallback ID
- `resolved: false` for external-PDF targets

**Appendix note references** → `clause.note_refs[]` — pattern: `See Note A-<identifier>`:
- `target_ids` is a **list** (can resolve to multiple clauses)
- **Two-pass note index:** indexes both CL-AUTO clause titles AND embedded text items starting with `A-`
- Fallback: strip sentence sub-number and retry (e.g. `A-4.1.3.2.(2)` → `A-4.1.3.2`)
- `resolved: false` for external appendix notes (different PDF)
- **`RE_NOTE` and `RE_A_TITLE` patterns** use `\d+(?:\.\d+)*` (not `[\d\.]+`) so that sub-note identifiers like `A-4.1.6.16.(6)` are captured correctly. The old `[\d\.]+` greedily consumed the trailing dot, preventing `(?:\.\(\d+\))?` from matching the `(6)` suffix.

**Key functions:**
| Function | Purpose |
|---|---|
| `build_id_index(document_dict)` | Flat `{id→node}` lookup + `_cap_`-prefixed caption keys for tables/figures |
| `build_note_index(document_dict)` | Note key → `[clause_id, ...]` lookup; two-pass (titles + embedded text) |
| `_normalize_ref(s)` | Strips dots/hyphens for fuzzy caption matching; handles PDF typos |
| `_ref_to_id(ref, kind, id_index)` | Converts reference string to node ID |
| `_extract_refs_from_text(text)` | Scans text for all standard reference patterns |
| `_extract_notes_from_text(text)` | Scans text for all `(See Note A-...)` patterns |
| `_resolve_note(note_ref, note_index)` | Resolves note ref to list of clause IDs; exact then base match |
| `link_references(document_dict)` | Main entry; populates `references[]` and `note_refs[]` on all clauses |

---

## AI Enhancer (`parser/ai_enhancer.py`)

Claude model: `claude-sonnet-4-20250514`

| Function | Purpose | Max tokens |
|---|---|---|
| `get_claude_client()` | Creates `anthropic.Anthropic()` client; raises `EnvironmentError` if key missing | — |
| `ask_claude(prompt)` | Base call | 1024 |
| `label_table_columns(headers, rows)` | Semantic column labels → `column_semantics[]` | 400 |
| `classify_block(text, ctx_before, ctx_after)` | Classify as clause/continuation/paragraph/list_item (**not called by pipeline**) | 200 |
| `should_join_fragments(end, start)` | Detect cross-page list continuation (**not called by pipeline**) | 10 |
| `resolve_ambiguous_reference(ref, clause_text, ids)` | Resolve "see above table"-style refs (**not called by pipeline**) | 100 |
| `enhance_document(doc, use_ai_for_tables)` | Main entry; walks all tables, adds `column_semantics[]` | — |

All functions strip markdown fences from Claude responses before JSON parsing; fall back to sensible defaults on parse errors.

---

## FastAPI Endpoints (`api/main.py`, port 8000)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Health check → `{"status": "ok", "message": "..."}` |
| GET | `/document` | Full document tree |
| GET | `/document/summary` | Lightweight nav tree (chapters + section clause counts, no clause content) |
| GET | `/section/{section_id}` | Single section with clauses |
| GET | `/clause/{clause_id}` | Single clause with `_breadcrumb` context |
| GET | `/search?q=term` | Full-text search (caps at 50 results); snippet = 60 chars before + 100 after match |
| GET | `/references/{node_id}` | Reverse lookup: what clauses reference this node |

**CORS allows `http://localhost:8501` and `http://127.0.0.1:8501`** (Streamlit viewer).

**Caching:** `_document_cache` and `_search_index_cache` as module globals — loaded on first request.

---

## Search Index (`storage/document_store.py`)

`build_search_index()` returns per-clause entries with:

| Field | Source |
|---|---|
| `id`, `type`, `number`, `title` | Clause fields |
| `text` | Concatenation of all content[] items: text/sub_clause `value` + equation `latex` + figure `caption` (or `alt_text` ≤120 chars) + table `value` (caption) |
| `snippet` | First text or sub_clause `value`, truncated to 200 chars |
| `breadcrumb` | `"Chapter N > M.K > L"` |
| `page` | First entry in `page_span` |

---

## Streamlit Viewer (`viewer_streamlit.py`)

Run: `streamlit run viewer_streamlit.py` → opens at http://localhost:8501

**3 modes (sidebar radio):**
- **Browse** (📑) — Continuous-scroll rendering of all chapters, sections, and clauses; sidebar tree expander for navigation; JS scroll injection to jump to a target clause
- **Search** (🔍) — Client-side full-text search over `clause_list` (not the FastAPI endpoint); caps at 30 results; matching clauses rendered in expandable sections
- **Stats & Raw** (📊) — Per-chapter breakdown, reference resolution stats, per-clause unresolved refs table, raw JSON download and preview

> **Note:** A "🚩 Flagged Issues" mode was previously planned. The QA flag data structures (`save_flag`, `remove_flag`, `load_flags`, `FLAGS_PATH`) are fully implemented, and per-clause flag UI is embedded inside `render_clause()`, but there is no dedicated sidebar mode for reviewing all flags by issue type.

### Index Builders (viewer-local)
| Function | Purpose |
|---|---|
| `build_id_index(doc)` | Flat `{id→node}` lookup; FIG-/TBL- nodes get `_parent_clause_id` for navigation; all nodes get `_type` and parent breadcrumb metadata |
| `build_clause_list(doc)` | Flat list of all clauses with parent chapter/section context |

### Content Rendering

| Content type | Renderer | Notes |
|---|---|---|
| `text` (plain) | `st.markdown()` | Inline `$...$` renders as KaTeX |
| `text` (where line) | Indented italic block | Lines starting with "where" / "where:" |
| `text` (variable def) | Two-column: symbol \| description | Detects `$symbol$ = ...` or `symbol = ...` pattern; pre-processes space-subscript notation via regex |
| `text` (inline note) | `_render_text_with_inline_notes()` | Splits at `(See Note A-...)` and renders as buttons |
| `text` (roman numeral sub-clause) | `render_subclause_item()` | Pattern `^([ivxlcdm]{2,})\)\s+(.+)$` detected inside `render_text_item` |
| `text` (embedded sub-clause) | Split + `render_subclause_item()` | `RE_EMBEDDED_SC` / `RE_GLUED_SC` patterns detect sub-clause markers glued to preceding text |
| `text` (unit superscripts) | `st.markdown()` with `$m^{2}$` substitution | `mm`/`km`/`m` + ` 2`/ ` 3` converted when no existing `$` or `\` |
| `equation` | `st.latex()` (display math) | Code block fallback if malformed |
| `figure` | `st.image()` + caption + alt text | Container with border & styling; alt text truncated to 200 chars |
| `table` | Custom HTML + KaTeX via `st.components.v1.html()` | Renders `$...$` with KaTeX CDN v0.16.9; visual rowspans applied |
| `sub_clause` | Two-column flex: marker \| markdown value | Inline math converted via `_value_with_inline_math()`; column ratio `[1, 24]` |

### Table Rendering (`_html_table()`)
Tables are rendered as self-contained HTML documents (not `st.dataframe()`) to support math. `_html_table()` returns a **tuple `(html_str, est_height)`** consumed by `st.components.v1.html(html_doc, height=height, scrolling=True)`.

Helper functions:
- `_split_math_segments(s)`: splits a string into `(segment, is_math)` tuples based on `$...$` delimiters. KaTeX validity rules applied (opening `$` must not be followed by whitespace; closing `$` must not be preceded by whitespace). Used by `_wrap_cell_math()` and `_value_with_inline_math()` to avoid double-wrapping already-delimited math regions.
- `_wrap_cell_math()`: wraps LaTeX expressions in cells with `$...$`. Uses `_split_math_segments` — only processes plain-text segments, leaves existing `$...$` regions untouched. Uses `COMBINED_RE` + `OP_ONLY` bridge detection for token-level greedy merging; display commands (`\frac`, `\sum`, etc.) trigger whole-cell wrap.
- `_fix_cell_subscripts(cell)`: pre-processes table cell text for **uppercase multi-letter subscripts** — pattern `r'(?<![_^{\\])([A-Za-z])\s([A-Z]{2,5})\b'` → converts e.g. `L XC` → `L_{XC}`. Handles 2–5 uppercase char subscripts; single-char subscripts handled by `_wrap_cell_math`.
- `_esc_html_math()`: HTML-escapes `&`, `<`, `>` outside `$...$`; inside math only escapes `&`. Uses a simple `$...$` scan (not `_split_math_segments`).
- `_render_cell_content(cell)`: calls `_fix_cell_subscripts` first; handles bullet characters (`•`) in table cells — splits on `•`, renders each bullet on its own line via `<br>`, applies math wrapping to each segment.
- `_build_tbody_with_rowspan()`: applies visual `rowspan` to consecutive identical values in cols 0–1 (max span 30).
- `_build_hierarchical_thead(headers)`: reconstructs multi-row `<thead>` from header strings that use `' / '` as a hierarchy separator (e.g. `"Factors / Arch / $C_a$ Downwind"`). Returns `(thead_html_str, num_header_rows)`. Intermediate cells get `colspan` for shared path prefixes; leaf cells get `rowspan` to fill remaining rows. Falls back to single-row `<thead>` if no `' / '` found.
- `_recover_vardef_subscripts(text)`: recovers subscript notation lost when the parser stripped `<sub>` tags — pattern `r'(?<!\w)([A-Za-z])\s([a-z]{1,5})(?!\w)'` converts e.g. `C w` → `$C_{w}$`. Applied to variable-definition descriptions; only processes non-math segments via `_split_math_segments`.
- `_value_with_inline_math()`: converts raw LaTeX tokens in sub-clause text to `$...$`; uses `_split_math_segments` to leave existing `$...$` regions untouched.
- Height estimated dynamically: `header_h` uses the longest individual-level label (not the full `' / '`-joined string for hierarchical headers); `row_h` = 56/40/30 based on max cell length; `est_height = header_h + rows * row_h + caption_h + 16`.

### Reference Rendering
- **Standard refs:** resolved → clickable button (max 4 per row) → `navigate_to()` + `st.rerun()`; unresolved → grey badge
- **Note refs:** resolved → green button(s); single target → button labeled `note_ref`; multiple target_ids → one button per target labeled `"{note_ref} [{j+1}]"`; unresolved → amber badge. Button label uses `note_ref` directly (not derived from the CL-AUTO clause title, which may represent a different embedded sub-note).

### Inline Note Pattern Fixes (`viewer_streamlit.py`)
All three inline note regex patterns use `\d+(?:\.\d+)*` instead of `[\d\.]+` to correctly capture sub-note identifiers like `A-4.1.6.16.(6)`. The old `[\d\.]+` greedily consumed the trailing dot and prevented the sub-number suffix from being matched.

### Navigation via Query Params
`?clause=CL-...` deep-links to any node. `navigate_to(id)` sets `st.query_params["clause"]`. The navigation handler uses `if/elif/elif` branching on `_type`:
- `_type == "clause"`: finds parent section → sets `selected_section_id` + `scroll_target` → `st.rerun()`
- `_type == "section"`: sets `selected_section_id` + `scroll_target` → `st.rerun()`
- `_type in ("figure", "table")`: resolves `_parent_clause_id` → sets section + scroll_target → `st.rerun()`

All three branches are reachable (no `return` before the elif branches). The `st.rerun()` call in the clause branch exits the run but control does not fall through to other branches.

**QA flag types:** `Missing text`, `Wrong hierarchy`, `Table error`, `Sub-clause split wrong`, `Equation wrong`, `Figure missing`, `Figure wrong position`, `Reference not resolved`, `Wrong page number`, `Other`

Flags saved to `storage/output/flagged_issues.json` as `{ clause_id → { clause_id, issue_type, note, flagged_at } }`.

---

## ID Naming Conventions
| Node | Pattern | Example |
|---|---|---|
| Chapter | `CH-{n}` | `CH-4` |
| Section | `SEC-{n}-{m}[-{k}...]` | `SEC-4-1` or `SEC-4-1-6` |
| Clause | `CL-{n}-{m}-{k}[-{j}...]` | `CL-4-1-6-5` |
| Auto-clause (no number) | `CL-AUTO-{n}` | `CL-AUTO-1` |
| Table | `TBL-{n}` | `TBL-4` |
| Equation | `EQ-{n}` | `EQ-2` |
| Figure | `FIG-{n}` | `FIG-3` |

Periods in numbers are replaced with hyphens in IDs: `4.1.6.5` → `CL-4-1-6-5`.

---

## Key Patterns & Conventions
- **Env secrets**: always via `load_dotenv()` + `os.getenv()` — never hardcoded
- **Claude model**: `claude-sonnet-4-20250514` (in `ai_enhancer.py`)
- **Inline math**: `<math>` tags in body text → `$...$` notation for `st.markdown()` KaTeX rendering (not block `st.latex()`)
- **Block equations**: each `<math>` tag in an Equation block → separate `EQ-N` ContentItem rendered with `st.latex()`
- **Ingestion cache**: `storage/raw_{pdf_stem}.json` — avoids repeat API charges; bypassed with `--force-extract`
- **Figures dir**: `storage/figures/` — base64 decoded to JPEG, hash-named (not FIG-N.jpg)
- **Document cache**: `api/main.py` caches as module globals; loaded on first request
- **Fallback parsing**: `_flatten_legacy()` handles old Datalab format or markdown-only responses
- **Heading levels**: h1→Chapter, h2→Section, h3→Section (3-part number) or label clause, h4→Clause (4-part checked first), h5→Notes/Appendix (CL-AUTO), h6→bold text item or new clause
- **Text block promotion**: text blocks starting with a structural number can auto-promote to section/clause; duplicate-ID guards prevent re-creation
- **Bidirectional caption search**: figure captions looked up before, after, and via "Notes to Figure" heading
- **Table merging**: cross-page `(continued)` fragments merged into base table; cross-page rowspan carry via sandwich detection
- **Rowspan/colspan parsing**: multi-row `<thead>` with label grid collapsing; `<tbody>` rowspan carry dict; bbox-based carry for Datalab-missing rowspan attrs
- **Decorative image filtering**: two-stage with different keyword sets — `_flatten_blocks` uses exact-match on `"horizontal line"`, `"vertical line"`, `"divider"`, `"line"`, `"rule"`, `"separator"`; `_build_hierarchy` uses <60 char check + starts_with/exact for `"horizontal line"`, `"vertical line"`, `"divider"`, `"separator"`, `"solid black line"`, `"decorative"`; figure counter decremented on skip
- **Orphaned figure handling**: creates a minimal holder clause when a figure block has no active clause
- **Storage is file-based** — `document_store.py` noted as swap candidate for PostgreSQL/SQLite
- **Reference normalization**: dots/hyphens stripped for fuzzy caption matching (handles PDF typos)
- **Note index two-pass**: indexes appendix clause titles AND embedded text items starting with `A-`
- **Note ref regex**: `RE_NOTE` and `RE_A_TITLE` use `\d+(?:\.\d+)*` (not `[\d\.]+`) so sub-note identifiers such as `A-4.1.6.16.(6)` are captured fully; fix improved note resolution from 73/77 (94.8%) to 128/133 (96.2%)
- **Inline note viewer regex**: same `\d+(?:\.\d+)*` fix applied to all three inline note patterns in `viewer_streamlit.py`
- **Table cell math safety**: `_wrap_cell_math()` and `_value_with_inline_math()` use `_split_math_segments()` to avoid double-wrapping existing `$...$` regions
- **Table scrolling**: tables rendered with `scrolling=True` in `st.components.v1.html()`
- **Table height estimation**: header height computed dynamically from column count and text length; hierarchical headers use individual level labels (not full `' / '`-joined string) for estimation
- **Hierarchical table headers**: parser stores multi-level column names joined with `' / '`; viewer's `_build_hierarchical_thead()` reconstructs proper `<thead>` rows with `colspan`/`rowspan`
- **Table bullet cells**: `_render_cell_content()` splits on `•` and renders each bullet on its own line
- **Deduplication**: reference linker tracks `(kind, ref)` tuples per clause; note linker tracks `note_ref` per clause
- **`note_refs[]` is dynamic**: not part of the `Clause` dataclass — added to the dict by `reference_linker.link_references()`
- **Navigation**: all three branches (clause, section, figure/table) in the `if nav_target:` block are reachable via `if/elif/elif`; `st.rerun()` exits the current run rather than falling through
- **Subscript recovery**: `_recover_vardef_subscripts()` in the viewer re-creates `$X_{sub}$` notation for variable definitions where `<sub>` tags were stripped by the parser
- **Table cell subscript fix**: `_fix_cell_subscripts()` handles 2–5 uppercase-char subscript patterns (e.g. `L XC` → `L_{XC}`) before math wrapping; complements the single-char handling in `_wrap_cell_math()`
- **Unused AI functions**: `classify_block()`, `should_join_fragments()`, and `resolve_ambiguous_reference()` in `ai_enhancer.py` are defined but not called anywhere in the current pipeline — available for future use

---

## How to Run
```bash
# 1. Process a PDF
python main.py path/to/building_code.pdf
python main.py path/to/building_code.pdf --force-extract  # skip cache, re-call Datalab API
python main.py path/to/building_code.pdf --ai             # with Claude table enhancement

# 2. Start API
uvicorn api.main:app --reload --port 8000

# 3. Start Streamlit viewer
streamlit run viewer_streamlit.py
```

---

## Potential Feature Areas (for future prompts)
- React/Next.js frontend (CORS currently configured for Streamlit port 8501 — update for port 3000 if adding React)
- Export to PDF/Word/CSV
- Multi-document support (currently single document per pipeline run)
- Database backend (PostgreSQL/SQLite replacing JSON file storage)
- Authentication for the API
- Annotation/comment system on clauses
- Diff view between two versions of a building code
- Clause comparison across documents
- AI-powered Q&A over the document (RAG)
- Add "🚩 Flagged Issues" sidebar mode to expose existing flag data (`save_flag`, `load_flags`, `remove_flag` are implemented; only missing the dedicated viewer panel)
- Wire up unused AI pipeline functions (`classify_block`, `should_join_fragments`, `resolve_ambiguous_reference`) or remove them
- Remove unused `pdfplumber` and `pymupdf` dependencies from `requirements.txt`
