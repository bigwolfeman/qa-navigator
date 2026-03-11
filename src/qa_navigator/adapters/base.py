"""
Base adapter contract for UI automation frameworks.

Defines the minimal interface that all adapters must implement to provide
consistent automation capabilities across different UI technologies.

Windows desktop automation layer for QA Navigator.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from qa_navigator.ui_selectors.model import Selector


class Adapter(ABC):
    """
    Base adapter interface for UI automation.

    All adapters must implement these minimal verbs to provide consistent
    automation capabilities with confidence scoring and capability reporting.
    """

    name: str = "base"

    @abstractmethod
    def probe(self, pid: int) -> float:
        """
        Assess confidence in controlling the target process.

        Args:
            pid: Target process ID

        Returns:
            Confidence score from 0.0 to 1.0
            - 0.0: Cannot control this process
            - 1.0: Highest confidence for this framework
        """
        pass

    @abstractmethod
    def find(self, selector: Selector, timeout_ms: int = 1000) -> List[Dict[str, Any]]:
        """
        Find UI elements matching the selector.

        Args:
            selector: Normalized selector with criteria for multiple frameworks
            timeout_ms: Maximum time to wait for elements

        Returns:
            List of element handles/references with metadata:
            [{"handle": ..., "name": ..., "type": ..., "bounds": {...}}, ...]
        """
        pass

    @abstractmethod
    def act(self, handle: Any, op: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform an action on a UI element.

        Args:
            handle: Element handle/reference from find()
            op: Operation name ("click", "set_text", "invoke", "select", etc.)
            args: Operation-specific arguments

        Returns:
            Result dictionary with at least {"ok": bool, "error": str?}
        """
        pass

    @abstractmethod
    def get(self, handle: Any, prop: str) -> Any:
        """
        Get a property value from a UI element.

        Args:
            handle: Element handle/reference from find()
            prop: Property name ("text", "enabled", "bounds", "class", etc.)

        Returns:
            Property value or None if not available
        """
        pass

    def get_capabilities(self) -> Dict[str, List[str]]:
        """
        Report supported operations and properties.

        Returns:
            Dictionary with "operations" and "properties" keys listing
            what this adapter can do
        """
        return {
            "operations": ["click", "set_text"],
            "properties": ["text", "enabled"]
        }

    def cleanup(self):
        """
        Clean up adapter resources.
        Called when the adapter is no longer needed.
        """
        pass
