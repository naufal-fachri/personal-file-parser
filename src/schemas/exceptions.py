class FileValidationError(Exception):
    def __init__(self, message: str):
        self.message = message

class ExtractionError(Exception):
    def __init__(self, message: str):
        self.message = message

class VectorStoreError(Exception):
    def __init__(self, message: str):
        self.message = message

class DocumentNotFoundError(Exception):
    def __init__(self, message: str):
        self.message = message

class DatabaseError(Exception):
    def __init__(self, message: str):
        self.message = message

class MinioConnectionError(Exception):
    def __init__(self, message: str):
        self.message = message