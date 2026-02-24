import urllib3
from minio import Minio
from loguru import logger
from fastapi import UploadFile
from datetime import datetime
from urllib.parse import quote, unquote  # Add this import
from src.config import settings
from src.schemas.exceptions import (
    MinioConnectionError,
    DatabaseError,
    DocumentNotFoundError,
)
from minio.error import S3Error

http_client = urllib3.PoolManager(
    cert_reqs='CERT_REQUIRED',
    ca_certs='/home/naufal/minio/certs/public.crt'
)
# Create a synchronous database engine by replacing asyncpg with psycopg2
def get_sync_database_url():
    """Convert async database URL to sync version for psycopg2."""
    db_url = settings.DATABASE_URL
    if "postgresql+asyncpg://" in db_url:
        return db_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    elif "postgresql://" in db_url and "asyncpg" not in db_url:
        # If it's a plain postgresql URL, make it explicit for psycopg2
        return db_url.replace("postgresql://", "postgresql+psycopg2://")
    return db_url


def upload_file_to_minio(file: UploadFile, file_id: str) -> str:
    """
    Upload a file to MinIO storage and save metadata to DB.

    Args:
        bucket_name (str): Target bucket in MinIO.
        file (UploadFile): File to upload.
        file_id          : Unique ID

    Returns:
        str: URL of the uploaded file.
    """
    try:
        bucket_name = "file-uploads"
        minio_client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=True,
            http_client=http_client
        )

        object_name = f"{file.filename}"
                # URL-encode metadata values to ensure ASCII compatibility
        metadata = {
            "original_filename": quote(file.filename, safe=''), 
            "file_id": file_id,
            "content_type": file.content_type or "application/octet-stream",
            "upload_time": datetime.now().isoformat(),
        }

        # Upload ke MinIO
        minio_client.put_object(
            bucket_name=bucket_name,
            object_name=object_name,
            data=file.file,
            length=(
                file.size if hasattr(file, "size") else -1
            ),
            content_type=file.content_type or "application/octet-stream",
            metadata=metadata,
        )

        # Buat URL publik (bisa disesuaikan, tergantung gateway kamu)
        file_url = f"/{bucket_name}/{object_name}"
        logger.info(f"File uploaded successfully to {file_url}")
        return file_url

    except Exception as e:
        logger.error(f"Failed to upload file to MinIO: {e}")
        raise


def get_file_from_minio(bucket_name: str, filename: str) -> bytes:
    """
    Retrieve a file from MinIO storage (support file_uploads & registered buckets).
    """
    try:
        minio_client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=True,
            http_client=http_client
        )

        if bucket_name == "file_uploads":
            # User-uploaded bucket
            file_url = f"{filename}"
            logger.info(
                f"Retrieving file uploads: {file_url} from bucket: {bucket_name}"
            )

            response = minio_client.get_object(
                bucket_name=bucket_name,
                object_name=file_url
            )
            file_content = response.read()
            response.close()
            response.release_conn()
            return file_content

        else:
            logger.info(
                f"Retrieving registered document file: {filename} from bucket: {bucket_name}"
            )
            response = minio_client.get_object(
                bucket_name=bucket_name, object_name=filename
            )
            file_content = response.read()
            response.close()
            response.release_conn()
            return file_content

    except S3Error as e:
        raise MinioConnectionError(f"Failed to retrieve file from Minio: {str(e.message)}")

    except Exception as e:
        raise Exception(f"Error during retriving '{filename}': {str(e)}")