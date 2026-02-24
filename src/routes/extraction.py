import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid5, NAMESPACE_DNS
from typing import AsyncGenerator
from io import BytesIO

from loguru import logger
from fastapi import APIRouter, UploadFile, HTTPException, Depends, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.schemas.exceptions import FileValidationError
from src.core.validator import FileValidator
from src.services.extract import FileExtractionService
from src.services.docs import upload_file_to_minio
from src.config import settings


router = APIRouter()

_extraction_service: FileExtractionService | None = None


class ExtractionRequest(BaseModel):
    user_id: str
    conversation_id: str


def _upload_to_minio_sync(
    file_content: bytes,
    filename: str,
    content_type: str,
    file_id: str,
) -> str:
    """
    Synchronous MinIO upload wrapper that matches the actual
    upload_file_to_minio(file, file_id) signature.
    """

    class MinioUploadFile:
        """Mimics UploadFile interface for MinIO upload."""
        def __init__(self, content: bytes, fname: str, ctype: str):
            self.file = BytesIO(content)
            self.filename = fname
            self.content_type = ctype
            self.size = len(content)

    temp_file = MinioUploadFile(file_content, filename, content_type)
    return upload_file_to_minio(temp_file, file_id)


async def async_upload_to_minio(
    file_content: bytes,
    filename: str,
    content_type: str,
    file_id: str,
) -> str:
    """Async wrapper for synchronous MinIO upload."""
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as executor:
        return await loop.run_in_executor(
            executor,
            _upload_to_minio_sync,
            file_content,
            filename,
            content_type,
            file_id,
        )


def get_extraction_service() -> FileExtractionService:
    """Dependency to get the extraction service."""
    if not _extraction_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return _extraction_service


def set_extraction_service(service: FileExtractionService) -> None:
    """Set the extraction service instance."""
    global _extraction_service
    _extraction_service = service


@router.post(
    "/doc/extract",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server-Sent Events (SSE) stream with real-time progress updates",
            "content": {
                "text/event-stream": {
                    "schema": {
                        "type": "string",
                        "description": "Stream of SSE events in JSON format",
                        "example": 'data: {"status": "started", "message": "Reading file..."}\n\n',
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
    conversation_id: str = Form(...),
    extraction_service: FileExtractionService = Depends(get_extraction_service),
):
    """
    Extract documents or upload images/presentations with progress updates via SSE.

    Flow for PDFs:
        1. Submit to OCR service → real-time extraction progress
        2. Chunk extracted text
        3. Upsert chunks to vector store
        4. Upload original file to MinIO
        5. Return final result with file metadata

    Flow for DOCX:
        Same as above but extraction happens locally.

    Flow for images/presentations:
        Direct upload to MinIO storage.
    """

    async def progress_generator() -> AsyncGenerator[str, None]:
        content = None
        file_content_for_minio = None
        file_name = file.filename or "unknown"
        file_id = str(uuid5(NAMESPACE_DNS, f"{file_name}_{user_id}_{conversation_id}"))
        extraction_task = None

        # Queue for receiving progress updates from OCR service
        progress_queue: asyncio.Queue[dict] = asyncio.Queue()

        def on_ocr_progress(percent: float, message: str):
            """Callback that pushes OCR progress into the SSE queue."""
            progress_queue.put_nowait({
                "status": "processing",
                "message": message,
                "progress": round(percent, 1),
            })

        def sse(data: dict) -> str:
            """Helper to format SSE data."""
            return f"data: {json.dumps(data)}\n\n"

        try:
            # ── Step 0: Read & validate file ──
            yield sse({"status": "started", "message": "Reading file..."})

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
                    "status": "processing",
                    "message": f"Uploading {file_type} to storage...",
                    "progress": 50.0,
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
                        "status": "completed",
                        "message": f"{file_type.capitalize()} uploaded successfully!",
                        "progress": 100.0,
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
                        "status": "failed",
                        "message": f"Failed to upload {file_type}",
                        "progress": 0.0,
                        "file_metadata": {
                            "file_name": file_name,
                            "file_id": file_id,
                            "file_type": file_type,
                        },
                        "success": False,
                        "error": str(minio_error),
                    })

            # ────────────────────────────────────────────
            # PATH B: Document processing (PDF / DOCX)
            # ────────────────────────────────────────────
            else:
                yield sse({
                    "status": "processing",
                    "message": "Starting content extraction...",
                    "progress": 5.0,
                })

                try:
                    async with asyncio.timeout(600):
                        async with extraction_service.semaphore:

                            if file.filename.lower().endswith(".pdf"):
                                # ── PDF: OCR service with real-time progress ──
                                extraction_task = asyncio.create_task(
                                    extraction_service.extract_pdf(
                                        file=content,
                                        filename=file.filename,
                                        batch_size=20,
                                        on_progress=on_ocr_progress,
                                    )
                                )

                                # Stream progress until extraction completes
                                while not extraction_task.done():
                                    try:
                                        progress = await asyncio.wait_for(
                                            progress_queue.get(),
                                            timeout=3.0,
                                        )
                                        yield sse(progress)
                                    except asyncio.TimeoutError:
                                        continue

                                # Drain remaining progress messages
                                while not progress_queue.empty():
                                    progress = progress_queue.get_nowait()
                                    yield sse(progress)

                                extraction_result = extraction_task.result()

                            else:
                                # ── DOCX: Local extraction ──
                                extraction_result = await asyncio.to_thread(
                                    extraction_service.extract_word,
                                    content,
                                    file.filename,
                                )

                except asyncio.TimeoutError:
                    if extraction_task and not extraction_task.done():
                        extraction_task.cancel()
                    raise FileValidationError(
                        "Extraction timeout - file too large or complex"
                    )

                # Free raw content from memory
                del content
                content = None

                if extraction_result.get("status") != "success":
                    yield sse({
                        "status": "failed",
                        "message": "Extraction failed or no content found",
                        "progress": 0.0,
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

                # ── Chunking ──
                yield sse({
                    "status": "processing",
                    "message": "Chunking document...",
                    "progress": 70.0,
                })

                chunked_documents, ids = await extraction_service.chunk_file(
                    parsed_file_result=extraction_result,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    chunker=extraction_service.chunker,
                )

                del extraction_result

                # ── Upserting to vector store ──
                yield sse({
                    "status": "processing",
                    "message": f"Upserting {len(chunked_documents)} chunks to vector store...",
                    "progress": 80.0,
                })

                upsert_status = await extraction_service.upsert_chunks_to_vector_store(
                    documents=chunked_documents,
                    ids=ids,
                    batch_size=settings.VECTOR_STORE_BATCH_SIZE,
                    vector_store=extraction_service.vector_store,
                )

                del chunked_documents, ids

                # ── Upload original file to MinIO ──
                file_url = None
                if upsert_status:
                    try:
                        yield sse({
                            "status": "processing",
                            "message": "Uploading file to storage...",
                            "progress": 90.0,
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
                    "status": "completed" if upsert_status else "failed",
                    "message": "Processing completed!" if upsert_status else "Processing failed",
                    "progress": 100.0 if upsert_status else 0.0,
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
                "status": "error",
                "message": str(e),
                "progress": 0.0,
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