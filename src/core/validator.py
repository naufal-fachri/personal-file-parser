from fastapi import UploadFile
from src.config import settings
from src.schemas.exceptions import FileValidationError

class FileValidator:
    """Handles file validation logic."""
    
    # Document extensions that need extraction
    DOCUMENT_EXTENSIONS = (".pdf", ".docx", ".ppt", ".pptx")
    
    # Image extensions that should be uploaded directly
    IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")
    
    # All allowed extensions
    ALLOWED_EXTENSIONS = DOCUMENT_EXTENSIONS + IMAGE_EXTENSIONS
    
    @staticmethod
    def validate_file(file: UploadFile, content: bytes) -> None:
        """Validate uploaded file."""
        if not file.filename:
            raise FileValidationError("No filename provided")
        
        # Check file extension
        file_ext = FileValidator.get_file_extension(file.filename)
        if file_ext not in FileValidator.ALLOWED_EXTENSIONS:
            supported = ', '.join(FileValidator.ALLOWED_EXTENSIONS)
            raise FileValidationError(
                f"Unsupported file type '{file_ext}'. Supported types: {supported}"
            )
        
        # Check file size
        if len(content) > settings.MAX_FILE_SIZE_MB:
            max_size_mb = settings.MAX_FILE_SIZE_MB // (1024 * 1024)
            raise FileValidationError(f"File too large. Maximum size is {max_size_mb}MB")
        
        # Check if file is empty
        if len(content) == 0:
            raise FileValidationError("Empty file uploaded")
    
    @staticmethod
    def get_file_extension(filename: str) -> str:
        """Get lowercase file extension including the dot."""
        return filename.lower()[filename.rfind('.'):] if '.' in filename else ''
    
    @staticmethod
    def is_image(filename: str) -> bool:
        """Check if file is an image based on extension."""
        file_ext = FileValidator.get_file_extension(filename)
        return file_ext in FileValidator.IMAGE_EXTENSIONS
    
    @staticmethod
    def is_document(filename: str) -> bool:
        """Check if file is a document that needs extraction."""
        file_ext = FileValidator.get_file_extension(filename)
        return file_ext in FileValidator.DOCUMENT_EXTENSIONS
    
    @staticmethod
    def is_powerpoint(filename: str) -> bool:
        """Check if file is a PowerPoint presentation."""
        file_ext = FileValidator.get_file_extension(filename)
        return file_ext in (".ppt", ".pptx")