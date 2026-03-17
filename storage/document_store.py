"""
storage/document_store.py
==========================
Handles saving and loading the structured document JSON.

We use a simple JSON file for the prototype.
This can be swapped for a database (PostgreSQL, SQLite) in later stages.
"""

import os
import json
from pathlib import Path

OUTPUT_DIR = Path("storage/output")


def save_document(document_dict: dict, filename: str = "structured_document.json") -> str:
    """
    Save the structured document dict to a JSON file.

    Args:
        document_dict: The fully structured and linked document
        filename:      Output filename (inside storage/output/)

    Returns:
        Full path to the saved file
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename

    with open(path, "w", encoding="utf-8") as f:
        json.dump(document_dict, f, indent=2, ensure_ascii=False)

    size_kb = path.stat().st_size / 1024
    print(f"[Storage] Document saved to: {path}  ({size_kb:.1f} KB)")
    return str(path)


def load_document(filename: str = "structured_document.json") -> dict:
    """
    Load a previously saved structured document.

    Args:
        filename: JSON file inside storage/output/

    Returns:
        The document dict
    """
    path = OUTPUT_DIR / filename

    if not path.exists():
        raise FileNotFoundError(
            f"Document not found at {path}.\n"
            "Run main.py first to process a PDF."
        )

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_search_index(document_dict: dict) -> list:
    """
    Build a flat list of all searchable text entries from the document.
    Used by the FastAPI /search endpoint.

    FIX (Bug 1): The old version read clause.get("text", "") which does not
    exist in the data model — all content lives in the ordered content[] array.
    It also iterated clause.get("sub_clauses", []) which no longer exists as a
    separate list (sub-clauses are content[] items with type="sub_clause").

    This version:
      - Concatenates all content[] item values and latex strings into a single
        searchable text string per clause.
      - Extracts a meaningful snippet from the first text/sub_clause item.
      - Sub-clauses are included in the parent clause's content text, so they
        do not need separate index entries.

    Returns:
        List of dicts: [{"id": "CL-4-1-2-1", "text": "...", "breadcrumb": "..."}, ...]
    """
    index = []

    for chapter in document_dict.get("chapters", []):
        ch_label = f"Chapter {chapter['number']}"

        for section in chapter.get("sections", []):
            sec_label = f"{ch_label} > {section['number']}"

            for clause in section.get("clauses", []):
                cl_label = f"{sec_label} > {clause['number']}"

                # Build a single searchable text string from all content[] items.
                # Includes text values, sub_clause values, and equation LaTeX.
                content_parts = []
                for item in clause.get("content", []):
                    itype = item.get("type", "")
                    if itype in ("text", "sub_clause"):
                        v = item.get("value", "").strip()
                        if v:
                            content_parts.append(v)
                    elif itype == "equation":
                        latex = item.get("latex", "").strip()
                        if latex:
                            content_parts.append(latex)
                    elif itype == "figure":
                        # Include caption and alt text for figure search
                        cap = item.get("caption", "").strip()
                        alt = item.get("alt_text", "").strip()
                        if cap:
                            content_parts.append(cap)
                        elif alt:
                            content_parts.append(alt[:120])
                    elif itype == "table":
                        # value holds the caption text for table content items
                        cap = item.get("value", "").strip()
                        if cap:
                            content_parts.append(cap)

                full_text = " ".join(content_parts)

                # Extract a short human-readable snippet from the first text item
                snippet = ""
                for item in clause.get("content", []):
                    if item.get("type") in ("text", "sub_clause"):
                        snippet = item.get("value", "").strip()
                        if snippet:
                            break

                index.append({
                    "id":         clause["id"],
                    "type":       "clause",
                    "number":     clause.get("number", ""),
                    "title":      clause.get("title", ""),
                    "text":       full_text,
                    "snippet":    snippet[:200],
                    "breadcrumb": cl_label,
                    "page":       clause.get("page_span", [0])[0],
                })

    return index