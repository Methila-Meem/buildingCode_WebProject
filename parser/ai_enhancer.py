"""
parser/ai_enhancer.py
======================
Uses the Claude API to improve parsing quality for:
  1. Ambiguous clause boundaries (is this a new clause or continuation?)
  2. Table column semantics (labeling what each column means)
  3. Cross-page list continuity (should these fragments be joined?)
  4. Unresolved references (try to infer what "the above table" means)

Claude is called ONLY when the rule-based parser is uncertain —
not for every block. This keeps API costs low.

How the Anthropic API works:
  - We send a "messages" array with a user message
  - Claude returns a structured response
  - We parse the JSON from Claude's reply
"""

import os
import json
import anthropic
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def get_claude_client():
    """
    Create and return an Anthropic client.

    The client reads ANTHROPIC_API_KEY from the environment automatically.
    If the key is missing, it raises a clear error.
    """
    if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY == "your_anthropic_api_key_here":
        raise EnvironmentError(
            "\n\n[ERROR] ANTHROPIC_API_KEY is not set.\n"
            "Steps to fix:\n"
            "  1. Open your .env file\n"
            "  2. Set ANTHROPIC_API_KEY=your_actual_key\n"
            "  3. Get a key at https://console.anthropic.com → API Keys\n"
        )
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def ask_claude(prompt: str, max_tokens: int = 1024) -> str:
    """
    Send a prompt to Claude and return the text response.

    This is the base function all other helpers use.
    """
    client = get_claude_client()

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    return message.content[0].text


def classify_block(text: str, context_before: str = "", context_after: str = "") -> dict:
    """
    Ask Claude whether a text block is:
      - A new clause heading
      - A continuation of the previous clause
      - A standalone paragraph
      - A list item

    Args:
        text:           The block text to classify
        context_before: A few lines from just before this block
        context_after:  A few lines from just after this block

    Returns:
        {"type": "clause|continuation|paragraph|list_item", "confidence": 0.0-1.0, "reason": "..."}
    """
    prompt = f"""You are parsing a building code document. Classify the following text block.

Context before:
{context_before or "(start of document)"}

Text to classify:
{text}

Context after:
{context_after or "(end of document)"}

Reply ONLY with a JSON object in this exact format (no markdown, no explanation):
{{
  "type": "clause|continuation|paragraph|list_item",
  "confidence": 0.95,
  "reason": "brief explanation"
}}"""

    try:
        raw = ask_claude(prompt, max_tokens=200)
        # Strip any accidental markdown fences
        raw = raw.strip().strip("```json").strip("```").strip()
        return json.loads(raw)
    except Exception as e:
        return {"type": "paragraph", "confidence": 0.5, "reason": f"Parse error: {e}"}


def should_join_fragments(fragment_end: str, fragment_start: str) -> bool:
    """
    Decide if two text fragments from adjacent pages should be joined
    into a single continuous list/clause.

    Returns True if they should be joined, False if they are separate.
    """
    prompt = f"""You are parsing a building code document that spans multiple pages.

End of page N:
{fragment_end}

Start of page N+1:
{fragment_start}

Do these two fragments form one continuous passage (a list or clause that was split across a page break)?
Reply ONLY with: yes or no"""

    try:
        answer = ask_claude(prompt, max_tokens=10).strip().lower()
        return answer.startswith("yes")
    except Exception:
        return False


def label_table_columns(headers: list, sample_rows: list) -> dict:
    """
    Ask Claude to infer the semantic meaning of each table column.
    Useful for tables in building codes (loads, dimensions, factors, etc.)

    Args:
        headers:     List of raw header strings, e.g. ["Col 1", "Col 2", "Col 3"]
        sample_rows: First 3 rows of data as list of lists

    Returns:
        {"columns": [{"original": "Col 1", "semantic": "Load Type (kN/m²)"}, ...]}
    """
    prompt = f"""You are reading a table from a building code document.

Column headers: {headers}
Sample data rows:
{json.dumps(sample_rows[:3], indent=2)}

For each column, provide a clear semantic label describing what the column contains.
Reply ONLY with JSON in this format:
{{
  "columns": [
    {{"original": "Col 1", "semantic": "Descriptive label"}},
    ...
  ]
}}"""

    try:
        raw = ask_claude(prompt, max_tokens=400)
        raw = raw.strip().strip("```json").strip("```").strip()
        return json.loads(raw)
    except Exception:
        return {"columns": [{"original": h, "semantic": h} for h in headers]}


def resolve_ambiguous_reference(ref_text: str, clause_text: str, nearby_ids: list) -> dict:
    """
    When a reference like "see the above table" or "as noted previously"
    can't be resolved by regex, ask Claude to infer the target.

    Args:
        ref_text:    The ambiguous reference string
        clause_text: Full text of the clause containing the reference
        nearby_ids:  List of nearby node IDs that could be the target

    Returns:
        {"target_id": "TBL-3", "confidence": 0.8} or {"target_id": None, "confidence": 0.0}
    """
    prompt = f"""You are resolving a cross-reference in a building code document.

Ambiguous reference: "{ref_text}"
Found in clause text: "{clause_text[:300]}"
Nearby document nodes: {nearby_ids}

Which node ID is most likely the target of this reference?
Reply ONLY with JSON:
{{
  "target_id": "NODE-ID-or-null",
  "confidence": 0.85
}}"""

    try:
        raw = ask_claude(prompt, max_tokens=100)
        raw = raw.strip().strip("```json").strip("```").strip()
        return json.loads(raw)
    except Exception:
        return {"target_id": None, "confidence": 0.0}


def enhance_document(document_dict: dict, use_ai_for_tables: bool = True) -> dict:
    """
    Optional post-processing pass using Claude.
    Run this after structure_parser.py and reference_linker.py.

    Currently enhances:
      - Table column semantic labels (if use_ai_for_tables=True)

    Args:
        document_dict:      Structured document dict
        use_ai_for_tables:  Whether to call Claude for table labeling

    Returns:
        Enhanced document dict
    """
    ai_calls = 0

    for chapter in document_dict.get("chapters", []):
        for section in chapter.get("sections", []):
            for clause in section.get("clauses", []):
                for table in clause.get("tables", []):

                    if use_ai_for_tables and table.get("headers") and table.get("rows"):
                        print(f"[AI] Labeling table {table['id']}...")
                        labeling = label_table_columns(table["headers"], table["rows"])
                        table["column_semantics"] = labeling.get("columns", [])
                        ai_calls += 1

    print(f"[AI] Enhancement complete. Made {ai_calls} Claude API calls.")
    return document_dict