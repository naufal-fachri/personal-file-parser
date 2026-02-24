from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from typing import List, Optional
import mimetypes
import base64
from starlette.responses import JSONResponse
from src.schemas.exceptions import MinioConnectionError, DatabaseError, DocumentNotFoundError
from src.services.docs import get_file_from_minio
from pydantic import BaseModel

router = APIRouter()


class DocumentItem(BaseModel):
    bucket_name: str
    document_name: str


class DocumentBatchRequest(BaseModel):
    preview: Optional[bool] = False
    documents: List[DocumentItem]


# Single file - GET with path/query params
@router.get("/doc")
async def download_or_preview_file(
    bucket_name: str,
    document_name: str,
    preview: bool = False,
):
    """Ambil satu file dari MinIO."""
    try:
        file_content = get_file_from_minio(bucket_name, document_name)
        mime_type = mimetypes.guess_type(document_name)[0] or "application/octet-stream"

        content_disposition = (
            f'inline; filename="{document_name}"'
            if preview or "pdf" in mime_type or "image" in mime_type
            else f'attachment; filename="{document_name}"'
        )

        return StreamingResponse(
            content=iter([file_content]),
            media_type=mime_type,
            headers={"Content-Disposition": content_disposition},
        )

    except DocumentNotFoundError as e:
        return JSONResponse(status_code=404, content={"error": e.message})
    except MinioConnectionError as e:
        return JSONResponse(status_code=503, content={"error": e.message})
    except DatabaseError as e:
        return JSONResponse(status_code=500, content={"error": e.message})
    except Exception as e:
        logger.exception("Unexpected error in GET /doc")
        return JSONResponse(status_code=500, content={"error": f"Internal server error: {str(e)}"})


# Batch files - POST with JSON body
@router.post("/doc/batch")
async def download_or_preview_files_batch(request: DocumentBatchRequest):
    """
    Ambil beberapa file dari MinIO sekaligus (return JSON base64).
    Kalau cuma 1 dokumen di list, tetap return streaming response.
    """
    try:
        if len(request.documents) == 1:
            doc = request.documents[0]
            file_content = get_file_from_minio(doc.bucket_name, doc.document_name)
            mime_type = mimetypes.guess_type(doc.document_name)[0] or "application/octet-stream"

            content_disposition = (
                f'inline; filename="{doc.document_name}"'
                if request.preview or "pdf" in mime_type or "image" in mime_type
                else f'attachment; filename="{doc.document_name}"'
            )

            return StreamingResponse(
                content=iter([file_content]),
                media_type=mime_type,
                headers={"Content-Disposition": content_disposition},
            )

        results = []
        for doc in request.documents:
            try:
                file_content = get_file_from_minio(doc.bucket_name, doc.document_name)
                mime_type = mimetypes.guess_type(doc.document_name)[0] or "application/octet-stream"
                b64_content = base64.b64encode(file_content).decode("utf-8")
                results.append({
                    "document_name": doc.document_name,
                    "bucket_name": doc.bucket_name,
                    "mime_type": mime_type,
                    "content_base64": b64_content,
                })
            except Exception as e:
                logger.error(f"Failed to process {doc.document_name}: {str(e)}")
                results.append({
                    "document_name": doc.document_name,
                    "bucket_name": doc.bucket_name,
                    "error": str(e),
                })

        return JSONResponse(content={"results": results})

    except DocumentNotFoundError as e:
        return JSONResponse(status_code=404, content={"error": e.message})
    except MinioConnectionError as e:
        return JSONResponse(status_code=503, content={"error": e.message})
    except DatabaseError as e:
        return JSONResponse(status_code=500, content={"error": e.message})
    except Exception as e:
        logger.exception("Unexpected error in POST /doc/batch")
        return JSONResponse(status_code=500, content={"error": f"Internal server error: {str(e)}"})