"""
OCR Engine with Windows OCR API and Tesseract fallback.

Supports multiple languages, DPI scaling, and performance optimization.

Ported from WindowsHarness.
"""

import asyncio
import logging
import time
from typing import List, Optional, Tuple, Dict, Any
from dataclasses import dataclass
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
import pytesseract

try:
    import winrt.windows.media.ocr as ocr
    import winrt.windows.graphics.imaging as imaging
    import winrt.windows.storage.streams as streams
    WINDOWS_OCR_AVAILABLE = True
except ImportError:
    WINDOWS_OCR_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class TextBlock:
    """Represents a detected text block with metadata."""
    text: str
    confidence: float
    bbox: Tuple[int, int, int, int]  # x, y, width, height
    orientation: float = 0.0
    language: str = "en"
    script: str = "latin"
    font_size: Optional[int] = None


@dataclass
class OCRResult:
    """Complete OCR result with all detected text blocks."""
    blocks: List[TextBlock]
    total_text: str
    processing_time_ms: float
    engine_used: str
    success: bool
    error_message: Optional[str] = None


class WindowsOCR:
    """Windows 10+ OCR API implementation."""

    def __init__(self):
        self.engine = None
        self.supported_languages = []
        self._initialize()

    def _initialize(self):
        """Initialize Windows OCR engine."""
        if not WINDOWS_OCR_AVAILABLE:
            logger.warning("Windows OCR not available")
            return

        try:
            languages = ocr.OcrEngine.get_available_recognizer_languages()
            self.supported_languages = [lang.language_tag for lang in languages]

            self.engine = ocr.OcrEngine.try_create_from_language(
                languages[0] if languages else None
            )

            logger.info(f"Windows OCR initialized with languages: {self.supported_languages}")

        except Exception as e:
            logger.error(f"Failed to initialize Windows OCR: {e}")
            self.engine = None

    async def recognize(self, image: np.ndarray, language: str = "en") -> OCRResult:
        """Recognize text using Windows OCR API."""
        start_time = time.time()

        if not self.engine:
            return OCRResult(
                blocks=[], total_text="", processing_time_ms=0,
                engine_used="windows", success=False,
                error_message="Windows OCR not available"
            )

        try:
            if len(image.shape) == 3:
                pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            else:
                pil_image = Image.fromarray(image)

            import io
            img_bytes = io.BytesIO()
            pil_image.save(img_bytes, format='PNG')
            img_bytes.seek(0)

            stream = streams.InMemoryRandomAccessStream()
            writer = streams.DataWriter(stream.get_output_stream())
            writer.write_bytes(img_bytes.read())
            await writer.store_async()
            stream.seek(0)

            decoder = await imaging.BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()

            result = await self.engine.recognize_async(bitmap)

            blocks = []
            full_text = []

            for line in result.lines:
                bbox = (
                    int(line.bounding_rect.x),
                    int(line.bounding_rect.y),
                    int(line.bounding_rect.width),
                    int(line.bounding_rect.height)
                )

                block = TextBlock(
                    text=line.text,
                    confidence=1.0,  # Windows OCR doesn't provide confidence
                    bbox=bbox,
                    language=language,
                    script="latin"
                )
                blocks.append(block)
                full_text.append(line.text)

            processing_time = (time.time() - start_time) * 1000

            return OCRResult(
                blocks=blocks,
                total_text="\n".join(full_text),
                processing_time_ms=processing_time,
                engine_used="windows",
                success=True
            )

        except Exception as e:
            processing_time = (time.time() - start_time) * 1000
            logger.error(f"Windows OCR failed: {e}")

            return OCRResult(
                blocks=[], total_text="", processing_time_ms=processing_time,
                engine_used="windows", success=False,
                error_message=str(e)
            )


class TesseractOCR:
    """Tesseract OCR fallback implementation."""

    def __init__(self, tesseract_path: Optional[str] = None):
        if tesseract_path is None:
            import platform
            if platform.system() == "Windows":
                tesseract_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path

        self.available_languages = self._get_available_languages()
        logger.info(f"Tesseract OCR initialized with languages: {self.available_languages}")

    def _get_available_languages(self) -> List[str]:
        """Get available Tesseract languages."""
        try:
            langs = pytesseract.get_languages()
            return langs
        except Exception as e:
            logger.warning(f"Could not get Tesseract languages: {e}")
            return ["eng"]

    def _preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """Preprocess image for better OCR accuracy - optimized for screen text."""
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()

        return gray

    def _standard_preprocessing(self, gray: np.ndarray) -> np.ndarray:
        """Standard preprocessing with adaptive thresholding."""
        processed = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        processed = cv2.fastNlMeansDenoising(processed)

        return processed

    def _high_contrast_preprocessing(self, gray: np.ndarray) -> np.ndarray:
        """High contrast preprocessing for better character separation."""
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)

        _, processed = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        return processed

    def _denoised_preprocessing(self, gray: np.ndarray) -> np.ndarray:
        """Advanced denoising preprocessing."""
        denoised = cv2.bilateralFilter(gray, 9, 75, 75)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        processed = cv2.morphologyEx(denoised, cv2.MORPH_OPEN, kernel)

        processed = cv2.adaptiveThreshold(
            processed, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 3
        )

        return processed

    def _scaled_preprocessing(self, gray: np.ndarray) -> np.ndarray:
        """Scale up small images for better OCR accuracy."""
        height, width = gray.shape

        if height < 100 or width < 200:
            scale_factor = max(2, 100 // min(height, width))
            scaled = cv2.resize(
                gray, None, fx=scale_factor, fy=scale_factor,
                interpolation=cv2.INTER_CUBIC
            )
        else:
            scaled = gray

        blurred = cv2.GaussianBlur(scaled, (3, 3), 0)

        processed = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        return processed

    def _morphological_preprocessing(self, gray: np.ndarray) -> np.ndarray:
        """Morphological preprocessing to clean up characters."""
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        closed = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)

        opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel)

        processed = cv2.adaptiveThreshold(
            opened, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        return processed

    def _select_best_preprocessed_image(self, images: List[np.ndarray]) -> np.ndarray:
        """Select the best preprocessed image based on contrast and edge density."""
        best_image = images[0]
        best_score = 0

        for img in images:
            contrast_score = self._calculate_contrast_score(img)
            edge_score = self._calculate_edge_density(img)
            total_score = contrast_score * 0.6 + edge_score * 0.4

            if total_score > best_score:
                best_score = total_score
                best_image = img

        return best_image

    def _calculate_contrast_score(self, image: np.ndarray) -> float:
        """Calculate contrast score for image quality assessment."""
        mean, std = cv2.meanStdDev(image)
        return float(std[0])

    def _calculate_edge_density(self, image: np.ndarray) -> float:
        """Calculate edge density for character clarity assessment."""
        edges = cv2.Canny(image, 50, 150)
        edge_pixels = np.sum(edges > 0)
        total_pixels = image.shape[0] * image.shape[1]
        return edge_pixels / total_pixels

    async def recognize(self, image: np.ndarray, language: str = "eng") -> OCRResult:
        """Recognize text using Tesseract OCR."""
        start_time = time.time()

        try:
            lang_map = {"en": "eng", "zh": "chi_sim", "ja": "jpn", "ko": "kor"}
            tess_lang = lang_map.get(language, language)

            if tess_lang not in self.available_languages:
                tess_lang = "eng"

            processed_image = self._preprocess_image(image)

            config = "--psm 6 --oem 3"

            data = pytesseract.image_to_data(
                processed_image, lang=tess_lang, config=config, output_type=pytesseract.Output.DICT
            )

            blocks = []
            full_text = []

            n_boxes = len(data['level'])
            for i in range(n_boxes):
                confidence = float(data['conf'][i])
                text = data['text'][i].strip()

                if confidence > 10 and text:
                    bbox = (
                        int(data['left'][i]),
                        int(data['top'][i]),
                        int(data['width'][i]),
                        int(data['height'][i])
                    )

                    block = TextBlock(
                        text=text,
                        confidence=confidence / 100.0,
                        bbox=bbox,
                        language=tess_lang,
                        script="latin"
                    )
                    blocks.append(block)
                    full_text.append(text)

            processing_time = (time.time() - start_time) * 1000

            return OCRResult(
                blocks=blocks,
                total_text=" ".join(full_text),
                processing_time_ms=processing_time,
                engine_used="tesseract",
                success=True
            )

        except Exception as e:
            processing_time = (time.time() - start_time) * 1000
            logger.error(f"Tesseract OCR failed: {e}")

            return OCRResult(
                blocks=[], total_text="", processing_time_ms=processing_time,
                engine_used="tesseract", success=False,
                error_message=str(e)
            )


class OCREngine:
    """Main OCR engine with fallback support."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.primary_engine = config.get("ocr_engine", "windows")
        self.confidence_threshold = config.get("confidence_threshold", 0.7)

        self.windows_ocr = WindowsOCR() if WINDOWS_OCR_AVAILABLE else None
        self.tesseract_ocr = TesseractOCR()

        logger.info(f"OCR Engine initialized with primary: {self.primary_engine}")

    async def recognize_text(
        self,
        image: np.ndarray,
        language: str = "en",
        region: Optional[Tuple[int, int, int, int]] = None
    ) -> OCRResult:
        """
        Recognize text in image with automatic fallback.

        Args:
            image: Input image as numpy array
            language: Language code (en, zh, ja, ko, etc.)
            region: Optional region (x, y, width, height) to analyze

        Returns:
            OCRResult with detected text blocks
        """
        if region:
            x, y, w, h = region
            image = image[y:y+h, x:x+w]

        if self.primary_engine == "windows" and self.windows_ocr:
            result = await self.windows_ocr.recognize(image, language)
            if result.success and self._is_result_acceptable(result):
                return result

            logger.warning("Windows OCR failed or low quality, falling back to Tesseract")

        result = await self.tesseract_ocr.recognize(image, language)

        if not result.success:
            logger.error("All OCR engines failed")

        return result

    def _is_result_acceptable(self, result: OCRResult) -> bool:
        """Check if OCR result meets quality threshold."""
        if not result.blocks:
            return False

        avg_confidence = sum(block.confidence for block in result.blocks) / len(result.blocks)
        return avg_confidence >= self.confidence_threshold

    async def find_text(
        self,
        image: np.ndarray,
        search_text: str,
        language: str = "en",
        fuzzy_match: bool = True
    ) -> List[TextBlock]:
        """
        Find specific text in image.

        Args:
            image: Input image
            search_text: Text to search for
            language: Language for OCR
            fuzzy_match: Allow approximate matching

        Returns:
            List of matching text blocks
        """
        result = await self.recognize_text(image, language)

        if not result.success:
            return []

        matches = []
        search_lower = search_text.lower()

        for block in result.blocks:
            block_lower = block.text.lower()

            if fuzzy_match:
                if search_lower in block_lower or block_lower in search_lower:
                    matches.append(block)
                elif self._calculate_similarity(search_lower, block_lower) > 0.8:
                    matches.append(block)
            else:
                if search_lower == block_lower:
                    matches.append(block)

        return matches

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate text similarity using simple ratio."""
        longer = text1 if len(text1) > len(text2) else text2
        shorter = text2 if longer == text1 else text1

        if len(longer) == 0:
            return 1.0

        matches = sum(1 for a, b in zip(longer, shorter) if a == b)
        return matches / len(longer)


class OCRPerformanceMonitor:
    """Monitor OCR performance and optimization."""

    def __init__(self):
        self.stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "avg_processing_time": 0.0,
            "engine_usage": {"windows": 0, "tesseract": 0}
        }

    def record_request(self, result: OCRResult):
        """Record OCR request statistics."""
        self.stats["total_requests"] += 1

        if result.success:
            self.stats["successful_requests"] += 1

        current_avg = self.stats["avg_processing_time"]
        new_avg = ((current_avg * (self.stats["total_requests"] - 1)) + result.processing_time_ms) / self.stats["total_requests"]
        self.stats["avg_processing_time"] = new_avg

        self.stats["engine_usage"][result.engine_used] += 1

    def get_performance_report(self) -> Dict[str, Any]:
        """Get performance statistics."""
        success_rate = (self.stats["successful_requests"] / max(1, self.stats["total_requests"])) * 100

        return {
            "success_rate_percent": round(success_rate, 2),
            "avg_processing_time_ms": round(self.stats["avg_processing_time"], 2),
            "total_requests": self.stats["total_requests"],
            "engine_usage": self.stats["engine_usage"]
        }


# Global OCR instance
_ocr_engine = None
_performance_monitor = OCRPerformanceMonitor()


def initialize_ocr(config: Dict[str, Any]):
    """Initialize global OCR engine."""
    global _ocr_engine
    _ocr_engine = OCREngine(config)


async def recognize_text(image: np.ndarray, language: str = "en", region: Optional[Tuple[int, int, int, int]] = None) -> OCRResult:
    """Global OCR function."""
    if not _ocr_engine:
        raise RuntimeError("OCR engine not initialized. Call initialize_ocr() first.")

    result = await _ocr_engine.recognize_text(image, language, region)
    _performance_monitor.record_request(result)
    return result


async def find_text(image: np.ndarray, search_text: str, language: str = "en", fuzzy_match: bool = True) -> List[TextBlock]:
    """Global text search function."""
    if not _ocr_engine:
        raise RuntimeError("OCR engine not initialized. Call initialize_ocr() first.")

    return await _ocr_engine.find_text(image, search_text, language, fuzzy_match)


def get_ocr_performance() -> Dict[str, Any]:
    """Get OCR performance statistics."""
    return _performance_monitor.get_performance_report()
