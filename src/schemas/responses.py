from pydantic import BaseModel, Field
from typing import Optional, Any, Dict, List
from enum import Enum

class ProgressStatus(str, Enum):
    STARTED = "started"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    ERROR = "error"

class SSEProgressEvent(BaseModel):
    """SSE event model for progress updates."""
    status: ProgressStatus = Field(ProgressStatus.COMPLETED, description="Processing status")
    message: str = Field(..., description="Completion message")
    file_metadata: List[Dict[str, Any]] = Field(..., description="Metadata about the processed file")
    success: bool = Field(True, description="Whether processing succeeded")
    error: Optional[str] = Field(None, description="Error message (null on success)")

class FileMetadata(BaseModel):
    """File metadata model."""
    file_name: str
    file_id: str

class ProgressResponse(BaseModel):
    """Response model for progress updates."""
    status: ProgressStatus
    message: str
    file_metadata: FileMetadata
    success: bool
    error: Optional[str] = None

class HealthCheckResponse(BaseModel):
    """Health check response model."""
    status: str
    service: str
    cpu_percent: float

class DocumentResponse(BaseModel):
    """Document response model."""
    id: str
    file_name: str
    bucket_name: str
    file_url: str
    file_size: Optional[int] = None
    content_type: Optional[str] = None
    uploaded_at: Optional[str] = None

class DocumentListResponse(BaseModel):
    """Document list response model."""
    documents: List[DocumentResponse]
    total: int
    bucket_name: str