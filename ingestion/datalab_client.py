"""
ingestion/datalab_client.py
============================
Handles submitting a PDF to the Datalab Marker API and
retrieving the structured extraction result.

How Datalab Marker works:
  - You POST your PDF file to their /marker endpoint
  - It's an async job: the API returns a "request_check_url"
  - You poll that URL until the job status is "complete"
  - The final response contains markdown + JSON structured output

Datalab docs: https://www.datalab.to/docs/marker
"""

import os
import time
import json
import requests
from dotenv import load_dotenv

# -------------------------------------------------------
# Load environment variables from the .env file.
# This reads DATALAB_API_KEY so we don't hardcode secrets.
# -------------------------------------------------------
load_dotenv()

DATALAB_API_KEY = os.getenv("DATALAB_API_KEY")
MARKER_ENDPOINT = "https://www.datalab.to/api/v1/marker"


def validate_api_key():
    """Check that the API key is configured before making any requests."""
    if not DATALAB_API_KEY or DATALAB_API_KEY == "your_datalab_api_key_here":
        raise EnvironmentError(
            "\n\n[ERROR] DATALAB_API_KEY is not set.\n"
            "Steps to fix:\n"
            "  1. Open your .env file\n"
            "  2. Set DATALAB_API_KEY=your_actual_key\n"
            "  3. Get a key at https://www.datalab.to → Dashboard → API Keys\n"
        )


def submit_pdf(pdf_path: str) -> str:
    """
    Submit a PDF file to Datalab Marker for processing.

    Args:
        pdf_path: Full path to your PDF file, e.g. "my_building_code.pdf"

    Returns:
        request_check_url: A URL you can poll to check job progress
    """
    validate_api_key()

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found at path: {pdf_path}")

    print(f"[Datalab] Submitting PDF: {pdf_path}")

    with open(pdf_path, "rb") as f:
        response = requests.post(
            MARKER_ENDPOINT,
            files={"file": (os.path.basename(pdf_path), f, "application/pdf")},
            data={
                "output_format": "json",       # Ask for structured JSON output
                "use_llm": "true",             # Enable LLM-enhanced extraction
                "extract_images": "false",     # Skip image extraction for now
            },
            headers={"X-Api-Key": DATALAB_API_KEY},
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

    print(f"[Datalab] Job submitted. Polling URL: {check_url}")
    return check_url


def poll_for_result(check_url: str, poll_interval: int = 5, max_wait: int = 300) -> dict:
    """
    Poll the Datalab job URL until processing is complete.

    Args:
        check_url:     The URL returned by submit_pdf()
        poll_interval: Seconds to wait between checks (default 5)
        max_wait:      Maximum total seconds to wait (default 300 = 5 minutes)

    Returns:
        The full JSON result from Datalab once status == "complete"
    """
    headers = {"X-Api-Key": DATALAB_API_KEY}
    elapsed = 0

    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        response = requests.get(check_url, headers=headers, timeout=30)

        if response.status_code != 200:
            print(f"[Datalab] Poll error {response.status_code}, retrying...")
            continue

        data = response.json()
        status = data.get("status", "unknown")
        print(f"[Datalab] Status: {status} ({elapsed}s elapsed)")

        if status == "complete":
            print("[Datalab] Extraction complete!")
            return data

        if status == "error":
            raise RuntimeError(f"[Datalab] Job failed: {data.get('error', 'Unknown error')}")

    raise TimeoutError(f"[Datalab] Job did not complete within {max_wait} seconds.")


def extract_pdf(pdf_path: str, save_raw: bool = True) -> dict:
    """
    Full pipeline: submit PDF → poll → return structured result.

    Args:
        pdf_path: Path to your PDF file
        save_raw: If True, saves the raw Datalab output to storage/raw_output.json

    Returns:
        Datalab JSON result containing 'markdown' and 'output' fields
    """
    check_url = submit_pdf(pdf_path)
    result = poll_for_result(check_url)

    if save_raw:
        os.makedirs("storage", exist_ok=True)
        raw_path = "storage/raw_output.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"[Datalab] Raw output saved to: {raw_path}")

    return result


# -------------------------------------------------------
# Run this file directly to test your Datalab connection:
#   python ingestion/datalab_client.py
# -------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python ingestion/datalab_client.py path/to/your.pdf")
        sys.exit(1)

    result = extract_pdf(sys.argv[1])
    print(f"\n[Done] Keys in result: {list(result.keys())}")