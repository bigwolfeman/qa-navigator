"""
UI Element Detector using YOLO-based object detection.

Detects buttons, checkboxes, inputs, tabs, menus, and other UI elements.

Windows desktop automation layer for QA Navigator.
"""

import cv2
import numpy as np
import logging
import time
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass
from pathlib import Path
import json

logger = logging.getLogger(__name__)


@dataclass
class UIElement:
    """Detected UI element with classification and confidence."""
    element_type: str  # button, checkbox, textbox, dropdown, menu, tab, etc.
    confidence: float
    bbox: Tuple[int, int, int, int]  # x, y, width, height
    center: Tuple[int, int]  # center coordinates
    area: int
    aspect_ratio: float
    properties: Dict[str, Any] = None  # Additional element-specific properties


@dataclass
class DetectionResult:
    """Complete detection result for a frame."""
    elements: List[UIElement]
    processing_time_ms: float
    image_size: Tuple[int, int]  # width, height
    detection_count: Dict[str, int]  # count by element type
    success: bool
    error_message: Optional[str] = None


class YOLODetector:
    """YOLO-based UI element detector."""

    # UI element classes (expandable)
    ELEMENT_CLASSES = [
        "button", "checkbox", "textbox", "dropdown", "menu",
        "tab", "slider", "radiobutton", "label", "icon",
        "window", "dialog", "toolbar", "scrollbar", "listitem"
    ]

    def __init__(self, model_path: Optional[str] = None, confidence_threshold: float = 0.7):
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = 0.4
        self.model_loaded = False
        self.net = None
        self.output_layers = []

        if model_path is None:
            model_path = Path(__file__).parent / "models" / "ui_detector_yolo.onnx"

        self.model_path = Path(model_path)
        self._load_model()

    def _load_model(self):
        """Load YOLO model."""
        try:
            if self.model_path.exists():
                self.net = cv2.dnn.readNetFromONNX(str(self.model_path))

                layer_names = self.net.getLayerNames()
                self.output_layers = [layer_names[i - 1] for i in self.net.getUnconnectedOutLayers()]

                self.model_loaded = True
                logger.info(f"YOLO model loaded successfully from {self.model_path}")
            else:
                logger.warning(f"YOLO model not found at {self.model_path}, using fallback detection")
                self.model_loaded = False
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}")
            self.model_loaded = False

    def detect_elements(self, image: np.ndarray) -> DetectionResult:
        """Detect UI elements in image."""
        start_time = time.time()

        try:
            if self.model_loaded:
                return self._detect_with_yolo(image, start_time)
            else:
                return self._detect_with_fallback(image, start_time)
        except Exception as e:
            processing_time = (time.time() - start_time) * 1000
            logger.error(f"UI detection failed: {e}")

            return DetectionResult(
                elements=[],
                processing_time_ms=processing_time,
                image_size=(image.shape[1], image.shape[0]),
                detection_count={},
                success=False,
                error_message=str(e)
            )

    def _detect_with_yolo(self, image: np.ndarray, start_time: float) -> DetectionResult:
        """Detect using YOLO model."""
        height, width = image.shape[:2]

        blob = cv2.dnn.blobFromImage(
            image, 1/255.0, (416, 416), swapRB=True, crop=False
        )
        self.net.setInput(blob)

        outputs = self.net.forward(self.output_layers)

        boxes = []
        confidences = []
        class_ids = []

        for output in outputs:
            for detection in output:
                scores = detection[5:]
                class_id = np.argmax(scores)
                confidence = scores[class_id]

                if confidence > self.confidence_threshold:
                    center_x = int(detection[0] * width)
                    center_y = int(detection[1] * height)
                    w = int(detection[2] * width)
                    h = int(detection[3] * height)

                    x = int(center_x - w/2)
                    y = int(center_y - h/2)

                    boxes.append([x, y, w, h])
                    confidences.append(float(confidence))
                    class_ids.append(class_id)

        indices = cv2.dnn.NMSBoxes(
            boxes, confidences, self.confidence_threshold, self.nms_threshold
        )

        elements = []
        detection_count = {}

        if len(indices) > 0:
            for i in indices.flatten():
                x, y, w, h = boxes[i]
                class_id = class_ids[i]
                confidence = confidences[i]

                element_type = self.ELEMENT_CLASSES[class_id] if class_id < len(self.ELEMENT_CLASSES) else "unknown"

                element = UIElement(
                    element_type=element_type,
                    confidence=confidence,
                    bbox=(x, y, w, h),
                    center=(x + w//2, y + h//2),
                    area=w * h,
                    aspect_ratio=w / max(h, 1),
                    properties={
                        "detected_by": "yolo",
                        "class_id": class_id
                    }
                )

                elements.append(element)
                detection_count[element_type] = detection_count.get(element_type, 0) + 1

        processing_time = (time.time() - start_time) * 1000

        return DetectionResult(
            elements=elements,
            processing_time_ms=processing_time,
            image_size=(width, height),
            detection_count=detection_count,
            success=True
        )

    def _detect_with_fallback(self, image: np.ndarray, start_time: float) -> DetectionResult:
        """Fallback detection using traditional computer vision."""
        height, width = image.shape[:2]
        elements = []
        detection_count = {}

        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()

        elements.extend(self._detect_rectangular_elements(gray))
        elements.extend(self._detect_text_regions(gray))
        elements.extend(self._detect_buttons_and_controls(gray))
        elements.extend(self._detect_menu_items(gray))

        for element in elements:
            detection_count[element.element_type] = detection_count.get(element.element_type, 0) + 1

        processing_time = (time.time() - start_time) * 1000

        return DetectionResult(
            elements=elements,
            processing_time_ms=processing_time,
            image_size=(width, height),
            detection_count=detection_count,
            success=True
        )

    def _detect_rectangles(self, gray: np.ndarray, element_type: str = "button") -> List[UIElement]:
        """Detect rectangular UI elements using edge detection."""
        elements = []

        edges = cv2.Canny(gray, 50, 150, apertureSize=3)

        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            epsilon = 0.02 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)

            if len(approx) >= 4:
                x, y, w, h = cv2.boundingRect(contour)
                area = w * h

                if 20 <= w <= 400 and 15 <= h <= 100 and area > 300:
                    aspect_ratio = w / h

                    if 2.0 <= aspect_ratio <= 8.0:
                        detected_type = "button"
                    elif 0.8 <= aspect_ratio <= 1.2:
                        detected_type = "checkbox"
                    elif aspect_ratio > 8.0:
                        detected_type = "textbox"
                    else:
                        detected_type = element_type

                    element = UIElement(
                        element_type=detected_type,
                        confidence=0.6,
                        bbox=(x, y, w, h),
                        center=(x + w//2, y + h//2),
                        area=area,
                        aspect_ratio=aspect_ratio,
                        properties={
                            "detected_by": "fallback_rectangle",
                            "vertices": len(approx)
                        }
                    )
                    elements.append(element)

        return elements

    def _detect_checkboxes(self, gray: np.ndarray) -> List[UIElement]:
        """Detect checkbox elements."""
        elements = []

        checkbox_templates = self._generate_checkbox_templates()

        for template, template_name in checkbox_templates:
            if template.shape[0] > gray.shape[0] or template.shape[1] > gray.shape[1]:
                continue

            result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
            locations = np.where(result >= 0.6)

            for pt in zip(*locations[::-1]):
                x, y = pt
                w, h = template.shape[1], template.shape[0]

                element = UIElement(
                    element_type="checkbox",
                    confidence=float(result[y, x]),
                    bbox=(x, y, w, h),
                    center=(x + w//2, y + h//2),
                    area=w * h,
                    aspect_ratio=w / h,
                    properties={
                        "detected_by": "template_matching",
                        "template": template_name
                    }
                )
                elements.append(element)

        return elements

    def _detect_text_regions(self, gray: np.ndarray) -> List[UIElement]:
        """Detect text input regions."""
        elements = []

        mser = cv2.MSER_create()
        regions, _ = mser.detectRegions(gray)

        for region in regions:
            if len(region) < 10:
                continue

            x, y, w, h = cv2.boundingRect(region)
            aspect_ratio = w / h

            if aspect_ratio > 3.0 and w > 50 and 15 <= h <= 40:
                element = UIElement(
                    element_type="textbox",
                    confidence=0.5,
                    bbox=(x, y, w, h),
                    center=(x + w//2, y + h//2),
                    area=w * h,
                    aspect_ratio=aspect_ratio,
                    properties={
                        "detected_by": "mser",
                        "region_size": len(region)
                    }
                )
                elements.append(element)

        return elements

    def _generate_checkbox_templates(self) -> List[Tuple[np.ndarray, str]]:
        """Generate checkbox templates for matching."""
        templates = []

        empty_box = np.zeros((20, 20), dtype=np.uint8)
        cv2.rectangle(empty_box, (2, 2), (17, 17), 255, 1)
        templates.append((empty_box, "empty_checkbox"))

        checked_box = empty_box.copy()
        cv2.line(checked_box, (5, 10), (8, 13), 255, 2)
        cv2.line(checked_box, (8, 13), (14, 7), 255, 2)
        templates.append((checked_box, "checked_checkbox"))

        radio_button = np.zeros((20, 20), dtype=np.uint8)
        cv2.circle(radio_button, (10, 10), 8, 255, 1)
        templates.append((radio_button, "radio_button"))

        return templates

    def _detect_rectangular_elements(self, gray: np.ndarray) -> List[UIElement]:
        """Detect rectangular UI elements using improved edge detection."""
        elements = []

        edges1 = cv2.Canny(gray, 50, 150, apertureSize=3)
        edges2 = cv2.Canny(gray, 100, 200, apertureSize=3)
        edges = cv2.bitwise_or(edges1, edges2)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            epsilon = 0.02 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)

            if len(approx) >= 4:
                x, y, w, h = cv2.boundingRect(contour)
                area = w * h

                if 20 <= w <= 400 and 15 <= h <= 100 and area > 300:
                    aspect_ratio = w / h

                    if 2.0 <= aspect_ratio <= 8.0 and h >= 20:
                        detected_type = "button"
                        confidence = 0.7
                    elif 0.8 <= aspect_ratio <= 1.2 and 15 <= w <= 25 and 15 <= h <= 25:
                        detected_type = "checkbox"
                        confidence = 0.8
                    elif aspect_ratio > 8.0 and h >= 15:
                        detected_type = "textbox"
                        confidence = 0.6
                    elif 1.5 <= aspect_ratio <= 3.0 and h >= 15:
                        detected_type = "label"
                        confidence = 0.5
                    else:
                        detected_type = "button"
                        confidence = 0.4

                    element = UIElement(
                        element_type=detected_type,
                        confidence=confidence,
                        bbox=(x, y, w, h),
                        center=(x + w//2, y + h//2),
                        area=area,
                        aspect_ratio=aspect_ratio,
                        properties={
                            "detected_by": "enhanced_rectangle",
                            "vertices": len(approx),
                            "contour_area": cv2.contourArea(contour)
                        }
                    )
                    elements.append(element)

        return elements

    def _detect_buttons_and_controls(self, gray: np.ndarray) -> List[UIElement]:
        """Detect buttons and control elements using template matching."""
        elements = []

        button_templates = self._generate_button_templates()

        for template, template_name in button_templates:
            if template.shape[0] > gray.shape[0] or template.shape[1] > gray.shape[1]:
                continue

            result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
            locations = np.where(result >= 0.5)

            for pt in zip(*locations[::-1]):
                x, y = pt
                w, h = template.shape[1], template.shape[0]

                is_duplicate = False
                for existing in elements:
                    if (abs(existing.bbox[0] - x) < 10 and
                            abs(existing.bbox[1] - y) < 10):
                        is_duplicate = True
                        break

                if not is_duplicate:
                    element = UIElement(
                        element_type="button",
                        confidence=float(result[y, x]),
                        bbox=(x, y, w, h),
                        center=(x + w//2, y + h//2),
                        area=w * h,
                        aspect_ratio=w / h,
                        properties={
                            "detected_by": "template_matching",
                            "template": template_name
                        }
                    )
                    elements.append(element)

        return elements

    def _detect_menu_items(self, gray: np.ndarray) -> List[UIElement]:
        """Detect menu items and navigation elements."""
        elements = []

        horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
        horizontal_lines = cv2.morphologyEx(gray, cv2.MORPH_OPEN, horizontal_kernel)

        contours, _ = cv2.findContours(horizontal_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)

            if w > 100 and h < 30 and h > 5:
                element = UIElement(
                    element_type="menu",
                    confidence=0.6,
                    bbox=(x, y, w, h),
                    center=(x + w//2, y + h//2),
                    area=w * h,
                    aspect_ratio=w / h,
                    properties={
                        "detected_by": "horizontal_line",
                        "line_length": w
                    }
                )
                elements.append(element)

        return elements

    def _generate_button_templates(self) -> List[Tuple[np.ndarray, str]]:
        """Generate button templates for matching."""
        templates = []

        button = np.zeros((30, 80), dtype=np.uint8)
        cv2.rectangle(button, (2, 2), (77, 27), 255, 2)
        templates.append((button, "standard_button"))

        small_button = np.zeros((25, 60), dtype=np.uint8)
        cv2.rectangle(small_button, (2, 2), (57, 22), 255, 2)
        templates.append((small_button, "small_button"))

        rounded_button = np.zeros((30, 80), dtype=np.uint8)
        cv2.rectangle(rounded_button, (5, 2), (74, 27), 255, 2)
        templates.append((rounded_button, "rounded_button"))

        return templates


class UIElementFilter:
    """Filter and refine detected UI elements."""

    def __init__(self, min_confidence: float = 0.5):
        self.min_confidence = min_confidence

    def filter_overlapping(self, elements: List[UIElement], overlap_threshold: float = 0.5) -> List[UIElement]:
        """Remove overlapping elements, keeping highest confidence."""
        if not elements:
            return []

        sorted_elements = sorted(elements, key=lambda x: x.confidence, reverse=True)
        filtered = []

        for element in sorted_elements:
            overlaps = False
            for accepted in filtered:
                if self._calculate_overlap(element.bbox, accepted.bbox) > overlap_threshold:
                    overlaps = True
                    break

            if not overlaps:
                filtered.append(element)

        return filtered

    def filter_by_confidence(self, elements: List[UIElement]) -> List[UIElement]:
        """Filter elements by minimum confidence."""
        return [elem for elem in elements if elem.confidence >= self.min_confidence]

    def filter_by_size(self, elements: List[UIElement], min_area: int = 100, max_area: int = 50000) -> List[UIElement]:
        """Filter elements by area."""
        return [elem for elem in elements if min_area <= elem.area <= max_area]

    def _calculate_overlap(self, bbox1: Tuple[int, int, int, int], bbox2: Tuple[int, int, int, int]) -> float:
        """Calculate overlap ratio between two bounding boxes."""
        x1, y1, w1, h1 = bbox1
        x2, y2, w2, h2 = bbox2

        left = max(x1, x2)
        top = max(y1, y2)
        right = min(x1 + w1, x2 + w2)
        bottom = min(y1 + h1, y2 + h2)

        if left >= right or top >= bottom:
            return 0.0

        intersection_area = (right - left) * (bottom - top)
        union_area = w1 * h1 + w2 * h2 - intersection_area

        return intersection_area / union_area if union_area > 0 else 0.0


class DetectionPerformanceMonitor:
    """Monitor detection performance."""

    def __init__(self):
        self.stats = {
            "total_detections": 0,
            "successful_detections": 0,
            "avg_processing_time": 0.0,
            "avg_elements_per_frame": 0.0,
            "element_type_counts": {}
        }

    def record_detection(self, result: DetectionResult):
        """Record detection statistics."""
        self.stats["total_detections"] += 1

        if result.success:
            self.stats["successful_detections"] += 1

            current_avg = self.stats["avg_processing_time"]
            new_avg = ((current_avg * (self.stats["total_detections"] - 1)) + result.processing_time_ms) / self.stats["total_detections"]
            self.stats["avg_processing_time"] = new_avg

            current_elem_avg = self.stats["avg_elements_per_frame"]
            new_elem_avg = ((current_elem_avg * (self.stats["successful_detections"] - 1)) + len(result.elements)) / self.stats["successful_detections"]
            self.stats["avg_elements_per_frame"] = new_elem_avg

            for element_type, count in result.detection_count.items():
                self.stats["element_type_counts"][element_type] = self.stats["element_type_counts"].get(element_type, 0) + count

    def get_performance_report(self) -> Dict[str, Any]:
        """Get performance statistics."""
        success_rate = (self.stats["successful_detections"] / max(1, self.stats["total_detections"])) * 100

        return {
            "success_rate_percent": round(success_rate, 2),
            "avg_processing_time_ms": round(self.stats["avg_processing_time"], 2),
            "avg_elements_per_frame": round(self.stats["avg_elements_per_frame"], 2),
            "total_detections": self.stats["total_detections"],
            "element_type_distribution": self.stats["element_type_counts"]
        }


# Global detector instance
_detector = None
_element_filter = None
_performance_monitor = DetectionPerformanceMonitor()


def initialize_detector(confidence_threshold: float = 0.7, model_path: Optional[str] = None):
    """Initialize global UI detector."""
    global _detector, _element_filter
    _detector = YOLODetector(model_path, confidence_threshold)
    _element_filter = UIElementFilter(confidence_threshold * 0.7)


def detect_ui_elements(image: np.ndarray, filter_overlaps: bool = True) -> DetectionResult:
    """Global UI element detection function."""
    if not _detector:
        raise RuntimeError("UI detector not initialized. Call initialize_detector() first.")

    result = _detector.detect_elements(image)

    if result.success and filter_overlaps:
        filtered_elements = _element_filter.filter_by_confidence(result.elements)
        filtered_elements = _element_filter.filter_overlapping(filtered_elements)
        filtered_elements = _element_filter.filter_by_size(filtered_elements)

        result.elements = filtered_elements
        result.detection_count = {}
        for element in filtered_elements:
            result.detection_count[element.element_type] = result.detection_count.get(element.element_type, 0) + 1

    _performance_monitor.record_detection(result)
    return result


def get_detection_performance() -> Dict[str, Any]:
    """Get detection performance statistics."""
    return _performance_monitor.get_performance_report()
