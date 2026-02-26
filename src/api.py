from contextlib import asynccontextmanager

from loguru import logger
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import psutil

from src.core.dependencies import (
    create_vector_store,
    create_text_chunker,
    create_semaphore,
)
from src.schemas.responses import HealthCheckResponse
from src.services.extract import FileExtractionService
from src.routes.extraction import router as extraction_router, set_extraction_service
from src.routes.documents import router as documents_router
from src.config import settings

# Global variables
extraction_service = None
qdrant_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global extraction_service, qdrant_client

    # Startup
    try:
        logger.info("Setting up process executor and extraction service...")

        # Create dependencies
        vector_store, qdrant_client = create_vector_store()
        chunker = create_text_chunker()

        extraction_service = FileExtractionService(
            chunker=chunker,
            vector_store=vector_store,
            ocr_service_url=settings.OCR_SERVICE_URL,
            ocr_poll_interval=settings.OCR_POLL_INTERVAL,
            ocr_timeout=settings.OCR_TIMEOUT,
        )

        # Set the service in routes
        set_extraction_service(extraction_service)

        logger.info("Started extraction service successfully")
        logger.info(f"OCR Service URL: {settings.OCR_SERVICE_URL}")

    except Exception as e:
        logger.error(f"Failed to initialize services: {e}")
        raise

    yield

    # Shutdown — proper cleanup order
    if extraction_service:
        logger.info("Cleaning up extraction service...")
        await extraction_service.close()

    if qdrant_client:
        qdrant_client.close()
        logger.info("Qdrant client connection closed")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title="File Extraction & Document API",
        description=(
            "API for extracting text content from PDF and Word documents, "
            "and retrieving/downloading files from MinIO storage."
        ),
        version="0.2.0",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "Content-Length", "Content-Type"],
    )

    # ── Routers ──
    app.include_router(documents_router, tags=["Documents"])
    app.include_router(extraction_router, tags=["Extraction"])

    # ── Root ──
    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/doc")

    # ── Health check ──
    @app.get("/health", response_model=HealthCheckResponse, tags=["Health"])
    async def health_check():
        """Health check endpoint with memory and CPU metrics."""
        process = psutil.Process()
        return HealthCheckResponse(
            status="healthy",
            service="file-extraction-api",
            cpu_percent=process.cpu_percent(),
        )

    return app


# Create the app instance
app = create_app()