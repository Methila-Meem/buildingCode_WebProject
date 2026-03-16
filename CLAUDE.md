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

---

## Project File Map
```
buildingCodeWebProject/
├── main.py                        # CLI pipeline entry point
├── viewer_streamlit.py            # Streamlit document viewer/QA tool
├── .env                           # DATALAB_API_KEY, ANTHROPIC_API_KEY
├── requirements.txt
│
├── ingestion/
│   └── datalab_client.py          # Submit PDF → poll Datalab API → cache → return JSON
│
├── parser/
│   ├── structure_parser.py        # Datalab JSON → Document tree (dataclasses)
│   ├── reference_linker.py        # Resolve internal cross-references in clauses
│   └── ai_enhancer.py             # Claude calls for table labeling, block classification
│
├── storage/
│   ├── document_store.py          # save_document / load_document / build_search_index
│   ├── raw_{pdf_stem}.json        # Cached raw Datalab API response (e.g. raw_bcbc_2024_Part4-509-654.json)
│   ├── figures/                   # Extracted images saved as JPEG (FIG-1.jpg, etc.)
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
                    │     { type: "table",      table_id: "TBL-1" }   ← inline reference only
                    ├── tables[]    [{ id: "TBL-n", caption, headers[], rows[][], page, column_semantics[] }]
                    ├── figures[]   [{ id: "FIG-n", caption, alt_text, image_key, image_path, page }]
                    ├── equations[] [{ id: "EQ-n", latex, page }]
                    └── references[] [{ text, kind, target_id, resolved: bool }]
```

---

## Pipeline Steps (`main.py run_pipeline`)
1. **Ingest** — `ingestion/datalab_client.extract_pdf(pdf_path, force_extract)` → submits PDF, polls until done, saves `storage/raw_{pdf_stem}.json` (cached; skipped on re-runs unless `--force-extract`)
2. **Parse** — `parser/structure_parser.parse_datalab_output(result, source_pdf, figures_dir)` → builds Document tree; extracts images to `storage/figures/`
3. **Link** — `parser/reference_linker.link_references(doc)` → regex-scans clause content, resolves "Sentence 4.1.6.5" → `CL-4-1-6-5`, writes `_stats`
4. **Enhance** *(optional, `--ai` flag)* — `parser/ai_enhancer.enhance_document(doc)` → Claude labels table columns semantically, storing `column_semantics[]` on each table
5. **Save** — `storage/document_store.save_document(doc)` → writes `structured_document.json`

---

## FastAPI Endpoints (`api/main.py`, port 8000)
| Method | Path | Description |
|---|---|---|
| GET | `/` | Health check |
| GET | `/document` | Full document tree |
| GET | `/document/summary` | Lightweight nav tree (no clause content) |
| GET | `/section/{section_id}` | Single section with clauses |
| GET | `/clause/{clause_id}` | Single clause with `_breadcrumb` context |
| GET | `/search?q=term` | Full-text search (caps at 50 results) |
| GET | `/references/{node_id}` | Reverse lookup: what clauses reference this node |

CORS allows `http://localhost:3000` and `http://127.0.0.1:3000` (React viewer placeholder).

---

## Streamlit Viewer (`viewer_streamlit.py`)
Run: `streamlit run viewer_streamlit.py` → opens at http://localhost:8501

4 modes (sidebar radio):
- **Browse** — Chapter → Section → Clause navigation; tabs for ≤6 clauses, selectbox for more
- **Search** — Full-text search across clause titles and all content text items (up to 30 results)
- **Flagged Issues** — Review clauses flagged as extraction errors; export JSON
- **Stats & Raw** — Per-chapter breakdown, reference resolution stats, raw JSON download

**Content rendering:**
- Text with LaTeX variable definitions → two-column (symbol + description)
- `where` lines → indented italic block
- Equations → `st.latex()` with code-block fallback
- Figures → `st.image()` with caption
- Tables → `pd.DataFrame` via `st.dataframe()`
- Sub-clauses → styled marker + text row
- References → clickable buttons (resolved) or grey badges (unresolved); clicking navigates via query param `?clause=CL-…`

**QA flag types:** `Missing text`, `Wrong hierarchy`, `Table error`, `Sub-clause split wrong`, `Equation wrong`, `Figure missing`, `Figure wrong position`, `Reference not resolved`, `Wrong page number`, `Other`

Flags are saved to `storage/output/flagged_issues.json` as `{ clause_id → { issue_type, note, flagged_at } }`.

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
- **Claude model**: `claude-sonnet-4-20250514` (set in `ai_enhancer.py:ask_claude`)
- **Ingestion cache**: `storage/raw_{pdf_stem}.json` — avoids repeat API charges; bypassed with `--force-extract`
- **Figures dir**: `storage/figures/` — base64 images from Datalab decoded and saved as JPEG at parse time
- **Document cache**: `api/main.py` caches `_document_cache` and `_search_index_cache` as module globals at startup
- **Fallback parsing**: if Datalab returns only markdown (no structured blocks), `_flatten_legacy()` / `_parse_markdown_fallback` handles it
- **Heading levels**: h1→Part/Chapter, h2→Section, h3→Subsection/Article (3-part), h4→Sentence (4-part), h5→Notes, h6→Sub-article
- **Bidirectional caption search**: captions looked up both before and after figure blocks to handle appendix layouts
- **Storage is file-based** — noted in `document_store.py` as swap candidate for PostgreSQL/SQLite
- **Reference kinds**: `Sentence`, `Article`, `Subsection`, `Section`, `Table`, `Figure` — resolved to node IDs and attached to `clause.references[]`

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
- React/Next.js frontend (currently referenced but not built — CORS ready for port 3000)
- Export to PDF/Word/CSV
- Multi-document support (currently single document per pipeline run)
- Database backend (PostgreSQL/SQLite replacing JSON file storage)
- Authentication for the API
- Annotation/comment system on clauses
- Diff view between two versions of a building code
- Clause comparison across documents
- AI-powered Q&A over the document (RAG)
