import re
from typing import Optional, Any, Callable
from loguru import logger
from io import BytesIO

from .utils import _save_result

class MarkdownProcessor:
    """Utilities for processing and formatting extracted content as Markdown."""
    
    @staticmethod
    def process_title(text: str) -> str:
        clean_text = text.strip()
        if not clean_text:
            return ""
        return f"## {clean_text}"
    
    @staticmethod
    def process_text(text: str) -> str:
        return text.strip()
    
    @staticmethod
    def process_list_item(text: str) -> str:
        clean_text = text.strip()
        if not clean_text:
            return ""
        
        numbered_pattern = r'^\d+[\.\)]\s+'
        if re.match(numbered_pattern, clean_text):
            return clean_text
        
        return f"- {clean_text}"
    
    @staticmethod
    def process_table(element) -> str:
        html_table = MarkdownProcessor._extract_html_table(element)
        if html_table:
            html_table = re.sub(r'\s+', ' ', html_table)
            html_table = html_table.strip()
            return f"\n{html_table}\n"
        
        text = element.text.strip() if element.text else ""
        return text
    
    @staticmethod
    def _extract_html_table(element) -> Optional[str]:
        if not hasattr(element, 'metadata') or not element.metadata:
            return None
            
        metadata = element.metadata
        
        for attr in ['text_as_html', 'table_html', 'html']:
            if hasattr(metadata, attr):
                html_content = getattr(metadata, attr)
                if html_content:
                    return html_content
        
        return None
    

class ElementProcessor:
    """Handles processing of different document element types."""
    
    def __init__(self):
        self.processor = MarkdownProcessor()
    
    def process_element(self, element) -> tuple[str, str, bool]:
        element_type = element.category if hasattr(element, 'category') and element.category else type(element).__name__
        text = element.text.strip() if element.text else ""
        
        if element_type == "PageBreak":
            return element_type, "", True
        
        if not text:
            return element_type, "", False
        
        try:
            if element_type in ("Title", "Header"):
                content = self.processor.process_title(text)

            elif element_type in ("Text", "NarrativeText", "UncategorizedText"):
                content = self.processor.process_text(text)

            elif element_type in ("ListItem", "BulletPoint"):
                content = self.processor.process_list_item(text)

            elif element_type == "Table":
                content = self.processor.process_table(element)

            elif element_type == "CodeSnippet":
                content = f"```\n{text}\n```"
                
            else:
                content = self.processor.process_text(text)
            
            return element_type, content, False
            
        except Exception as e:
            logger.warning(f"Error processing element type {element_type}: {e}")
            return element_type, text, False


class PageFormatter:
    """Handles formatting of page content."""
    
    def format_page_content(self, content: list[tuple[str, str]]) -> str:
        if not content:
            return ""
        
        formatted_lines = [element_content for _, element_content in content]
        result = "\n".join(formatted_lines)
        result = '\n'.join(line.rstrip() for line in result.split('\n'))
        
        return result.strip()


class WordDocumentExtractor:
    """
    Production-ready extractor for converting Word documents to Markdown format.
    """
    
    SUPPORTED_EXTENSIONS = {'.doc', '.docx'}
    
    def __init__(self, infer_table_structure: bool = True):
        self.infer_table_structure = infer_table_structure
        self.element_processor = ElementProcessor()
        self.page_formatter = PageFormatter()
    
    def partition_document(self, file: BytesIO) -> list[Any]:
        try:
            from unstructured.partition.docx import partition_docx
            return partition_docx(
                file=file,
                infer_table_structure=self.infer_table_structure
            )
        except Exception as e:
            raise e
    
    def process_elements_to_pages(
        self,
        elements: list[Any],
        on_progress: Optional[Callable[[dict], None]] = None,
    ) -> list[dict[str, Any]]:
        """
        Process document elements and organize them into pages.
        
        Args:
            elements: List of unstructured elements
            on_progress: Optional callback for progress updates
        """
        pages = []
        current_page_content = []
        current_page_index = 0
        element_count = 0
        total_elements = len(elements)
        
        try:
            for i, element in enumerate(elements):
                element_type, content, is_page_break = self.element_processor.process_element(element)
                element_count += 1
                
                # Report progress every 10 elements or on page breaks
                if on_progress and (i % 10 == 0 or is_page_break):
                    percent = round((i + 1) / total_elements * 100, 1) if total_elements > 0 else 0
                    on_progress({
                        "stage": "extraction",
                        "percent": min(percent, 99),
                        "message": f"Processing elements... ({i + 1}/{total_elements})",
                        "completed_pages": len(pages),
                        "total_pages": current_page_index + 1,
                    })
                
                if is_page_break:
                    if current_page_content:
                        page_text = self.page_formatter.format_page_content(current_page_content)
                        pages.append({
                            "page_index": current_page_index,
                            "text": page_text,
                            "status": bool(page_text.strip())
                        })

                    current_page_index += 1
                    current_page_content = []
                    continue
                
                if content:
                    current_page_content.append((element_type, content))
            
            # Finalize the last page
            if current_page_content:
                page_text = self.page_formatter.format_page_content(current_page_content)
                pages.append({
                    "page_index": current_page_index,
                    "text": page_text,
                    "status": bool(page_text.strip())
                })
    
            if not pages and element_count > 0:
                combined_content = []
                for element in elements:
                    element_type, content, _ = self.element_processor.process_element(element)
                    if content:
                        combined_content.append((element_type, content))
                
                if combined_content:
                    page_text = self.page_formatter.format_page_content(combined_content)
                    pages.append({
                        "page_index": 0,
                        "text": page_text,
                        "status": True
                    })
            
            if on_progress:
                on_progress({
                    "stage": "extraction",
                    "percent": 100,
                    "message": f"Extraction complete — {len(pages)} pages processed",
                    "completed_pages": len(pages),
                    "total_pages": len(pages),
                })
            
            return pages
            
        except Exception as e:
            raise e
        
    def extract_file(
        self,
        file: BytesIO,
        file_id: str,
        filename: str,
        on_progress: Optional[Callable[[dict], None]] = None,
    ) -> dict[str, Any]:
        """
        Extract content from a Word document.
        
        Args:
            file: Document file bytes
            filename: Name of the document file
            on_progress: Optional callback for progress reporting
        """
        if not any(filename.lower().endswith(ext) for ext in self.SUPPORTED_EXTENSIONS):
            raise ValueError(
                f"Unsupported file format for {filename}. "
                f"Supported formats: {', '.join(self.SUPPORTED_EXTENSIONS)}"
            )
        
        try:
            # Stage 1: Partitioning
            if on_progress:
                on_progress({
                    "stage": "extraction",
                    "percent": 0,
                    "message": "Partitioning Word document...",
                    "completed_pages": 0,
                    "total_pages": 0,
                })

            elements = self.partition_document(file=file)
            
            logger.info(f"Document partitioned into {len(elements)} elements")

            if on_progress:
                on_progress({
                    "stage": "extraction",
                    "percent": 10,
                    "message": f"Partitioned into {len(elements)} elements, processing...",
                    "completed_pages": 0,
                    "total_pages": 0,
                })
            
            # Stage 2: Process elements into pages (progress reported inside)
            pages = self.process_elements_to_pages(
                elements=elements,
                on_progress=on_progress,
            )

            if not pages:
                logger.warning("Extraction result is empty!")

            else:
                logger.info("Save docx extraction result to redis")
                _save_result(file_id=file_id, results=pages)
            
            success_count = sum(1 for page in pages if page["status"])
            failed_count = len(pages) - success_count
            
            logger.info(
                f"Extraction completed for {filename}: "
                f"{success_count}/{len(pages)} pages successful"
            )

            # Stage 3: Fetching result (mirrors PDF flow)
            if on_progress:
                on_progress({
                    "stage": "fetching",
                    "percent": 100,
                    "message": "Finalizing extraction results...",
                    "completed_pages": len(pages),
                    "total_pages": len(pages),
                })

            return {
                "filename": filename,
                "total_pages": len(pages),
                "extracted_pages": pages,
                "success_count": success_count,
                "failed_count": failed_count,
                "file_type": "docx",
                "status": "success" if success_count > 0 else "failed",
                "errors": None
            }
            
        except Exception as e:
            logger.error(f"Extraction failed for {filename}: {e}")
            raise e