import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid5, NAMESPACE_DNS
from typing import AsyncGenerator
from io import BytesIO
from loguru import logger
from fastapi import APIRouter, UploadFile, HTTPException, Depends, Form, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.schemas.exceptions import FileValidationError
from src.core.validator import FileValidator
from src.services.extract import FileExtractionService
from src.services.docs import upload_file_to_minio
from src.config import settings
from src.schemas.responses import OCRResultResponse
from src.tools.utils import get_progress, get_result


router = APIRouter()

_extraction_service: FileExtractionService | None = None


class ExtractionRequest(BaseModel):
    user_id: str


def _upload_to_minio_sync(
    file_content: bytes,
    filename: str,
    content_type: str,
    file_id: str,
) -> str:
    class MinioUploadFile:
        def __init__(self, content: bytes, fname: str, ctype: str):
            self.file = BytesIO(content)
            self.filename = fname
            self.content_type = ctype
            self.size = len(content)

    temp_file = MinioUploadFile(file_content, filename, content_type)
    return upload_file_to_minio(temp_file, file_id)


async def async_upload_to_minio(
    file_content: bytes, filename: str,
    content_type: str, file_id: str,
) -> str:
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as executor:
        return await loop.run_in_executor(
            executor, _upload_to_minio_sync,
            file_content, filename, content_type, file_id,
        )


def get_extraction_service() -> FileExtractionService:
    if not _extraction_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return _extraction_service


def set_extraction_service(service: FileExtractionService) -> None:
    global _extraction_service
    _extraction_service = service


@router.post(
    "/doc/extract",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "SSE stream with real-time progress updates",
            "content": {
                "text/event-stream": {
                    "schema": {
                        "type": "string",
                        "example": 'data: {"stage": "extraction", "status": "processing", ...}\n\n',
                    }
                }
            },
        },
        503: {
            "description": "Service Unavailable",
            "content": {
                "application/json": {
                    "example": {"detail": "Service not initialized"},
                }
            },
        },
    },
)
async def extract_file(
    file: UploadFile,
    user_id: str = Form(...),
    extraction_service: FileExtractionService = Depends(get_extraction_service),
):
    """
    Extract documents or upload images/presentations with progress updates via SSE.

    SSE event format:
        {
            "stage": "reading" | "extraction" | "fetching" | "chunking" | "upserting" | "uploading" | "completed" | "failed",
            "status": "started" | "processing" | "completed" | "failed" | "error",
            "message": "human-readable message",

            // Only for "extraction" stage (OCR / Word):
            "percent": 0-100,
            "completed_pages": int,
            "total_pages": int,

            // Only for "completed" / "failed" stages:
            "file_metadata": { ... },
            "success": bool,
            "error": str | null,
        }
    """

    async def progress_generator() -> AsyncGenerator[str, None]:
        content = None
        file_content_for_minio = None
        file_name = file.filename or "unknown"
        file_id = str(uuid5(NAMESPACE_DNS, f"{file_name}_{user_id}"))
        extraction_task = None

        progress_queue: asyncio.Queue[dict] = asyncio.Queue()

        def on_progress(progress_data: dict):
            """Callback from extraction service — pushes structured progress into SSE queue."""
            progress_queue.put_nowait({
                "stage": progress_data.get("stage", "extraction"),
                "status": "processing",
                "message": progress_data.get("message", ""),
                "percent": progress_data.get("percent", 0),
                "completed_pages": progress_data.get("completed_pages", None),
                "total_pages": progress_data.get("total_pages", None),
            })

        def sse(data: dict) -> str:
            return f"data: {json.dumps(data)}\n\n"

        try:
            # ── Read & validate ──
            yield sse({
                "stage": "reading",
                "status": "started",
                "message": "Reading file...",
            })

            try:
                content = await asyncio.wait_for(file.read(), timeout=60.0)
                file_content_for_minio = content
            except asyncio.TimeoutError:
                raise FileValidationError("File upload timeout")

            FileValidator.validate_file(file, content)

            is_image = FileValidator.is_image(file.filename)
            is_powerpoint = FileValidator.is_powerpoint(file.filename)

            # ────────────────────────────────────────────
            # PATH A: Direct upload (images & presentations)
            # ────────────────────────────────────────────
            if is_image or is_powerpoint:
                file_type = "image" if is_image else "presentation"

                yield sse({
                    "stage": "uploading",
                    "status": "processing",
                    "message": f"Uploading {file_type} to storage...",
                })

                try:
                    if is_powerpoint:
                        content_type = (
                            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                            if file.filename.lower().endswith(".pptx")
                            else "application/vnd.ms-powerpoint"
                        )
                    else:
                        content_type = file.content_type or "image/jpeg"

                    file_url = await async_upload_to_minio(
                        file_content=file_content_for_minio,
                        filename=file.filename,
                        content_type=content_type,
                        file_id=file_id,
                    )

                    logger.info(f"{file_type.capitalize()} uploaded to MinIO: {file_url}")

                    yield sse({
                        "stage": "completed",
                        "status": "completed",
                        "message": f"{file_type.capitalize()} uploaded successfully!",
                        "file_metadata": {
                            "file_name": file_name,
                            "file_id": file_id,
                            "file_url": file_url,
                            "file_type": file_type,
                        },
                        "success": True,
                        "error": None,
                    })

                except Exception as minio_error:
                    logger.error(f"Failed to upload {file_type} to MinIO: {minio_error}")
                    yield sse({
                        "stage": "failed",
                        "status": "failed",
                        "message": f"Failed to upload {file_type}",
                        "file_metadata": {
                            "file_name": file_name,
                            "file_id": file_id,
                            "file_type": file_type,
                        },
                        "success": False,
                        "error": str(minio_error),
                    })

            else:
                try:
                    async with asyncio.timeout(600):

                        if file.filename.lower().endswith(".pdf"):
                            # ── PDF: OCR service with real-time progress ──
                            extraction_task = asyncio.create_task(
                                extraction_service.extract_pdf(
                                    file=content,
                                    filename=file.filename,
                                    file_id=file_id,
                                    batch_size=8,
                                    on_progress=on_progress,
                                )
                            )

                            # Stream progress until done
                            while not extraction_task.done():
                                try:
                                    progress = await asyncio.wait_for(
                                        progress_queue.get(), timeout=3.0,
                                    )
                                    yield sse(progress)
                                except asyncio.TimeoutError:
                                    continue

                            # Drain remaining
                            while not progress_queue.empty():
                                yield sse(progress_queue.get_nowait())

                            extraction_result = extraction_task.result()

                        else:
                            # ── DOCX: Local extraction with real-time progress ──
                            extraction_task = asyncio.create_task(
                                asyncio.to_thread(
                                    extraction_service.extract_word,
                                    content,
                                    file_id,
                                    file.filename,
                                    on_progress,
                                )
                            )

                            # Stream progress until done (same pattern as PDF)
                            while not extraction_task.done():
                                try:
                                    progress = await asyncio.wait_for(
                                        progress_queue.get(), timeout=3.0,
                                    )
                                    yield sse(progress)
                                except asyncio.TimeoutError:
                                    continue

                            # Drain remaining
                            while not progress_queue.empty():
                                yield sse(progress_queue.get_nowait())

                            extraction_result = extraction_task.result()

                except asyncio.TimeoutError:
                    if extraction_task and not extraction_task.done():
                        extraction_task.cancel()
                    raise FileValidationError(
                        "Extraction timeout - file too large or complex"
                    )

                # Free raw content
                del content
                content = None

                if extraction_result.get("status") != "success":
                    yield sse({
                        "stage": "failed",
                        "status": "failed",
                        "message": "Extraction failed or no content found",
                        "file_metadata": {
                            "file_name": file_name,
                            "file_id": file_id,
                            "file_type": "document",
                        },
                        "success": False,
                        "error": extraction_result.get("error", "Unknown error"),
                    })
                    return

                logger.info(f"Extraction successful for file: {file.filename}")

                # ── Step 2: Chunking ──
                yield sse({
                    "stage": "chunking",
                    "status": "processing",
                    "message": "Chunking document...",
                })

                chunked_documents, ids = await extraction_service.chunk_file(
                    parsed_file_result=extraction_result,
                    user_id=user_id,
                    chunker=extraction_service.chunker,
                )

                del extraction_result

                # ── Step 3: Upserting ──
                yield sse({
                    "stage": "upserting",
                    "status": "processing",
                    "message": f"Upserting {len(chunked_documents)} chunks to vector store...",
                })

                upsert_status = await extraction_service.upsert_chunks_to_vector_store(
                    documents=chunked_documents,
                    ids=ids,
                    batch_size=settings.VECTOR_STORE_BATCH_SIZE,
                    vector_store=extraction_service.vector_store,
                )

                del chunked_documents, ids

                # ── Step 4: Upload to MinIO ──
                file_url = None
                if upsert_status:
                    try:
                        yield sse({
                            "stage": "uploading",
                            "status": "processing",
                            "message": "Uploading file to storage...",
                        })

                        file_url = await async_upload_to_minio(
                            file_content=file_content_for_minio,
                            filename=file.filename,
                            content_type=file.content_type or "application/octet-stream",
                            file_id=file_id,
                        )

                        logger.info(f"Document uploaded to MinIO: {file_url}")

                    except Exception as minio_error:
                        logger.error(f"Failed to upload document to MinIO: {minio_error}")

                    finally:
                        if file_content_for_minio:
                            del file_content_for_minio
                            file_content_for_minio = None

                # ── Final result ──
                yield sse({
                    "stage": "completed" if upsert_status else "failed",
                    "status": "completed" if upsert_status else "failed",
                    "message": "Processing completed!" if upsert_status else "Processing failed",
                    "file_metadata": {
                        "file_name": file_name,
                        "file_id": file_id,
                        "file_url": file_url if upsert_status else None,
                        "file_type": "document",
                    },
                    "success": upsert_status,
                    "error": None,
                })

        except asyncio.CancelledError:
            logger.warning(f"Request cancelled for file: {file_name}")
            if extraction_task and not extraction_task.done():
                extraction_task.cancel()
            raise

        except Exception as e:
            logger.error(f"Error processing file {file_name}: {e}", exc_info=True)
            yield sse({
                "stage": "failed",
                "status": "error",
                "message": str(e),
                "file_metadata": {"file_name": file_name, "file_id": file_id},
                "success": False,
                "error": str(e),
            })

        finally:
            if content:
                del content
            if file_content_for_minio:
                del file_content_for_minio
            try:
                await file.close()
            except Exception as e:
                logger.warning(f"Error closing file: {e}")

    return StreamingResponse(
        progress_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

@router.get(
    "/doc/ocr_result/{file_id}",
    response_model=OCRResultResponse,
    summary="Get final OCR result for a file",
)
async def ocr_result(file_id: str):
    """
    Returns the final combined OCR result from Redis.
    Only call this after progress shows state=SUCCESS.
    """
    pages = get_result(file_id)

    if pages is None:
        progress = get_progress(file_id)
        if progress["state"] in ("PENDING", "PROCESSING", "COMBINING"):
            raise HTTPException(
                status_code=status.HTTP_202_ACCEPTED,
                detail=f"Task still in progress: {progress['message']}",
            )
        elif progress["state"] == "FAILURE":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"OCR failed: {progress['error']}",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Result not found. It may have expired or never completed.",
            )

    return OCRResultResponse(
        status=True,
        file_id=file_id,
        total_pages=len(pages),
        pages=pages,
    )