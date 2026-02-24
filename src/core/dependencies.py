import asyncio
from loguru import logger

try:
    from langchain_qdrant import QdrantVectorStore
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    from langchain_qdrant.fastembed_sparse import FastEmbedSparse
    from qdrant_client import QdrantClient
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError as e:
    logger.error(f"Failed to import dependencies: {e}")
    raise

from src.config import settings


def create_vector_store() -> QdrantVectorStore:
    """Create and configure the vector store."""
    dense_embeddings = GoogleGenerativeAIEmbeddings(
        model=settings.GOOGLE_EMBEDDING_MODEL,
        output_dimensionality=settings.GOOGLE_EMBEDDING_DIMENSION
    )

    sparse_embeddings = FastEmbedSparse(
        model_name=settings.SPARSE_EMBEDDING_NAME,
        cache_dir=settings.SPARSE_EMBEDDING_DIR
    )

    qdrant_client = QdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY,
        timeout=120
    )

    return QdrantVectorStore(
        client=qdrant_client,
        collection_name="file-uploads",
        embedding=dense_embeddings,
        sparse_embedding=sparse_embeddings,
        retrieval_mode="hybrid",
        vector_name="dense",
        sparse_vector_name="sparse"
    ), qdrant_client


def create_text_chunker() -> RecursiveCharacterTextSplitter:
    """Create and configure the text chunker."""
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        separators=["\n\n", "\n", " ", ",", "."],
    )


def create_semaphore() -> asyncio.Semaphore:
    """Create semaphore for controlling concurrent processes."""
    return asyncio.Semaphore(settings.MAX_CONCURRENT_PROCESSES)