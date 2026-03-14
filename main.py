"""
main.py
========
Master pipeline script — run this to process a PDF end to end.

Usage:
    python main.py path/to/your_building_code.pdf

What it does:
    1. Submits the PDF to Datalab Marker API → gets structured extraction
    2. Parses the extraction into a chapter/section/clause hierarchy
    3. Resolves all internal cross-references
    4. (Optional) Enhances tables using Claude AI
    5. Saves the final structured_document.json
    6. Prints a summary report

After running this:
    - Start the API:    uvicorn api.main:app --reload --port 8000
    - Start the viewer: cd viewer && npm install && npm start
"""

import sys
import os
import json
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ingestion.datalab_client import extract_pdf
from parser.structure_parser import parse_datalab_output
from parser.reference_linker import link_references
from parser.ai_enhancer import enhance_document
from storage.document_store import save_document


def run_pipeline(pdf_path: str, use_ai_enhancement: bool = False):
    """
    Full extraction pipeline for a single PDF.

    Args:
        pdf_path:           Path to the PDF file
        use_ai_enhancement: If True, call Claude to label table columns etc.
                            (uses Anthropic API credits)
    """
    print("=" * 60)
    print("  Building Code Extraction Pipeline")
    print("=" * 60)
    print(f"  PDF: {pdf_path}")
    print(f"  AI Enhancement: {'ON' if use_ai_enhancement else 'OFF'}")
    print("=" * 60)

    # --------------------------------------------------
    # STEP 1: Extract PDF via Datalab Marker API
    # --------------------------------------------------
    print("\n[Step 1/4] Extracting PDF with Datalab Marker...")
    datalab_result = extract_pdf(pdf_path, save_raw=True)
    print(f"  ✓ Extraction complete. Pages: {len(datalab_result.get('pages', []))}")

    # --------------------------------------------------
    # STEP 2: Parse into structured hierarchy
    # --------------------------------------------------
    print("\n[Step 2/4] Parsing document structure...")
    pdf_filename = os.path.basename(pdf_path)
    document_dict = parse_datalab_output(datalab_result, source_pdf=pdf_filename)

    chapters = document_dict.get("chapters", [])
    total_sections = sum(len(ch.get("sections", [])) for ch in chapters)
    total_clauses = sum(
        len(sec.get("clauses", []))
        for ch in chapters
        for sec in ch.get("sections", [])
    )
    print(f"  ✓ Found: {len(chapters)} chapters, {total_sections} sections, {total_clauses} clauses")

    # --------------------------------------------------
    # STEP 3: Resolve internal references
    # --------------------------------------------------
    print("\n[Step 3/4] Linking internal references...")
    document_dict = link_references(document_dict)
    stats = document_dict.get("_stats", {})
    print(f"  ✓ References: {stats.get('resolved_references')}/{stats.get('total_references')} resolved "
          f"({stats.get('resolution_rate_pct')}%)")

    # --------------------------------------------------
    # STEP 4: (Optional) AI enhancement
    # --------------------------------------------------
    if use_ai_enhancement:
        print("\n[Step 4/4] AI enhancement with Claude...")
        document_dict = enhance_document(document_dict, use_ai_for_tables=True)
    else:
        print("\n[Step 4/4] Skipping AI enhancement (pass --ai flag to enable)")

    # --------------------------------------------------
    # Save output
    # --------------------------------------------------
    output_path = save_document(document_dict)

    # --------------------------------------------------
    # Print summary report
    # --------------------------------------------------
    print("\n" + "=" * 60)
    print("  EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"  Document:    {document_dict.get('title', 'Unknown')}")
    print(f"  Pages:       {document_dict.get('total_pages', '?')}")
    print(f"  Chapters:    {len(chapters)}")
    print(f"  Sections:    {total_sections}")
    print(f"  Clauses:     {total_clauses}")
    print(f"  References:  {stats.get('total_references', 0)} found, "
          f"{stats.get('resolution_rate_pct', 0)}% resolved")
    print(f"  Output:      {output_path}")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Start API:    uvicorn api.main:app --reload --port 8000")
    print("  2. Start viewer: cd viewer && npm install && npm start")
    print("  3. Open:         http://localhost:3000")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Building Code PDF Extraction Pipeline")
    parser.add_argument("pdf", help="Path to the PDF file to process")
    parser.add_argument(
        "--ai",
        action="store_true",
        help="Enable Claude AI enhancement (uses Anthropic API credits)"
    )
    args = parser.parse_args()

    run_pipeline(args.pdf, use_ai_enhancement=args.ai)