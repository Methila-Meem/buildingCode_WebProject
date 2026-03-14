"""
parser/reference_linker.py
===========================
Scans every clause's text for internal references like:
  "see Section 4.2.1", "refer to Table 3-1", "as per Clause 2.3"

Then resolves those references against the ID index built from the
structured document, attaching hyperlink targets to each clause.

After this step runs, every clause has a "references" list like:
  [
    {"text": "Section 4.2.1", "target_id": "SEC-4-2-1", "resolved": True},
    {"text": "Table 3",        "target_id": "TBL-3",     "resolved": True},
  ]
"""

import re
from typing import Optional

# -------------------------------------------------------
# Patterns for detecting references in clause text.
# Each pattern must have a named group called 'ref'.
# -------------------------------------------------------
REFERENCE_PATTERNS = [
    # "Section 4.2.1" / "Clause 3.1.2" / "Article 5.4"
    re.compile(
        r'(?:section|clause|article)\s+(?P<ref>\d+(?:\.\d+)*)',
        re.IGNORECASE
    ),
    # "Table 3" / "Table 3-1"
    re.compile(
        r'table\s+(?P<ref>\d+(?:[-–]\d+)?)',
        re.IGNORECASE
    ),
    # "Figure 2" / "Figure 2.1"
    re.compile(
        r'figure\s+(?P<ref>\d+(?:\.\d+)?)',
        re.IGNORECASE
    ),
]


def build_id_index(document_dict: dict) -> dict:
    """
    Walk the document tree and build a flat lookup dict:
      { "SEC-3-1": True, "CL-3-1-2": True, "TBL-1": True, ... }

    This lets us quickly check whether a detected reference target
    actually exists in the document.

    Args:
        document_dict: The dict output from structure_parser.py

    Returns:
        A set-like dict of all known node IDs
    """
    index = {}

    for chapter in document_dict.get("chapters", []):
        index[chapter["id"]] = chapter

        for section in chapter.get("sections", []):
            index[section["id"]] = section

            for clause in section.get("clauses", []):
                index[clause["id"]] = clause

                for table in clause.get("tables", []):
                    index[table["id"]] = table

                for eq in clause.get("equations", []):
                    index[eq["id"]] = eq

    return index


def _ref_text_to_id(ref_text: str, ref_kind: str) -> Optional[str]:
    """
    Convert a detected reference string to the ID format used in the document.

    Examples:
      ref_text="4.2.1", ref_kind="section"  → "SEC-4-2-1"
      ref_text="4.2.1", ref_kind="clause"   → "CL-4-2-1"
      ref_text="3",     ref_kind="table"    → "TBL-3"
      ref_text="2.1",   ref_kind="figure"   → "FIG-2-1"
    """
    normalized = ref_text.replace(".", "-").replace("–", "-")

    kind_lower = ref_kind.lower()
    if kind_lower in ("section", "article"):
        parts = ref_text.split(".")
        if len(parts) >= 3:
            return f"CL-{normalized}"
        elif len(parts) == 2:
            return f"SEC-{normalized}"
        else:
            return f"CH-{normalized}"
    elif kind_lower == "clause":
        return f"CL-{normalized}"
    elif kind_lower == "table":
        return f"TBL-{normalized}"
    elif kind_lower == "figure":
        return f"FIG-{normalized}"
    return None


def _extract_references_from_text(text: str) -> list:
    """
    Scan a single text string for all reference patterns.

    Returns a list of dicts:
      [{"raw_match": "Section 4.2.1", "ref": "4.2.1", "kind": "section"}, ...]
    """
    found = []
    seen = set()  # avoid duplicates from overlapping patterns

    for pattern in REFERENCE_PATTERNS:
        # Determine the "kind" from the pattern (section/table/figure)
        for match in pattern.finditer(text):
            raw = match.group(0)
            ref = match.group("ref")
            kind = raw.split()[0].lower()  # first word = "section", "table", etc.

            key = (kind, ref)
            if key not in seen:
                seen.add(key)
                found.append({"raw_match": raw, "ref": ref, "kind": kind})

    return found


def link_references(document_dict: dict) -> dict:
    """
    Main function: walk all clauses, detect references in their text,
    resolve each one against the ID index, and attach the result.

    Args:
        document_dict: Structured document dict (output of structure_parser)

    Returns:
        The same dict with "references" arrays filled in on every clause.
        Also returns stats about resolution rate.
    """
    id_index = build_id_index(document_dict)

    total_refs = 0
    resolved_refs = 0

    for chapter in document_dict.get("chapters", []):
        for section in chapter.get("sections", []):
            for clause in section.get("clauses", []):

                full_text = clause.get("text", "") + " " + clause.get("title", "")
                detected = _extract_references_from_text(full_text)

                linked = []
                for det in detected:
                    total_refs += 1
                    target_id = _ref_text_to_id(det["ref"], det["kind"])
                    resolved = target_id in id_index if target_id else False

                    if resolved:
                        resolved_refs += 1

                    linked.append({
                        "text": det["raw_match"],      # display text, e.g. "Section 4.2.1"
                        "target_id": target_id,        # e.g. "SEC-4-2-1"
                        "resolved": resolved,          # True if the target exists in this doc
                    })

                clause["references"] = linked

    resolution_rate = (resolved_refs / total_refs * 100) if total_refs > 0 else 0
    print(
        f"[References] Found {total_refs} references, "
        f"{resolved_refs} resolved ({resolution_rate:.1f}%)"
    )

    document_dict["_stats"] = {
        "total_references": total_refs,
        "resolved_references": resolved_refs,
        "resolution_rate_pct": round(resolution_rate, 1),
    }

    return document_dict