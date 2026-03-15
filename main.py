"""
main.py - Master pipeline. Run this to process a PDF end to end.

Usage:
    python main.py path/to/your_building_code.pdf
    python main.py path/to/your_building_code.pdf --force-extract
    python main.py path/to/your_building_code.pdf --ai
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ingestion.datalab_client import extract_pdf
from parser.structure_parser import parse_datalab_output
from parser.reference_linker import link_references
from parser.ai_enhancer import enhance_document
from storage.document_store import save_document


def run_pipeline(pdf_path: str, force_extract: bool = False, use_ai_enhancement: bool = False):
    print("=" * 60)
    print("  Building Code Extraction Pipeline")
    print("=" * 60)
    print(f"  PDF:            {pdf_path}")
    print(f"  Force extract:  {'YES - will call Datalab API' if force_extract else 'NO  - will use cache if available'}")
    print(f"  AI Enhancement: {'ON' if use_ai_enhancement else 'OFF'}")
    print("=" * 60)

    print("\n[Step 1/4] Extracting PDF...")
    datalab_result = extract_pdf(pdf_path, force_extract=force_extract)
    print(f"  Pages: {datalab_result.get('page_count', '?')}")

    print("\n[Step 2/4] Parsing document structure...")
    pdf_filename  = os.path.basename(pdf_path)
    document_dict = parse_datalab_output(datalab_result, source_pdf=pdf_filename)

    chapters         = document_dict.get("chapters", [])
    total_sections   = sum(len(ch.get("sections", [])) for ch in chapters)
    total_clauses    = sum(
        len(sec.get("clauses", []))
        for ch in chapters
        for sec in ch.get("sections", [])
    )
    total_subclauses = sum(
        len(cl.get("sub_clauses", []))
        for ch in chapters
        for sec in ch.get("sections", [])
        for cl in sec.get("clauses", [])
    )
    total_tables = sum(
        len(cl.get("tables", []))
        for ch in chapters
        for sec in ch.get("sections", [])
        for cl in sec.get("clauses", [])
    )
    print(f"  Chapters:    {len(chapters)}")
    print(f"  Sections:    {total_sections}")
    print(f"  Clauses:     {total_clauses}")
    print(f"  Sub-clauses: {total_subclauses}")
    print(f"  Tables:      {total_tables}")

    print("\n[Step 3/4] Linking internal references...")
    document_dict = link_references(document_dict)
    stats = document_dict.get("_stats", {})
    print(f"  {stats.get('resolved_references')}/{stats.get('total_references')} "
          f"resolved ({stats.get('resolution_rate_pct')}%)")

    if use_ai_enhancement:
        print("\n[Step 4/4] AI enhancement with Claude...")
        document_dict = enhance_document(document_dict, use_ai_for_tables=True)
    else:
        print("\n[Step 4/4] Skipping AI enhancement (pass --ai to enable)")

    output_path = save_document(document_dict)

    print("\n" + "=" * 60)
    print("  EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"  Document:    {document_dict.get('title', 'Unknown')}")
    print(f"  Pages:       {document_dict.get('total_pages', '?')}")
    print(f"  Chapters:    {len(chapters)}")
    print(f"  Sections:    {total_sections}")
    print(f"  Clauses:     {total_clauses}")
    print(f"  Sub-clauses: {total_subclauses}")
    print(f"  Tables:      {total_tables}")
    print(f"  References:  {stats.get('total_references', 0)} found, "
          f"{stats.get('resolution_rate_pct', 0)}% resolved")
    print(f"  Output:      {output_path}")
    print("=" * 60)
    print("\nNext step:")
    print("  streamlit run viewer_streamlit.py")
    print("  Then open: http://localhost:8501")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Building Code PDF Extraction Pipeline")
    parser.add_argument("pdf", help="Path to the PDF file")
    parser.add_argument(
        "--force-extract",
        action="store_true",
        help="Skip cache, re-extract from Datalab API (use when PDF changes)"
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        help="Enable Claude AI enhancement (uses Anthropic API credits)"
    )
    args = parser.parse_args()
    run_pipeline(args.pdf, force_extract=args.force_extract, use_ai_enhancement=args.ai)