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
| Viewer | Streamlit (`viewer_streamlit.py`) |
| Storage | JSON files (`storage/output/structured_document.json`) |
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
│   └── datalab_client.py          # Submit PDF → poll Datalab API → return JSON
│
├── parser/
│   ├── structure_parser.py        # Datalab JSON → Document tree (dataclasses)
│   ├── reference_linker.py        # Resolve internal cross-references in clauses
│   └── ai_enhancer.py             # Claude calls for table labeling, block classification
│
├── storage/
│   ├── document_store.py          # save_document / load_document / build_search_index
│   ├── raw_output.json            # Raw Datalab API response
│   └── output/
│       ├── structured_document.json   # Final processed document
│       └── flagged_issues.json        # QA flags from Streamlit viewer
│
└── api/
    └── main.py                    # FastAPI app — serves structured document via REST
```

---

## Data Model (Document Hierarchy)
```
Document
  title, source_pdf, total_pages, extracted_at, _stats
  └── Chapter  (id: CH-3, number: "3", title: "Structural Loads")
        └── Section  (id: SEC-3-1, number: "3.1", title: "Dead Loads")
              └── Clause  (id: CL-3-1-2, number: "3.1.2", title: "...", text: "...")
                    ├── sub_clauses  [{ id, marker "(a)", text }]
                    ├── tables       [{ id TBL-n, caption, headers[], rows[][] }]
                    ├── equations    [{ id EQ-n, raw_text }]
                    ├── references   [{ text, target_id, resolved: bool }]
                    └── page_span    [int, ...]
```

---

## Pipeline Steps (`main.py run_pipeline`)
1. **Ingest** — `ingestion/datalab_client.extract_pdf(pdf_path)` → submits PDF, polls until done, saves `raw_output.json`
2. **Parse** — `parser/structure_parser.parse_datalab_output(result)` → builds Document tree from Datalab blocks (or markdown fallback)
3. **Link** — `parser/reference_linker.link_references(doc)` → regex-scans clause text, maps "Section 3.1.2" → `SEC-3-1-2`, writes `_stats`
4. **Enhance** *(optional, `--ai` flag)* — `parser/ai_enhancer.enhance_document(doc)` → Claude labels table columns semantically
5. **Save** — `storage/document_store.save_document(doc)` → writes `structured_document.json`

---

## FastAPI Endpoints (`api/main.py`, port 8000)
| Method | Path | Description |
|---|---|---|
| GET | `/` | Health check |
| GET | `/document` | Full document tree |
| GET | `/document/summary` | Lightweight nav tree (no clause text) |
| GET | `/section/{section_id}` | Single section with clauses |
| GET | `/clause/{clause_id}` | Single clause with breadcrumb |
| GET | `/search?q=term` | Full-text search (caps at 50 results) |
| GET | `/references/{node_id}` | Reverse lookup: what clauses reference this node |

CORS allows `http://localhost:3000` (React viewer placeholder).

---

## Streamlit Viewer (`viewer_streamlit.py`)
Run: `streamlit run viewer_streamlit.py`

4 modes (sidebar radio):
- **Browse** — Chapter → Section → Clause navigation with tabs
- **Search** — Full-text search across all clauses
- **Flagged Issues** — Review clauses flagged as extraction errors; export JSON
- **Stats & Raw** — Per-chapter breakdown, reference resolution stats, raw JSON download

QA flag types: `Missing text`, `Wrong hierarchy`, `Table error`, `Reference not resolved`, `Sub-clause split wrong`, `Equation garbled`, `Wrong page number`, `Other`

Flags are saved to `storage/output/flagged_issues.json`.

---

## ID Naming Conventions
| Node | Pattern | Example |
|---|---|---|
| Chapter | `CH-{n}` | `CH-3` |
| Section | `SEC-{n}-{m}` | `SEC-3-1` |
| Clause | `CL-{n}-{m}-{k}` | `CL-3-1-2` |
| Sub-clause | `{clause_id}-SC{n}` | `CL-3-1-2-SC1` |
| Table | `TBL-{n}` | `TBL-4` |
| Equation | `EQ-{n}` | `EQ-2` |
| Figure | `FIG-{n}-{m}` | `FIG-2-1` |

---

## Key Patterns & Conventions
- **Env secrets**: always via `load_dotenv()` + `os.getenv()` — never hardcoded
- **Claude model**: `claude-sonnet-4-20250514` (set in `ai_enhancer.py:ask_claude`)
- **Document cache**: `api/main.py` caches `_document_cache` and `_search_index_cache` as module globals
- **Fallback parsing**: if Datalab returns only markdown (no structured blocks), `_parse_markdown_fallback` handles it
- **Storage is file-based** — noted in `document_store.py` as swap candidate for PostgreSQL/SQLite

---

## How to Run
```bash
# 1. Process a PDF
python main.py path/to/building_code.pdf
python main.py path/to/building_code.pdf --ai   # with Claude enhancement

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
