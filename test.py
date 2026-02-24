"""
API tests for File Extraction & Document API.

Usage:
    uv run python test.py

Update the constants below before running.
Server must be running at BASE_URL.
"""

import json
import sys
from pathlib import Path

import httpx

# ── Config ─────────────────────────────────────────────────────────────────────
BASE_URL = "http://localhost:8002"

# POST /doc/extract — path to a local file to test upload
TEST_FILE_PATH = "/home/naufal/personal_file_parser/documents/2105.05318.pdf"        # e.g. "/tmp/sample.pdf"
TEST_USER_ID   = "user-naufal"

# GET /doc and POST /doc/batch — real MinIO values
MINIO_BUCKET   = "file-uploads"
MINIO_DOCUMENT = "2105.05318.pdf"
# ───────────────────────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

PASS = f"{GREEN}PASS{RESET}"
FAIL = f"{RED}FAIL{RESET}"
SKIP = f"{YELLOW}SKIP{RESET}"

_results: dict[str, bool | None] = {}


def section(title: str):
    print(f"\n{'─' * 58}")
    print(f"  {title}")
    print("─" * 58)


def check(label: str, passed: bool, detail: str = ""):
    tag = PASS if passed else FAIL
    print(f"  [{tag}] {label}")
    if detail:
        print(f"         {detail}")


# ── 1. Health ──────────────────────────────────────────────────────────────────
def test_health() -> bool:
    section("GET /health")
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=10)
        ok = r.status_code == 200
        body = r.json()
        check(
            f"status={body.get('status')}  service={body.get('service')}",
            ok,
            f"cpu={body.get('cpu_percent')}%",
        )
        return ok
    except Exception as e:
        check("request failed", False, str(e))
        return False


# ── 2. GET /doc ────────────────────────────────────────────────────────────────
def test_get_doc() -> bool:
    section("GET /doc  (single file)")
    try:
        r = httpx.get(
            f"{BASE_URL}/doc",
            params={"bucket_name": MINIO_BUCKET, "document_name": MINIO_DOCUMENT, "preview": "true"},
            timeout=30,
        )
        ok = r.status_code == 200
        if ok:
            ct = r.headers.get("content-type", "")
            check(f"200  content-type: {ct}", True, f"bytes: {len(r.content)}")
        else:
            check(f"status {r.status_code}", False, r.text[:200])
        return ok
    except Exception as e:
        check("request failed", False, str(e))
        return False


# ── 3. POST /doc/batch (single → streaming) ────────────────────────────────────
def test_batch_single() -> bool:
    section("POST /doc/batch  (1 document → streaming response)")
    payload = {
        "preview": True,
        "documents": [{"bucket_name": MINIO_BUCKET, "document_name": MINIO_DOCUMENT}],
    }
    try:
        r = httpx.post(f"{BASE_URL}/doc/batch", json=payload, timeout=30)
        ok = r.status_code == 200
        ct = r.headers.get("content-type", "")
        check(f"status {r.status_code}  content-type: {ct}", ok, f"bytes: {len(r.content)}")
        return ok
    except Exception as e:
        check("request failed", False, str(e))
        return False


# ── 4. POST /doc/batch (multi → JSON base64) ──────────────────────────────────
def test_batch_multi() -> bool:
    section("POST /doc/batch  (2 documents → JSON base64)")
    payload = {
        "preview": False,
        "documents": [
            {"bucket_name": MINIO_BUCKET, "document_name": MINIO_DOCUMENT},
            {"bucket_name": MINIO_BUCKET, "document_name": "another.pdf"},
        ],
    }
    try:
        r = httpx.post(f"{BASE_URL}/doc/batch", json=payload, timeout=30)
        ok = r.status_code == 200
        if ok:
            items = r.json().get("results", [])
            check(f"{len(items)} item(s) returned", True)
            for item in items:
                name = item.get("document_name", "?")
                if "content_base64" in item:
                    check(f"  {name}", True, "base64 content present")
                else:
                    check(f"  {name}", False, item.get("error", "no content"))
        else:
            check(f"status {r.status_code}", False, r.text[:200])
        return ok
    except Exception as e:
        check("request failed", False, str(e))
        return False


# ── 5. POST /doc/extract  (SSE stream) ────────────────────────────────────────
def test_extract() -> bool | None:
    section("POST /doc/extract  (SSE stream)")

    if not TEST_FILE_PATH:
        print(f"  [{SKIP}] TEST_FILE_PATH not set — skipping")
        return None

    path = Path(TEST_FILE_PATH)
    if not path.exists():
        print(f"  [{SKIP}] file not found: {TEST_FILE_PATH}")
        return None

    mime_map = {
        ".pdf":  "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".ppt":  "application/vnd.ms-powerpoint",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    mime = mime_map.get(path.suffix.lower(), "application/octet-stream")

    events: list[dict] = []
    try:
        with httpx.stream(
            "POST",
            f"{BASE_URL}/doc/extract",
            data={"user_id": TEST_USER_ID},
            files={"file": (path.name, path.open("rb"), mime)},
            timeout=None,
        ) as r:
            if r.status_code != 200:
                check(f"status {r.status_code}", False, r.read().decode()[:200])
                return False

            print(f"  Streaming events for: {path.name}\n")
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                    events.append(event)
                    status   = event.get("status", "?")
                    message  = event.get("message", "")
                    progress = event.get("progress")
                    pct      = f"[{progress}%]" if progress is not None else ""
                    print(f"    {status:<12} {pct:<8} {message}")
                except json.JSONDecodeError:
                    pass

        final   = events[-1] if events else {}
        success = final.get("success", False)
        meta    = final.get("file_metadata", {})
        check("extraction finished", success, f"file_id={meta.get('file_id', '?')}")
        return success

    except Exception as e:
        check("request failed", False, str(e))
        return False


# ── Runner ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\nTarget: {BASE_URL}\n")

    results = {
        "health":        test_health(),
        "extract":       test_extract(),
        "get_doc":       test_get_doc(),
        "batch_single":  test_batch_single(),
        "batch_multi":   test_batch_multi(),
    }

    section("Summary")
    passed  = sum(1 for v in results.values() if v is True)
    failed  = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v is None)

    for name, ok in results.items():
        tag = PASS if ok is True else (SKIP if ok is None else FAIL)
        print(f"  [{tag}] {name}")

    print(f"\n  {passed} passed · {failed} failed · {skipped} skipped\n")
    sys.exit(0 if failed == 0 else 1)
