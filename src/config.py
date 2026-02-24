import os
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="/app/.env", extra="ignore", env_file_encoding="utf-8"
    )

    # --- Google Gemini Configuration ---
    GOOGLE_API_KEY: str
    GOOGLE_EMBEDDING_MODEL: str = "gemini-embedding-001"
    GOOGLE_EMBEDDING_DIMENSION: int = 768

    # --- Sparse Embedding Configuration ---
    SPARSE_EMBEDDING_DIR: str = "/app/src/sparse_models_cache"
    SPARSE_EMBEDDING_NAME: str = "Qdrant/bm25"

    # --- Qdrant Configuration ---
    QDRANT_API_KEY: str
    QDRANT_URL: str

    # --- Application Configuration ---
    MAX_FILE_SIZE_MB: int = 20 * 1024 * 1024  # 20MB in bytes
    ALLOWED_EXTENSIONS: tuple = (".pdf", ".docx", ".png", ".jpg", ".jpeg", ".ppt", ".pptx")
    DOCUMENT_EXTENSIONS: tuple = (".pdf", ".docx", ".ppt", ".pptx")
    IMAGE_EXTENSIONS: tuple = (".png", ".jpg", ".jpeg")
    MAX_CONCURRENT_PROCESSES: int = 5
    CHUNK_SIZE: int = 800
    CHUNK_OVERLAP: int = 100
    DENSE_EMBEDDING_DIM: int = 768
    VECTOR_STORE_BATCH_SIZE: int = 64

    # --- OCR Service Configuration ---
    OCR_SERVICE_URL: str = "http://host.docker.internal:8001"
    OCR_POLL_INTERVAL: float = 2.0
    OCR_TIMEOUT: float = 600.0

    # --- Minio Configuration ---
    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    CA_CERTS_PATH: str

    # --- Redis Configuration
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_PASSWORD: str


settings = Settings()