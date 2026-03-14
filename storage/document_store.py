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

    Returns:
        List of dicts: [{"id": "CL-3-1-2", "text": "...", "breadcrumb": "Ch3 > 3.1 > 3.1.2"}, ...]
    """
    index = []

    for chapter in document_dict.get("chapters", []):
        ch_label = f"Chapter {chapter['number']}"

        for section in chapter.get("sections", []):
            sec_label = f"{ch_label} > {section['number']}"

            for clause in section.get("clauses", []):
                cl_label = f"{sec_label} > {clause['number']}"

                # Index the clause itself
                index.append({
                    "id": clause["id"],
                    "type": "clause",
                    "number": clause["number"],
                    "title": clause.get("title", ""),
                    "text": clause.get("text", ""),
                    "breadcrumb": cl_label,
                    "page": clause.get("page_span", [0])[0],
                })

                # Index sub-clauses
                for sc in clause.get("sub_clauses", []):
                    index.append({
                        "id": sc["id"],
                        "type": "sub_clause",
                        "number": sc.get("marker", ""),
                        "title": "",
                        "text": sc.get("text", ""),
                        "breadcrumb": cl_label,
                        "page": clause.get("page_span", [0])[0],
                    })

    return index