"""
ingestion/datalab_client.py
============================
Handles submitting a PDF to the Datalab Marker API and
retrieving the structured extraction result.

CACHING:
    Raw extraction results are cached per PDF filename.
    If storage/raw_{pdf_name}.json already exists, the Datalab API
    is NOT called and the cached file is loaded instead.

    This avoids re-paying API costs every time you fix the parser.
    The cache is tied to the PDF filename so different PDFs never
    share each other's cached results.

    To force a fresh extraction (e.g. after uploading a new PDF version):
        python main.py your_file.pdf --force-extract

How Datalab Marker works:
    - POST the PDF -> get a request_check_url
    - Poll that URL until status == "complete"
    - The result contains structured JSON blocks
"""

import os
import time
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DATALAB_API_KEY = os.getenv("DATALAB_API_KEY")
MARKER_ENDPOINT = "https://www.datalab.to/api/v1/marker"
RAW_CACHE_DIR   = Path("storage")


# ============================================================
# Cache helpers
# ============================================================

def _cache_path(pdf_path: str) -> Path:
    """
    Build the cache file path for a given PDF.

    Examples:
        "bcbc_2024_sample.pdf"   -> storage/raw_bcbc_2024_sample.json
        "docs/building_code.pdf" -> storage/raw_building_code.json

    Using the PDF stem (name without extension) means:
        - Different PDFs never share cached results
        - Re-running the same PDF always hits the same cache file
    """
    pdf_stem  = Path(pdf_path).stem
    safe_name = pdf_stem.replace(" ", "_")
    return RAW_CACHE_DIR / f"raw_{safe_name}.json"


def load_cached(pdf_path: str):
    """
    Load cached raw extraction result for a PDF if it exists.
    Returns the dict, or None if no cache found.
    """
    path = _cache_path(pdf_path)
    if path.exists():
        print(f"[Cache] Found cached extraction: {path}")
        print(f"[Cache] Skipping Datalab API call.")
        print(f"[Cache] To re-extract, run:  python main.py {pdf_path} --force-extract")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_cache(pdf_path: str, result: dict):
    """Save raw Datalab result to the PDF-specific cache file."""
    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(pdf_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    size_kb = path.stat().st_size / 1024
    print(f"[Cache] Raw output saved: {path}  ({size_kb:.1f} KB)")


# ============================================================
# API helpers
# ============================================================

def validate_api_key():
    """Raise a clear error if the API key is missing or placeholder."""
    if not DATALAB_API_KEY or DATALAB_API_KEY == "your_datalab_api_key_here":
        raise EnvironmentError(
            "\n\n[ERROR] DATALAB_API_KEY is not set.\n"
            "Steps to fix:\n"
            "  1. Open your .env file\n"
            "  2. Set DATALAB_API_KEY=your_actual_key\n"
            "  3. Get a key at https://www.datalab.to/app/keys\n"
        )


def submit_pdf(pdf_path: str) -> str:
    """Submit PDF to Datalab and return the polling URL."""
    validate_api_key()

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    print(f"[Datalab] Submitting PDF: {pdf_path}")

    with open(pdf_path, "rb") as f:
        response = requests.post(
            MARKER_ENDPOINT,
            files={"file": (os.path.basename(pdf_path), f, "application/pdf")},
            data={
                "output_format": "json",
                "use_llm": "true",
                "extract_images": "false",
            },
            headers={"X-API-Key": DATALAB_API_KEY},
            timeout=60,
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"[Datalab] Submission failed.\n"
            f"Status: {response.status_code}\n"
            f"Response: {response.text}"
        )

    result = response.json()
    check_url = result.get("request_check_url")
    if not check_url:
        raise RuntimeError(f"[Datalab] No check URL returned. Response: {result}")

    print(f"[Datalab] Job submitted. Polling: {check_url}")
    return check_url


def poll_for_result(check_url: str, poll_interval: int = 5, max_wait: int = 300) -> dict:
    """Poll Datalab until the job is complete."""
    headers = {"X-API-Key": DATALAB_API_KEY}
    elapsed = 0

    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        response = requests.get(check_url, headers=headers, timeout=30)
        if response.status_code != 200:
            print(f"[Datalab] Poll error {response.status_code}, retrying...")
            continue

        data   = response.json()
        status = data.get("status", "unknown")
        print(f"[Datalab] Status: {status} ({elapsed}s elapsed)")

        if status == "complete":
            print("[Datalab] Extraction complete!")
            return data
        if status == "error":
            raise RuntimeError(f"[Datalab] Job failed: {data.get('error', 'Unknown error')}")

    raise TimeoutError(f"[Datalab] Job did not complete within {max_wait} seconds.")


# ============================================================
# Main entry point
# ============================================================

def extract_pdf(pdf_path: str, force_extract: bool = False) -> dict:
    """
    Extract a PDF via Datalab, using cache when available.

    Args:
        pdf_path:       Path to the PDF file
        force_extract:  If True, skip cache and always call Datalab API.
                        Use when you upload a new version of the same PDF.

    Returns:
        Datalab JSON result dict

    Cache behaviour:
        First run   -> calls Datalab API, saves storage/raw_{name}.json
        Later runs  -> loads from cache, zero API cost
        --force-extract -> always calls API, overwrites cache
    """
    if not force_extract:
        cached = load_cached(pdf_path)
        if cached is not None:
            return cached

    # No cache or forced re-extract
    print(f"[Datalab] No cache for '{Path(pdf_path).name}'. Calling API...")
    check_url = submit_pdf(pdf_path)
    result    = poll_for_result(check_url)
    save_cache(pdf_path, result)
    return result


# ============================================================
# Direct CLI test
# ============================================================
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ingestion/datalab_client.py path/to/your.pdf [--force]")
        sys.exit(1)
    force  = "--force" in sys.argv
    result = extract_pdf(sys.argv[1], force_extract=force)
    print(f"\n[Done] Keys: {list(result.keys())}")
    print(f"[Done] Pages: {result.get('page_count')}")