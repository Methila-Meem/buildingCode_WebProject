"""
parser/reference_linker.py
===========================
Scans every clause's content[] items for internal cross-references and
resolves them against the document ID index.

Two types of references handled:

1. Standard references (Sentence/Article/Section/Table/Figure):
   Sentence 4.1.6.5.(1)   -> CL-4-1-6-5
   Article 4.1.6.5.        -> CL-4-1-6-5
   Subsection 4.1.6.       -> SEC-4-1-6  (always SEC-)
   Section 4.1.            -> SEC-4-1
   Table 4.1.3.2.-A        -> TBL-N (caption lookup)
   Figure 4.1.6.5.-A       -> FIG-N (caption lookup)

2. Appendix note references (See Note A-...):
   (See Note A-4.1.3.2.(2).)  -> CL-AUTO-39  (if appendix is in this PDF)
   (See Note A-4.1.6.1.(1).)  -> None         (external - in a different PDF)

   Note references that resolve navigate to the appendix clause.
   Note references that don't resolve are still detected and displayed
   as styled badges so users know they exist.
"""

import re
from typing import Optional, List

# ─────────────────────────────────────────────────────────────────────────────
# Standard cross-reference patterns
# ─────────────────────────────────────────────────────────────────────────────
REFERENCE_PATTERNS = [
    re.compile(
        r'(?P<kind>Sentence|Article|Subsection|Section|Clause)\s+'
        r'(?P<ref>\d+(?:\.\d+)*(?:\.\d+)?(?:\([^)]+\))?)',
        re.IGNORECASE
    ),
    re.compile(
        r'(?P<kind>Table)\s+(?P<ref>[\d\.]+[\w\.\-]*)',
        re.IGNORECASE
    ),
    re.compile(
        r'(?P<kind>Figure)\s+(?P<ref>[\d\.]+[\w\.\-]*)',
        re.IGNORECASE
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# Appendix note reference pattern
# Matches: "(See Note A-4.1.3.2.(2).)" and "See Note A-Table 4.1.2.1."
# Group 1 captures the full A- identifier including optional sentence number
# ─────────────────────────────────────────────────────────────────────────────
RE_NOTE = re.compile(
    r'See Note\s+(A-(?:Table\s+)?[\d\.]+(?:\.\(\d+\))?(?:\s+and\s+\(\d+\))*\.?)',
    re.IGNORECASE
)


# ─────────────────────────────────────────────────────────────────────────────
# Index builders
# ─────────────────────────────────────────────────────────────────────────────

def build_id_index(document_dict: dict) -> dict:
    """
    Build flat {id -> node} lookup from the full document tree.
    Also builds caption-based lookups for Tables and Figures.
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
                    cap = table.get("caption", "")
                    if cap:
                        index[f"_cap_{cap}"] = table

                for figure in clause.get("figures", []):
                    index[figure["id"]] = figure
                    cap = figure.get("caption", "")
                    if cap:
                        index[f"_cap_{cap}"] = figure

    return index


def build_note_index(document_dict: dict) -> dict:
    """
    Build a note reference -> [clause_id, ...] lookup.

    Scans all CL-AUTO-N clauses whose titles start with 'A-'.
    These are the appendix commentary sections included in this PDF.

    Returns dict mapping note key -> list of matching clause IDs.
    e.g. {'A-4.1.3.2': ['CL-AUTO-39', 'CL-AUTO-40'], ...}

    Notes that are NOT in this dict are external references (in other PDFs).
    """
    note_idx = {}

    for chapter in document_dict.get("chapters", []):
        for section in chapter.get("sections", []):
            for clause in section.get("clauses", []):
                cid   = clause.get("id", "")
                title = clause.get("title", "")

                if not (cid.startswith("CL-AUTO") and title.startswith("A-")):
                    continue

                # Extract the A- identifier from the title
                # Handles: "A-4.1.3.2.(2) Load..."  "A-Table 4.1.2.1. Importance..."
                m = re.match(
                    r'(A-(?:Table\s+)?[\d\.]+(?:\.\(\d+\))?(?:\s+and\s+\(\d+\))?)',
                    title
                )
                if m:
                    key = m.group(1).strip().rstrip('.')
                    note_idx.setdefault(key, []).append(cid)

    return note_idx


# ─────────────────────────────────────────────────────────────────────────────
# Reference resolution helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ref_to_id(ref: str, kind: str, id_index: dict) -> Optional[str]:
    """
    Convert a standard reference string to a document node ID.

    Subsection always maps to SEC- regardless of the number of dot-parts.
    "4.1.4" has 3 parts but is still a Subsection (SEC-4-1-4), not a Clause.
    """
    kind_lower = kind.lower()
    normalized = re.sub(r'[.\-]', '-', ref).strip('-')

    if kind_lower in ("sentence", "article", "clause"):
        return f"CL-{normalized}"

    if kind_lower in ("subsection", "section"):
        return f"SEC-{normalized}"

    if kind_lower == "table":
        for key in id_index:
            if key.startswith("_cap_") and ref.lower() in key.lower():
                return id_index[key].get("id", "")
        return f"TBL-{normalized}"

    if kind_lower == "figure":
        for key in id_index:
            if key.startswith("_cap_") and ref.lower() in key.lower():
                return id_index[key].get("id", "")
        return f"FIG-{normalized}"

    return None


def _resolve_note(note_ref: str, note_index: dict) -> List[str]:
    """
    Resolve a note reference string to a list of clause IDs.

    Tries exact match first, then base match (without sentence number).

    e.g. "A-4.1.3.2.(2)" -> tries "A-4.1.3.2.(2)" then "A-4.1.3.2"
         "A-4.1.7.5."    -> tries "A-4.1.7.5." then "A-4.1.7.5"

    Returns [] if the note is not in this PDF (external reference).
    """
    clean = note_ref.strip().rstrip('.')

    # Exact match
    if clean in note_index:
        return note_index[clean]

    # Match without sentence number: "A-4.1.3.2.(2)" -> "A-4.1.3.2"
    base = re.sub(r'\.\(\d+\).*$', '', clean)
    if base in note_index:
        return note_index[base]

    return []


def _extract_refs_from_text(text: str) -> list:
    """Scan a text string for all standard reference patterns."""
    found = []
    seen  = set()
    for pattern in REFERENCE_PATTERNS:
        for m in pattern.finditer(text):
            raw  = m.group(0)
            ref  = m.group("ref")
            kind = m.group("kind")
            key  = (kind.lower(), ref)
            if key not in seen:
                seen.add(key)
                found.append({"raw": raw, "ref": ref, "kind": kind})
    return found


def _extract_notes_from_text(text: str) -> list:
    """
    Scan a text string for all (See Note A-...) references.

    Returns list of dicts:
        [{"raw": "(See Note A-4.1.3.2.(2).)", "note_ref": "A-4.1.3.2.(2)."}, ...]
    """
    found = []
    seen  = set()
    for m in RE_NOTE.finditer(text):
        note_ref = m.group(1).strip()
        if note_ref not in seen:
            seen.add(note_ref)
            found.append({
                "raw":      m.group(0),
                "note_ref": note_ref,
            })
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def link_references(document_dict: dict) -> dict:
    """
    Walk all clauses, detect both standard references and note references,
    resolve each, and attach to clause.references[] and clause.note_refs[].

    Standard refs -> clause.references[]
      [{"text": "Article 4.1.6.5.", "kind": "Article",
        "target_id": "CL-4-1-6-5", "resolved": True}, ...]

    Note refs -> clause.note_refs[]
      [{"raw": "(See Note A-4.1.3.2.(2).)", "note_ref": "A-4.1.3.2.(2).",
        "target_ids": ["CL-AUTO-39", "CL-AUTO-40"], "resolved": True}, ...]

    Items in clause.note_refs[]:
      resolved=True  -> target_ids contains one or more CL-AUTO IDs (clickable)
      resolved=False -> external appendix note (styled badge, not clickable)
    """
    id_index   = build_id_index(document_dict)
    note_index = build_note_index(document_dict)

    total_refs    = resolved_refs    = 0
    total_notes   = resolved_notes   = 0

    for chapter in document_dict.get("chapters", []):
        for section in chapter.get("sections", []):
            for clause in section.get("clauses", []):

                texts = [clause.get("title", "")]
                for item in clause.get("content", []):
                    if item.get("type") in ("text", "sub_clause"):
                        texts.append(item.get("value", ""))

                # ── Standard references ───────────────────────────────────
                linked     = []
                seen_refs  = set()
                for text in texts:
                    for det in _extract_refs_from_text(text):
                        key = (det["kind"].lower(), det["ref"])
                        if key in seen_refs:
                            continue
                        seen_refs.add(key)
                        total_refs += 1
                        target_id  = _ref_to_id(det["ref"], det["kind"], id_index)
                        is_resolved = bool(target_id and target_id in id_index)
                        if is_resolved:
                            resolved_refs += 1
                        linked.append({
                            "text":      det["raw"],
                            "kind":      det["kind"],
                            "target_id": target_id,
                            "resolved":  is_resolved,
                        })
                clause["references"] = linked

                # ── Note references ───────────────────────────────────────
                note_linked = []
                seen_notes  = set()
                for text in texts:
                    for det in _extract_notes_from_text(text):
                        nr = det["note_ref"]
                        if nr in seen_notes:
                            continue
                        seen_notes.add(nr)
                        total_notes += 1
                        target_ids  = _resolve_note(nr, note_index)
                        is_resolved = len(target_ids) > 0
                        if is_resolved:
                            resolved_notes += 1
                        note_linked.append({
                            "raw":        det["raw"],
                            "note_ref":   nr,
                            "target_ids": target_ids,
                            "resolved":   is_resolved,
                        })
                clause["note_refs"] = note_linked

    ref_rate  = round(resolved_refs  / total_refs  * 100, 1) if total_refs  else 0.0
    note_rate = round(resolved_notes / total_notes * 100, 1) if total_notes else 0.0

    print(f"[References] {resolved_refs}/{total_refs} resolved ({ref_rate}%)")
    print(f"[Note refs]  {resolved_notes}/{total_notes} resolved ({note_rate}%) "
          f"— unresolved are external appendix notes in other PDFs")

    document_dict["_stats"] = {
        "total_references":        total_refs,
        "resolved_references":     resolved_refs,
        "resolution_rate_pct":     ref_rate,
        "total_note_refs":         total_notes,
        "resolved_note_refs":      resolved_notes,
        "note_resolution_rate_pct": note_rate,
    }
    return document_dict