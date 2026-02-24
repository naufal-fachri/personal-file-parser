from typing import Any
import redis
import json

from  src.config import settings

# ──────────────────────────────────────────────
# Redis client for progress tracking
# ──────────────────────────────────────────────

REDIS_CLIENT = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    db=0,
    decode_responses=True,
)


def get_progress(file_id: str) -> dict[str, Any]:
    """
    Read progress from Redis. Called by Server 1 (FastAPI) to poll status.
    
    Returns:
        {
            "state": "PROCESSING",
            "total_pages": 20,
            "completed_pages": 12,
            "percent": 60.0,
            "stage": "extraction",
            "message": "OCR processed 12/20 pages...",
            "error": ""
        }
    """
    key = f"ocr_progress:{file_id}"
    data = REDIS_CLIENT.hgetall(key)
    if not data:
        return {
            "state": "PENDING",
            "total_pages": 0,
            "completed_pages": 0,
            "percent": 0.0,
            "stage": "queued",
            "message": "Waiting in queue...",
            "error": "",
        }
    
    total = int(data.get("total_pages", 1))
    completed = int(data.get("completed_pages", 0))
    percent = round((completed / total) * 100, 1) if total > 0 else 0.0
    
    return {
        "state": data.get("state", "PENDING"),
        "total_pages": total,
        "completed_pages": completed,
        "percent": percent,
        "stage": data.get("stage", ""),
        "message": data.get("message", ""),
        "error": data.get("error", ""),
    }

def get_result(file_id: str) -> list[dict[str, Any]] | None:
    key = f"ocr_results:{file_id}"
    data = REDIS_CLIENT.get(key)
    if not data:
        return None
    return json.loads(data)