"""Typed file processors for per-kind ingestion."""

from ._types import FileArtefact, FileProcessor, ProcessorContext
from .av import AVFileProcessor
from .document import DocumentFileProcessor
from .image import ImageFileProcessor
from .text import TextFileProcessor

__all__ = [
    "AVFileProcessor",
    "DocumentFileProcessor",
    "FileArtefact",
    "FileProcessor",
    "ImageFileProcessor",
    "ProcessorContext",
    "TextFileProcessor",
]
