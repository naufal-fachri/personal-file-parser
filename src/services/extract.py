import asyncio
from io import BytesIO
from typing import Any, Optional, Callable
from uuid import uuid4, uuid5, NAMESPACE_DNS

from loguru import logger
import httpx

# Import extractors
try:
    from src.tools.word_extractor import WordDocumentExtractor
except ImportError as e:
    logger.error(f"Failed to import extractors: {e}")
    raise

# Import Document & Text Splitter
try:
    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError as e:
    logger.error(f"Failed to import Text Splitter: {e}")
    raise

# Import Vector Store
try:
    from langchain_qdrant import QdrantVectorStore
except ImportError as e:
    logger.error(f"Failed to import vector store: {e}")
    raise

from src.config import settings
from src.schemas.exceptions import ExtractionError, VectorStoreError


class FileExtractionService:
    """Main service for file extraction operations."""

    def __init__(
        self,
        semaphore: asyncio.Semaphore,
        chunker: RecursiveCharacterTextSplitter,
        vector_store: QdrantVectorStore,
        ocr_service_url: str = "http://localhost:8001",
        ocr_poll_interval: float = 2.0,
        ocr_timeout: float = 600.0,
    ):
        self.semaphore = semaphore
        self.chunker = chunker
        self.vector_store = vector_store
        self.ocr_service_url = ocr_service_url.rstrip("/")
        self.ocr_poll_interval = ocr_poll_interval
        self.ocr_timeout = ocr_timeout

        self._word_extractor = WordDocumentExtractor(infer_table_structure=True)
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    # ──────────────────────────────────────────────
    # PDF Extraction via OCR Service
    # ──────────────────────────────────────────────

    async def extract_pdf(
        self,
        file: bytes,
        filename: str,
        batch_size: int = 4,
        on_progress: Optional[Callable[[float, str], None]] = None,
    ) -> dict[str, Any]:
        """
        Extract content from PDF by sending to OCR service.

        Args:
            file: Raw PDF bytes
            filename: Original filename
            batch_size: Pages per OCR batch
            on_progress: Callback called with (percent, message) for real-time updates

        Returns:
            dict with keys: status, filename, extracted_pages, total_pages
        """
        file_id = uuid4().hex

        try:
            # ── Step 1: Submit PDF to OCR service ──
            if on_progress:
                on_progress(2.0, "Submitting PDF to OCR service...")

            submit_response = await self._submit_to_ocr(
                file=file,
                filename=filename,
                file_id=file_id,
                batch_size=batch_size,
            )

            if not submit_response:
                return {"error": "Failed to submit PDF to OCR service", "status": "failed"}

            logger.info(
                f"PDF submitted to OCR service: file_id={file_id}, "
                f"task_id={submit_response['task_id']}"
            )

            # ── Step 2: Poll for progress ──
            success = await self._poll_ocr_progress(
                file_id=file_id,
                on_progress=on_progress,
            )

            if not success:
                return {"error": "OCR processing failed or timed out", "status": "failed"}

            # ── Step 3: Fetch result ──
            if on_progress:
                on_progress(62.0, "Fetching OCR results...")

            ocr_result = await self._fetch_ocr_result(file_id=file_id)

            if not ocr_result:
                return {"error": "Failed to fetch OCR results", "status": "failed"}

            if on_progress:
                on_progress(65.0, "PDF extraction complete!")

            # ── Step 4: Format output ──
            return {
                "status": "success",
                "filename": filename,
                "extracted_pages": ocr_result["pages"],
                "total_pages": ocr_result["total_pages"],
            }

        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            return {"error": f"PDF extraction failed: {str(e)}", "status": "failed"}

        finally:
            # Always clean up temp files on OCR service
            await self._cleanup_ocr(file_id=file_id)

    async def _submit_to_ocr(
        self,
        file: bytes,
        filename: str,
        file_id: str,
        batch_size: int,
    ) -> Optional[dict]:
        """Submit PDF to OCR service's /ocr/extract endpoint."""
        try:
            response = await self._http_client.post(
                f"{self.ocr_service_url}/ocr/extract",
                files={"file": (filename, file, "application/pdf")},
                data={
                    "file_id": file_id,
                    "batch_size": str(batch_size),
                },
            )

            if response.status_code == 202:
                return response.json()

            logger.error(f"OCR submit failed: {response.status_code} - {response.text}")
            return None

        except httpx.RequestError as e:
            logger.error(f"Failed to connect to OCR service: {e}")
            return None

    async def _poll_ocr_progress(
        self,
        file_id: str,
        on_progress: Optional[Callable[[float, str], None]] = None,
    ) -> bool:
        """
        Poll OCR service for progress until completion or timeout.

        Progress is scaled to 5–60% range in the overall pipeline:
            0-5%   = file upload & submission
            5-60%  = OCR extraction
            60-70% = chunking
            70-80% = upserting
            80-90% = MinIO upload
            90-100% = done

        Returns:
            True if OCR completed successfully, False otherwise.
        """
        elapsed = 0.0

        while elapsed < self.ocr_timeout:
            try:
                response = await self._http_client.get(
                    f"{self.ocr_service_url}/ocr/progress/{file_id}"
                )

                if response.status_code != 200:
                    logger.warning(f"Progress check failed: {response.status_code}")
                    await asyncio.sleep(self.ocr_poll_interval)
                    elapsed += self.ocr_poll_interval
                    continue

                progress = response.json()
                state = progress.get("state", "UNKNOWN")
                percent = min(progress.get("percent", 0.0), 100.0)
                message = progress.get("message", "")
                stage = progress.get("stage", "")

                # Scale OCR's 0-100% into our 5-60% range
                scaled_percent = 5.0 + (percent / 100.0) * 55.0

                if on_progress:
                    display_msg = f"[{stage}] {message}" if stage else message
                    on_progress(round(scaled_percent, 1), display_msg)

                if state == "SUCCESS":
                    logger.info(f"OCR completed for file_id={file_id}")
                    return True

                if state == "FAILURE":
                    error = progress.get("error", "Unknown error")
                    logger.error(f"OCR failed for file_id={file_id}: {error}")
                    if on_progress:
                        on_progress(0.0, f"OCR failed: {error}")
                    return False

                # Still processing — wait and poll again
                await asyncio.sleep(self.ocr_poll_interval)
                elapsed += self.ocr_poll_interval

            except httpx.RequestError as e:
                logger.warning(f"Progress poll error: {e}")
                await asyncio.sleep(self.ocr_poll_interval)
                elapsed += self.ocr_poll_interval

        logger.error(f"OCR timed out after {self.ocr_timeout}s for file_id={file_id}")
        if on_progress:
            on_progress(0.0, "OCR processing timed out")
        return False

    async def _fetch_ocr_result(self, file_id: str) -> Optional[dict]:
        """Fetch completed OCR result from /ocr/result/{file_id}."""
        try:
            response = await self._http_client.get(
                f"{self.ocr_service_url}/ocr/result/{file_id}"
            )

            if response.status_code == 200:
                return response.json()

            logger.error(f"Failed to fetch OCR result: {response.status_code} - {response.text}")
            return None

        except httpx.RequestError as e:
            logger.error(f"Failed to fetch OCR result: {e}")
            return None

    async def _cleanup_ocr(self, file_id: str):
        """Clean up temp files on OCR service."""
        try:
            await self._http_client.delete(
                f"{self.ocr_service_url}/ocr/cleanup/{file_id}"
            )
            logger.debug(f"OCR cleanup done for file_id={file_id}")
        except httpx.RequestError:
            logger.warning(f"Failed to cleanup OCR files for file_id={file_id}")

    # ──────────────────────────────────────────────
    # Word Document Extraction (local)
    # ──────────────────────────────────────────────

    def extract_word(self, file: bytes, filename: str) -> dict[str, Any]:
        """Extract content from Word document."""
        buffer = None
        try:
            buffer = BytesIO(file)
            result = self._word_extractor.extract_file(file=buffer, filename=filename)

            if result.get("status") == "success":
                logger.info("Word document extraction successful")
                return result

            logger.error("No content found in Word document")
            return {"error": "No content found", "status": "failed"}

        except Exception as e:
            logger.error(f"Word extraction failed: {e}")
            return {"error": f"Word extraction failed: {str(e)}", "status": "failed"}
        finally:
            if buffer:
                buffer.close()

    # ──────────────────────────────────────────────
    # Chunking & Vector Store
    # ──────────────────────────────────────────────

    async def chunk_file(
        self,
        parsed_file_result: dict,
        user_id: str,
        conversation_id: str,
        chunker: RecursiveCharacterTextSplitter,
    ) -> tuple[list[Document], list[str]]:
        """
        Chunk parsed file result into a list of documents.

        Returns:
            Tuple of (chunked_documents, chunk_ids)
        """
        if not parsed_file_result:
            raise ValueError("parsed_file_result cannot be empty")

        if not user_id or not conversation_id:
            raise ValueError("user_id and conversation_id must be non-empty strings")

        try:
            file_name = parsed_file_result["filename"]
            pages = parsed_file_result["extracted_pages"]
        except KeyError as e:
            raise KeyError(f"Missing required key in parsed_file_result: {e}")

        if not pages:
            logger.warning("No pages found in parsed_file_result")
            return [], []

        # Create documents from pages
        documents = []
        for page in pages:
            try:
                document = Document(
                    page_content=page["text"],
                    metadata={
                        "full_content": page["text"],
                        "file_name": file_name,
                        "user_id": user_id,
                        "chat_id": conversation_id,
                        "page_number": page["page_index"],
                        "link_path": "/user-uploaded/" + file_name,
                    },
                )
                documents.append(document)
            except KeyError as e:
                logger.error(f"Missing expected key in page data: {e}")
                continue

        if not documents:
            logger.warning("No valid documents created from pages")
            return [], []

        try:
            logger.info(f"Chunking {len(documents)} documents from file: {file_name}")
            chunked_documents = await chunker.atransform_documents(documents)
            logger.info(
                f"Chunked {len(pages)} pages into "
                f"{len(chunked_documents)} chunks for file: {file_name}"
            )

            ids = [
                str(uuid5(NAMESPACE_DNS, f"{file_name}_{user_id}_{conversation_id}_chunk_{i}"))
                for i in range(len(chunked_documents))
            ]

            return chunked_documents, ids

        except Exception as e:
            logger.error(f"Failed to chunk documents: {e}")
            raise
        finally:
            documents.clear()

    async def upsert_chunks_to_vector_store(
        self,
        documents: list[Document],
        ids: list[str],
        batch_size: int,
        vector_store: QdrantVectorStore,
    ) -> bool:
        """Upsert document chunks to vector store in batches."""
        try:
            for i in range(0, len(documents), batch_size):
                batch_docs = documents[i : i + batch_size]
                batch_ids = ids[i : i + batch_size]

                await vector_store.aadd_documents(documents=batch_docs, ids=batch_ids)

                logger.info(
                    f"Upserted batch {i // batch_size + 1}: {len(batch_docs)} chunks"
                )

                del batch_docs, batch_ids

            return True

        except Exception as e:
            logger.error(f"Failed to upsert chunks: {e}")
            return False

    # ──────────────────────────────────────────────
    # Cleanup
    # ──────────────────────────────────────────────

    async def close(self):
        """Cleanup resources."""
        await self._http_client.aclose()
        if hasattr(self._word_extractor, "close"):
            await self._word_extractor.close()