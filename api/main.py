"""
api/main.py
============
FastAPI backend that serves the structured document to the viewer.

Endpoints:
  GET  /                        Health check
  GET  /document                Full document tree
  GET  /section/{section_id}    Single section with its clauses
  GET  /clause/{clause_id}      Single clause with full detail
  GET  /search?q=term           Full-text search across all clauses
  GET  /references/{node_id}    All clauses that reference a given node

Run with:
  uvicorn api.main:app --reload --port 8000
"""

import sys
import os

# Make sure Python can find our sibling modules (parser/, storage/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from storage.document_store import load_document, build_search_index

app = FastAPI(
    title="Building Code API",
    description="Serves structured building code document data to the viewer.",
    version="1.0.0",
)

# -------------------------------------------------------
# CORS — allow the Streamlit viewer (localhost:8501) to call this API.
# No React frontend is used; Streamlit is the only viewer.
# -------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------
# Load document once at startup (cached in memory)
# -------------------------------------------------------
_document_cache = None
_search_index_cache = None


def get_document() -> dict:
    global _document_cache
    if _document_cache is None:
        _document_cache = load_document()
    return _document_cache


def get_search_index() -> list:
    global _search_index_cache
    if _search_index_cache is None:
        _search_index_cache = build_search_index(get_document())
    return _search_index_cache


# -------------------------------------------------------
# Routes
# -------------------------------------------------------

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Building Code API is running"}


@app.get("/document")
def get_full_document():
    """Return the entire structured document tree."""
    return get_document()


@app.get("/document/summary")
def get_document_summary():
    """Return lightweight summary (no clause text) for navigation sidebar."""
    doc = get_document()
    summary = {
        "title": doc.get("title"),
        "source_pdf": doc.get("source_pdf"),
        "total_pages": doc.get("total_pages"),
        "stats": doc.get("_stats"),
        "chapters": []
    }
    for chapter in doc.get("chapters", []):
        ch_summary = {
            "id": chapter["id"],
            "number": chapter["number"],
            "title": chapter["title"],
            "sections": []
        }
        for section in chapter.get("sections", []):
            sec_summary = {
                "id": section["id"],
                "number": section["number"],
                "title": section["title"],
                "clause_count": len(section.get("clauses", [])),
            }
            ch_summary["sections"].append(sec_summary)
        summary["chapters"].append(ch_summary)
    return summary


@app.get("/section/{section_id}")
def get_section(section_id: str):
    """Return a single section with all its clauses."""
    doc = get_document()
    for chapter in doc.get("chapters", []):
        for section in chapter.get("sections", []):
            if section["id"] == section_id:
                return section
    raise HTTPException(status_code=404, detail=f"Section '{section_id}' not found")


@app.get("/clause/{clause_id}")
def get_clause(clause_id: str):
    """Return a single clause with full detail including sub-clauses and references."""
    doc = get_document()
    for chapter in doc.get("chapters", []):
        for section in chapter.get("sections", []):
            for clause in section.get("clauses", []):
                if clause["id"] == clause_id:
                    return {
                        **clause,
                        "_breadcrumb": {
                            "chapter": {"id": chapter["id"], "title": chapter["title"]},
                            "section": {"id": section["id"], "title": section["title"]},
                        }
                    }
    raise HTTPException(status_code=404, detail=f"Clause '{clause_id}' not found")


@app.get("/search")
def search(q: str = Query(..., min_length=2, description="Search term")):
    """
    Full-text search across all clause titles and content text.
    Returns matching entries with breadcrumb navigation paths and
    a context-aware snippet showing the match in context.

    FIX: The old version built snippets from entry.get("text", "") which was
    always empty under the old build_search_index() schema.  Now that
    build_search_index() correctly populates "text" from content[] items,
    we search that field and generate a proper context snippet around the
    matched term.  We also fall back to the pre-computed "snippet" field
    (first text item of the clause) when the term is found only in the
    title rather than the body.
    """
    term  = q.lower()
    index = get_search_index()

    results = []
    for entry in index:
        title       = entry.get("title", "")
        full_text   = entry.get("text", "")
        haystack    = f"{title} {full_text}".lower()

        if term not in haystack:
            continue

        # Build a context snippet showing the match surrounded by ~80 chars
        snippet = ""
        idx = full_text.lower().find(term)
        if idx >= 0:
            start   = max(0, idx - 60)
            end     = min(len(full_text), idx + len(term) + 100)
            prefix  = "..." if start > 0 else ""
            suffix  = "..." if end < len(full_text) else ""
            snippet = prefix + full_text[start:end] + suffix
        else:
            # Term matched in title only — use the pre-computed first-sentence snippet
            snippet = entry.get("snippet", title)[:200]

        results.append({
            "id":         entry["id"],
            "type":       entry["type"],
            "number":     entry["number"],
            "title":      title,
            "breadcrumb": entry["breadcrumb"],
            "snippet":    snippet,
            "page":       entry.get("page", 0),
        })

    return {
        "query":   q,
        "count":   len(results),
        "results": results[:50],   # cap at 50 for performance
    }


@app.get("/references/{node_id}")
def get_references(node_id: str):
    """
    Return all clauses that contain a reference pointing TO this node_id.
    Useful for "what references this section?" reverse lookup.
    """
    doc = get_document()
    referring_clauses = []

    for chapter in doc.get("chapters", []):
        for section in chapter.get("sections", []):
            for clause in section.get("clauses", []):
                for ref in clause.get("references", []):
                    if ref.get("target_id") == node_id:
                        referring_clauses.append({
                            "clause_id": clause["id"],
                            "clause_number": clause["number"],
                            "clause_title": clause["title"],
                            "reference_text": ref["text"],
                        })

    return {
        "node_id": node_id,
        "referenced_by_count": len(referring_clauses),
        "referenced_by": referring_clauses,
    }