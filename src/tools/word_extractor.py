from typing import Optional, Any
from loguru import logger
from io import BytesIO
import re

class MarkdownProcessor:
    """Utilities for processing and formatting extracted content as Markdown."""
    
    @staticmethod
    def process_title(text: str) -> str:
        """
        Process title elements with proper Markdown formatting.
        
        Args:
            text: Raw title text
            
        Returns:
            Markdown-formatted title
        """
        clean_text = text.strip()
        if not clean_text:
            return ""
        return f"## {clean_text}"
    
    @staticmethod
    def process_text(text: str) -> str:
        """
        Process general text elements.
        
        Args:
            text: Raw text content
            
        Returns:
            Cleaned text
        """
        return text.strip()
    
    @staticmethod
    def process_list_item(text: str) -> str:
        """
        Process list items with proper Markdown formatting.
        
        Args:
            text: Raw list item text
            
        Returns:
            Markdown-formatted list item
        """
        clean_text = text.strip()
        if not clean_text:
            return ""
        
        # Check if it's a numbered list (starts with number and period/parenthesis)
        numbered_pattern = r'^\d+[\.\)]\s+'
        if re.match(numbered_pattern, clean_text):
            # Already has numbering, keep it as is
            return clean_text
        
        return f"- {clean_text}"
    
    @staticmethod
    def process_table(element) -> str:
        """
        Process table elements - returns HTML table for markdown compatibility.
        
        Args:
            element: Unstructured table element
            
        Returns:
            HTML table (which markdown renderers support)
        """
        # Try to extract HTML table from metadata
        html_table = MarkdownProcessor._extract_html_table(element)
        if html_table:
            # Clean up the HTML table for better markdown rendering
            html_table = re.sub(r'\s+', ' ', html_table)  # Normalize whitespace
            html_table = html_table.strip()
            return f"\n{html_table}\n"
        
        # Fallback: process as text table
        text = element.text.strip() if element.text else ""
        return text
    
    @staticmethod
    def _extract_html_table(element) -> Optional[str]:
        """
        Extract HTML table from element metadata.
        
        Args:
            element: Unstructured element
            
        Returns:
            HTML table string if found, None otherwise
        """
        if not hasattr(element, 'metadata') or not element.metadata:
            return None
            
        metadata = element.metadata
        
        # Check various possible metadata attributes for HTML table
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
        """
        Process a single document element.
        
        Args:
            element: Unstructured element to process
            
        Returns:
            Tuple of (element_type, processed_content, is_page_break)
        """
        # Get element type
        element_type = element.category if hasattr(element, 'category') and element.category else type(element).__name__
        text = element.text.strip() if element.text else ""
        
        # Handle page breaks
        if element_type == "PageBreak":
            return element_type, "", True
        
        # Skip empty elements
        if not text:
            return element_type, "", False
        
        # Process based on element type
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
        """
        Format page content by simply joining elements.
        
        Args:
            content: List of (element_type, content) tuples
            
        Returns:
            Formatted content for the page
        """
        if not content:
            return ""
        
        # Just extract the content and join with newlines
        formatted_lines = [element_content for _, element_content in content]
        
        # Join all lines
        result = "\n".join(formatted_lines)
        
        # Clean up: remove trailing whitespace from lines
        result = '\n'.join(line.rstrip() for line in result.split('\n'))
        
        return result.strip()


class WordDocumentExtractor:
    """
    Production-ready extractor for converting Word documents to Markdown format.
    
    Supports both .doc and .docx files with intelligent element processing,
    proper Markdown formatting, and comprehensive error handling.
    """
    
    SUPPORTED_EXTENSIONS = {'.doc', '.docx'}
    
    def __init__(self, infer_table_structure: bool = True):
        """
        Initialize the Word document extractor.
        
        Args:
            infer_table_structure: Whether to infer table structure during parsing
        """
        self.infer_table_structure = infer_table_structure
        self.element_processor = ElementProcessor()
        self.page_formatter = PageFormatter()
    
    def partition_document(self, file: BytesIO) -> list[Any]:
        """
        Partition the document into elements using the appropriate unstructured method.
        
        Args:
            file: Document file bytes
            
        Returns:
            List of document elements
            
        Raises:
            Exception: If partitioning fails
        """
        try:
            from unstructured.partition.docx import partition_docx
            return partition_docx(
                file=file,
                infer_table_structure=self.infer_table_structure
            )
        except Exception as e:
            raise e
    
    def process_elements_to_pages(self, elements: list[Any]) -> list[dict[str, Any]]:
        """
        Process document elements and organize them into pages.
        
        Args:
            elements: List of unstructured elements
            
        Returns:
            List of page dictionaries
        """
        pages = []
        current_page_content = []
        current_page_index = 0
        element_count = 0
        
        try:
            for element in elements:
                element_type, content, is_page_break = self.element_processor.process_element(element)
                element_count += 1
                
                # Handle page breaks
                if is_page_break:
                    # Finalize current page
                    if current_page_content:
                        page_text = self.page_formatter.format_page_content(current_page_content)
                        pages.append({
                            "page_index": current_page_index,
                            "text": page_text,
                            "status": bool(page_text.strip())
                        })

                    # Start new page
                    current_page_index += 1
                    current_page_content = []
                    continue
                
                # Add content to current page
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
    
            # If no pages were created, create a single page with all content
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
            
            return pages
            
        except Exception as e:
            raise e
        
    def extract_file(self, file: BytesIO, filename: str) -> dict[str, Any]:
        """
        Extract content from a Word document.
        
        Args:
            file: Document file bytes
            filename: Name of the document file
            
        Returns:
            Dictionary containing extraction results
            
        Raises:
            ValueError: If unsupported file format
        """
        if not any(filename.lower().endswith(ext) for ext in self.SUPPORTED_EXTENSIONS):
            raise ValueError(
                f"Unsupported file format for {filename}. "
                f"Supported formats: {', '.join(self.SUPPORTED_EXTENSIONS)}"
            )
        
        try:
            elements = self.partition_document(file=file)
            
            logger.info(f"Document partitioned into {len(elements)} elements")
            
            # Process elements into pages
            pages = self.process_elements_to_pages(elements=elements)
            
            # Calculate statistics
            success_count = sum(1 for page in pages if page["status"])
            failed_count = len(pages) - success_count
            
            logger.info(
                f"Extraction completed for {filename}: "
                f"{success_count}/{len(pages)} pages successful"
            )

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