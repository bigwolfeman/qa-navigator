"""
Spatial Memory System for UI Element Tracking.

Provides element fingerprinting, spatial relationships, and persistence across UI changes.

Windows desktop automation layer for QA Navigator.
"""

import hashlib
import logging
import time
import json
from typing import Dict, List, Optional, Tuple, Set, Any
from dataclasses import dataclass, asdict
from collections import defaultdict
import numpy as np
import cv2

from .detector import UIElement
from .ocr import TextBlock

logger = logging.getLogger(__name__)


@dataclass
class ElementFingerprint:
    """Unique fingerprint for UI element identification."""
    text_hash: str  # Hash of associated text
    icon_hash: str  # Hash of icon/image content
    aspect_ratio: float  # Width/height ratio
    relative_position: Tuple[float, float]  # Relative position in window (0-1)
    size_category: str  # small, medium, large
    element_type: str
    local_graph: Dict[str, List[str]]  # Spatial relationships
    confidence: float


@dataclass
class SpatialElement:
    """Element with spatial context and tracking info."""
    element_id: str  # Unique identifier
    current_element: Optional[UIElement]  # Current detection (None if lost)
    current_text: Optional[TextBlock]  # Associated text
    fingerprint: ElementFingerprint
    last_seen: float  # Timestamp
    track_count: int  # How many times we've seen this element
    stable_frames: int  # Consecutive frames where element was stable
    positions: List[Tuple[int, int, float]]  # Historical positions with timestamps
    confidence_history: List[float]  # Confidence over time


@dataclass
class SpatialRelationship:
    """Describes spatial relationship between two elements."""
    element1_id: str
    element2_id: str
    relationship_type: str  # "near", "left_of", "right_of", "above", "below", "within"
    distance: float  # Pixel distance
    angle: float  # Angle in degrees
    confidence: float


class ElementTracker:
    """Tracks UI elements across frames with spatial memory."""

    def __init__(self, max_tracking_age: float = 30.0, similarity_threshold: float = 0.8):
        self.max_tracking_age = max_tracking_age  # Max age in seconds
        self.similarity_threshold = similarity_threshold
        self.tracked_elements: Dict[str, SpatialElement] = {}
        self.spatial_relationships: List[SpatialRelationship] = []
        self.frame_count = 0

    def update_frame(self, elements: List[UIElement], text_blocks: List[TextBlock],
                     image: np.ndarray, timestamp: Optional[float] = None) -> List[SpatialElement]:
        """Update tracker with new frame data."""
        if timestamp is None:
            timestamp = time.time()

        self.frame_count += 1

        element_text_pairs = self._associate_text_with_elements(elements, text_blocks)

        current_fingerprints = []
        for element, text in element_text_pairs:
            fingerprint = self._generate_fingerprint(element, text, image, elements)
            current_fingerprints.append((element, text, fingerprint))

        matched_elements = self._match_elements(current_fingerprints, timestamp)

        self._cleanup_old_elements(timestamp)

        self._update_spatial_relationships(timestamp)

        return list(self.tracked_elements.values())

    def _associate_text_with_elements(self, elements: List[UIElement],
                                      text_blocks: List[TextBlock]) -> List[Tuple[UIElement, Optional[TextBlock]]]:
        """Associate text blocks with UI elements based on proximity."""
        associations = []
        used_text = set()

        for element in elements:
            ex, ey, ew, eh = element.bbox
            element_center = (ex + ew//2, ey + eh//2)

            closest_text = None
            min_distance = float('inf')

            for i, text_block in enumerate(text_blocks):
                if i in used_text:
                    continue

                tx, ty, tw, th = text_block.bbox
                text_center = (tx + tw//2, ty + th//2)

                distance = np.sqrt((element_center[0] - text_center[0])**2 +
                                   (element_center[1] - text_center[1])**2)

                max_distance = max(ew, eh) * 2

                if distance < min_distance and distance <= max_distance:
                    min_distance = distance
                    closest_text = text_block
                    closest_text_idx = i

            if closest_text:
                used_text.add(closest_text_idx)

            associations.append((element, closest_text))

        return associations

    def _generate_fingerprint(self, element: UIElement, text: Optional[TextBlock],
                               image: np.ndarray, all_elements: List[UIElement]) -> ElementFingerprint:
        """Generate unique fingerprint for element."""
        text_content = text.text if text else ""
        text_hash = hashlib.md5(text_content.encode()).hexdigest()[:8]

        x, y, w, h = element.bbox
        icon_region = image[y:y+h, x:x+w] if y+h <= image.shape[0] and x+w <= image.shape[1] else np.zeros((h, w))
        icon_hash = hashlib.md5(icon_region.tobytes()).hexdigest()[:8]

        area = element.area
        if area < 1000:
            size_category = "small"
        elif area < 5000:
            size_category = "medium"
        else:
            size_category = "large"

        img_height, img_width = image.shape[:2]
        rel_x = (x + w//2) / img_width
        rel_y = (y + h//2) / img_height

        local_graph = self._build_local_graph(element, all_elements)

        return ElementFingerprint(
            text_hash=text_hash,
            icon_hash=icon_hash,
            aspect_ratio=element.aspect_ratio,
            relative_position=(rel_x, rel_y),
            size_category=size_category,
            element_type=element.element_type,
            local_graph=local_graph,
            confidence=element.confidence
        )

    def _build_local_graph(self, target_element: UIElement, all_elements: List[UIElement]) -> Dict[str, List[str]]:
        """Build local spatial graph for element."""
        graph = {
            "near": [],
            "left_of": [],
            "right_of": [],
            "above": [],
            "below": []
        }

        tx, ty, tw, th = target_element.bbox
        target_center = (tx + tw//2, ty + th//2)

        for element in all_elements:
            if element == target_element:
                continue

            ex, ey, ew, eh = element.bbox
            element_center = (ex + ew//2, ey + eh//2)

            distance = np.sqrt((target_center[0] - element_center[0])**2 +
                               (target_center[1] - element_center[1])**2)

            near_threshold = max(tw, th) * 2

            if distance <= near_threshold:
                graph["near"].append(element.element_type)

                dx = element_center[0] - target_center[0]
                dy = element_center[1] - target_center[1]

                if abs(dx) > abs(dy):
                    if dx > 0:
                        graph["right_of"].append(element.element_type)
                    else:
                        graph["left_of"].append(element.element_type)
                else:
                    if dy > 0:
                        graph["below"].append(element.element_type)
                    else:
                        graph["above"].append(element.element_type)

        for key in graph:
            graph[key] = sorted(graph[key])

        return graph

    def _match_elements(self, current_fingerprints: List[Tuple[UIElement, Optional[TextBlock], ElementFingerprint]],
                        timestamp: float) -> List[SpatialElement]:
        """Match current elements with tracked elements."""
        matched = []
        used_tracked = set()

        for element, text, fingerprint in current_fingerprints:
            best_match = None
            best_similarity = 0.0

            for element_id, tracked in self.tracked_elements.items():
                if element_id in used_tracked:
                    continue

                similarity = self._calculate_fingerprint_similarity(fingerprint, tracked.fingerprint)

                if similarity > best_similarity and similarity >= self.similarity_threshold:
                    best_similarity = similarity
                    best_match = element_id

            if best_match:
                tracked = self.tracked_elements[best_match]
                tracked.current_element = element
                tracked.current_text = text
                tracked.last_seen = timestamp
                tracked.track_count += 1

                center_x, center_y = element.center
                tracked.positions.append((center_x, center_y, timestamp))
                if len(tracked.positions) > 50:
                    tracked.positions = tracked.positions[-50:]

                tracked.confidence_history.append(element.confidence)
                if len(tracked.confidence_history) > 20:
                    tracked.confidence_history = tracked.confidence_history[-20:]

                if len(tracked.positions) >= 2:
                    prev_pos = tracked.positions[-2]
                    current_pos = tracked.positions[-1]
                    distance = np.sqrt((current_pos[0] - prev_pos[0])**2 + (current_pos[1] - prev_pos[1])**2)

                    if distance < 5:
                        tracked.stable_frames += 1
                    else:
                        tracked.stable_frames = 0

                used_tracked.add(best_match)
                matched.append(tracked)
            else:
                element_id = f"elem_{self.frame_count}_{len(self.tracked_elements)}"
                center_x, center_y = element.center

                tracked = SpatialElement(
                    element_id=element_id,
                    current_element=element,
                    current_text=text,
                    fingerprint=fingerprint,
                    last_seen=timestamp,
                    track_count=1,
                    stable_frames=0,
                    positions=[(center_x, center_y, timestamp)],
                    confidence_history=[element.confidence]
                )

                self.tracked_elements[element_id] = tracked
                matched.append(tracked)

        for element_id, tracked in self.tracked_elements.items():
            if element_id not in used_tracked:
                tracked.current_element = None
                tracked.current_text = None

        return matched

    def _calculate_fingerprint_similarity(self, fp1: ElementFingerprint, fp2: ElementFingerprint) -> float:
        """Calculate similarity between two fingerprints."""
        score = 0.0

        if fp1.text_hash == fp2.text_hash:
            score += 0.3

        if fp1.icon_hash == fp2.icon_hash:
            score += 0.2

        if fp1.element_type == fp2.element_type:
            score += 0.25

        aspect_diff = abs(fp1.aspect_ratio - fp2.aspect_ratio)
        aspect_similarity = max(0, 1.0 - aspect_diff / 2.0)
        score += 0.1 * aspect_similarity

        pos_diff = np.sqrt((fp1.relative_position[0] - fp2.relative_position[0])**2 +
                           (fp1.relative_position[1] - fp2.relative_position[1])**2)
        pos_similarity = max(0, 1.0 - pos_diff * 2)
        score += 0.15 * pos_similarity

        return score

    def _cleanup_old_elements(self, timestamp: float):
        """Remove elements that haven't been seen recently."""
        to_remove = []

        for element_id, tracked in self.tracked_elements.items():
            age = timestamp - tracked.last_seen
            if age > self.max_tracking_age:
                to_remove.append(element_id)

        for element_id in to_remove:
            del self.tracked_elements[element_id]
            logger.debug(f"Removed old tracked element: {element_id}")

    def _update_spatial_relationships(self, timestamp: float):
        """Update spatial relationships between elements."""
        current_elements = [(eid, elem) for eid, elem in self.tracked_elements.items()
                            if elem.current_element is not None]

        new_relationships = []

        for i, (id1, elem1) in enumerate(current_elements):
            for j, (id2, elem2) in enumerate(current_elements[i+1:], i+1):
                relationships = self._calculate_spatial_relationships(elem1, elem2)

                for rel in relationships:
                    rel.element1_id = id1
                    rel.element2_id = id2
                    new_relationships.append(rel)

        self.spatial_relationships = [rel for rel in new_relationships if rel.confidence > 0.7]

    def _calculate_spatial_relationships(self, elem1: SpatialElement, elem2: SpatialElement) -> List[SpatialRelationship]:
        """Calculate spatial relationships between two elements."""
        if not elem1.current_element or not elem2.current_element:
            return []

        relationships = []

        center1 = elem1.current_element.center
        center2 = elem2.current_element.center

        dx = center2[0] - center1[0]
        dy = center2[1] - center1[1]
        distance = np.sqrt(dx**2 + dy**2)
        angle = np.degrees(np.arctan2(dy, dx))

        confidence = min(elem1.current_element.confidence, elem2.current_element.confidence)

        elem1_size = max(elem1.current_element.bbox[2], elem1.current_element.bbox[3])
        elem2_size = max(elem2.current_element.bbox[2], elem2.current_element.bbox[3])
        near_threshold = (elem1_size + elem2_size) * 1.5

        if distance <= near_threshold:
            relationships.append(SpatialRelationship(
                element1_id="", element2_id="",
                relationship_type="near",
                distance=distance,
                angle=angle,
                confidence=confidence
            ))

        if abs(dx) > abs(dy):
            if dx > 0:
                rel_type = "right_of"
            else:
                rel_type = "left_of"
        else:
            if dy > 0:
                rel_type = "below"
            else:
                rel_type = "above"

        relationships.append(SpatialRelationship(
            element1_id="", element2_id="",
            relationship_type=rel_type,
            distance=distance,
            angle=angle,
            confidence=confidence * 0.8
        ))

        return relationships

    def get_stable_elements(self, min_stable_frames: int = 5) -> List[SpatialElement]:
        """Get elements that have been stable for minimum number of frames."""
        return [elem for elem in self.tracked_elements.values()
                if elem.stable_frames >= min_stable_frames and elem.current_element is not None]

    def find_elements_by_spatial_query(self, query: str) -> List[SpatialElement]:
        """Find elements using spatial query language."""
        try:
            parsed_query = self._parse_spatial_query(query)
            if not parsed_query:
                return []

            results = self._execute_spatial_query(parsed_query)
            return results

        except Exception as e:
            logger.error(f"Error executing spatial query '{query}': {e}")
            return []

    def export_spatial_memory(self) -> Dict[str, Any]:
        """Export spatial memory for persistence."""
        return {
            "tracked_elements": {
                eid: {
                    "element_id": elem.element_id,
                    "fingerprint": asdict(elem.fingerprint),
                    "last_seen": elem.last_seen,
                    "track_count": elem.track_count,
                    "stable_frames": elem.stable_frames,
                    "positions": elem.positions[-10:],
                    "confidence_history": elem.confidence_history[-10:]
                }
                for eid, elem in self.tracked_elements.items()
            },
            "spatial_relationships": [asdict(rel) for rel in self.spatial_relationships],
            "frame_count": self.frame_count
        }

    def import_spatial_memory(self, data: Dict[str, Any]):
        """Import spatial memory from persistence."""
        try:
            self.tracked_elements.clear()
            self.spatial_relationships.clear()

            if "tracked_elements" in data:
                for element_id, elem_data in data["tracked_elements"].items():
                    fingerprint = ElementFingerprint(**elem_data["fingerprint"])

                    spatial_elem = SpatialElement(
                        element_id=element_id,
                        current_element=None,
                        current_text=None,
                        fingerprint=fingerprint,
                        last_seen=elem_data["last_seen"],
                        track_count=elem_data["track_count"],
                        stable_frames=elem_data["stable_frames"],
                        positions=elem_data["positions"],
                        confidence_history=elem_data["confidence_history"]
                    )

                    self.tracked_elements[element_id] = spatial_elem

            if "spatial_relationships" in data:
                self.spatial_relationships = [
                    SpatialRelationship(**rel_data)
                    for rel_data in data["spatial_relationships"]
                ]

            if "frame_count" in data:
                self.frame_count = data["frame_count"]

            logger.info(f"Imported spatial memory: {len(self.tracked_elements)} elements, {len(self.spatial_relationships)} relationships")

        except Exception as e:
            logger.error(f"Error importing spatial memory: {e}")

    def _parse_spatial_query(self, query: str) -> Optional[Dict[str, Any]]:
        """Parse spatial query string into structured query."""
        try:
            query = query.strip().lower()

            if "near" in query:
                return self._parse_near_query(query)
            elif "left_of" in query:
                return self._parse_directional_query(query, "left_of")
            elif "right_of" in query:
                return self._parse_directional_query(query, "right_of")
            elif "above" in query:
                return self._parse_directional_query(query, "above")
            elif "below" in query:
                return self._parse_directional_query(query, "below")
            elif "within" in query:
                return self._parse_within_query(query)
            elif "stable" in query:
                return self._parse_stable_query(query)
            else:
                return {"type": "all"}

        except Exception as e:
            logger.error(f"Error parsing spatial query '{query}': {e}")
            return None

    def _parse_near_query(self, query: str) -> Dict[str, Any]:
        """Parse 'near' spatial query."""
        import re
        match = re.search(r"near\(['\"]([^'\"]+)['\"],?\s*(\d+)?\)", query)
        if match:
            text = match.group(1)
            distance = int(match.group(2)) if match.group(2) else 100
            return {"type": "near", "text": text, "distance": distance}
        return {"type": "near", "text": "", "distance": 100}

    def _parse_directional_query(self, query: str, direction: str) -> Dict[str, Any]:
        """Parse directional spatial query."""
        import re
        match = re.search(rf"{direction}\(['\"]([^'\"]+)['\"]\)", query)
        if match:
            text = match.group(1)
            return {"type": direction, "text": text}
        return {"type": direction, "text": ""}

    def _parse_within_query(self, query: str) -> Dict[str, Any]:
        """Parse 'within' spatial query."""
        import re
        match = re.search(r"within\(['\"]([^'\"]+)['\"]\)", query)
        if match:
            container = match.group(1)
            return {"type": "within", "container": container}
        return {"type": "within", "container": ""}

    def _parse_stable_query(self, query: str) -> Dict[str, Any]:
        """Parse 'stable' query."""
        import re
        match = re.search(r"stable\((\d+)?\)", query)
        if match:
            frames = int(match.group(1)) if match.group(1) else 5
            return {"type": "stable", "min_frames": frames}
        return {"type": "stable", "min_frames": 5}

    def _execute_spatial_query(self, parsed_query: Dict[str, Any]) -> List[SpatialElement]:
        """Execute parsed spatial query against tracked elements."""
        query_type = parsed_query["type"]

        if query_type == "all":
            return list(self.tracked_elements.values())

        elif query_type == "near":
            return self._execute_near_query(parsed_query)

        elif query_type in ["left_of", "right_of", "above", "below"]:
            return self._execute_directional_query(parsed_query)

        elif query_type == "within":
            return self._execute_within_query(parsed_query)

        elif query_type == "stable":
            return self._execute_stable_query(parsed_query)

        else:
            logger.warning(f"Unknown query type: {query_type}")
            return []

    def _execute_near_query(self, query: Dict[str, Any]) -> List[SpatialElement]:
        """Execute 'near' query."""
        target_text = query["text"]
        max_distance = query["distance"]
        results = []

        reference_elem = None
        for elem in self.tracked_elements.values():
            if elem.current_text and target_text.lower() in elem.current_text.text.lower():
                reference_elem = elem
                break

        if not reference_elem or not reference_elem.current_element:
            return results

        ref_center = reference_elem.current_element.center

        for elem in self.tracked_elements.values():
            if elem == reference_elem or not elem.current_element:
                continue

            elem_center = elem.current_element.center
            distance = np.sqrt((ref_center[0] - elem_center[0])**2 +
                               (ref_center[1] - elem_center[1])**2)

            if distance <= max_distance:
                results.append(elem)

        return results

    def _execute_directional_query(self, query: Dict[str, Any]) -> List[SpatialElement]:
        """Execute directional query (left_of, right_of, above, below)."""
        direction = query["type"]
        target_text = query["text"]
        results = []

        reference_elem = None
        for elem in self.tracked_elements.values():
            if elem.current_text and target_text.lower() in elem.current_text.text.lower():
                reference_elem = elem
                break

        if not reference_elem or not reference_elem.current_element:
            return results

        ref_center = reference_elem.current_element.center

        for elem in self.tracked_elements.values():
            if elem == reference_elem or not elem.current_element:
                continue

            elem_center = elem.current_element.center

            if direction == "left_of" and elem_center[0] < ref_center[0]:
                results.append(elem)
            elif direction == "right_of" and elem_center[0] > ref_center[0]:
                results.append(elem)
            elif direction == "above" and elem_center[1] < ref_center[1]:
                results.append(elem)
            elif direction == "below" and elem_center[1] > ref_center[1]:
                results.append(elem)

        return results

    def _execute_within_query(self, query: Dict[str, Any]) -> List[SpatialElement]:
        """Execute 'within' query."""
        container_text = query["container"]
        results = []

        container_elem = None
        for elem in self.tracked_elements.values():
            if elem.current_text and container_text.lower() in elem.current_text.text.lower():
                container_elem = elem
                break

        if not container_elem or not container_elem.current_element:
            return results

        container_bbox = container_elem.current_element.bbox

        for elem in self.tracked_elements.values():
            if elem == container_elem or not elem.current_element:
                continue

            elem_bbox = elem.current_element.bbox

            if (elem_bbox[0] >= container_bbox[0] and
                    elem_bbox[1] >= container_bbox[1] and
                    elem_bbox[0] + elem_bbox[2] <= container_bbox[0] + container_bbox[2] and
                    elem_bbox[1] + elem_bbox[3] <= container_bbox[1] + container_bbox[3]):
                results.append(elem)

        return results

    def _execute_stable_query(self, query: Dict[str, Any]) -> List[SpatialElement]:
        """Execute 'stable' query."""
        min_frames = query["min_frames"]
        return self.get_stable_elements(min_frames)


# Spatial query language functions
def near(element: SpatialElement, text: str, within: int = 100) -> bool:
    """Check if element is near text within distance."""
    if not element.current_text:
        return False

    return text.lower() in element.current_text.text.lower()


def left_of(element: SpatialElement, reference_text: str) -> bool:
    """Check if element is left of reference text."""
    if not element.current_element or not element.current_text:
        return False

    return reference_text.lower() in element.current_text.text.lower()


def right_of(element: SpatialElement, reference_text: str) -> bool:
    """Check if element is right of reference text."""
    if not element.current_element or not element.current_text:
        return False

    return reference_text.lower() in element.current_text.text.lower()


def above(element: SpatialElement, reference_text: str) -> bool:
    """Check if element is above reference text."""
    if not element.current_element or not element.current_text:
        return False

    return reference_text.lower() in element.current_text.text.lower()


def below(element: SpatialElement, reference_text: str) -> bool:
    """Check if element is below reference text."""
    if not element.current_element or not element.current_text:
        return False

    return reference_text.lower() in element.current_text.text.lower()


def within(element: SpatialElement, container: str) -> bool:
    """Check if element is within container."""
    if not element.current_element or not element.current_text:
        return False

    return container.lower() in element.current_text.text.lower()


# Global tracker instance
_element_tracker = None


def initialize_spatial_tracker(max_tracking_age: float = 30.0, similarity_threshold: float = 0.8):
    """Initialize global spatial tracker."""
    global _element_tracker
    _element_tracker = ElementTracker(max_tracking_age, similarity_threshold)


def update_spatial_frame(elements: List[UIElement], text_blocks: List[TextBlock],
                         image: np.ndarray, timestamp: Optional[float] = None) -> List[SpatialElement]:
    """Update spatial tracker with new frame."""
    if not _element_tracker:
        raise RuntimeError("Spatial tracker not initialized. Call initialize_spatial_tracker() first.")

    return _element_tracker.update_frame(elements, text_blocks, image, timestamp)


def get_stable_elements(min_stable_frames: int = 5) -> List[SpatialElement]:
    """Get stable elements from global tracker."""
    if not _element_tracker:
        return []

    return _element_tracker.get_stable_elements(min_stable_frames)


def find_elements_by_spatial_query(query: str) -> List[SpatialElement]:
    """Find elements using spatial query."""
    if not _element_tracker:
        return []

    return _element_tracker.find_elements_by_spatial_query(query)
