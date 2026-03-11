"""
Adapter registry for automatic framework selection.

Maintains a registry of available adapters and selects the most
appropriate one based on process framework detection and adapter
confidence scoring.

Windows desktop automation layer for QA Navigator.
"""

from typing import List, Optional, Dict, Any
from qa_navigator.adapters.base import Adapter
from .detect import detect_frameworks
import logging

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """
    Registry for UI automation adapters.

    Manages available adapters and provides automatic selection based on
    framework detection and adapter confidence scores.
    """

    def __init__(self):
        """Initialize empty registry."""
        self._adapters: List[Adapter] = []
        self._cached_selections: Dict[int, Adapter] = {}

    def register(self, adapter: Adapter):
        """
        Register a new adapter.

        Args:
            adapter: Adapter instance to register
        """
        if adapter not in self._adapters:
            self._adapters.append(adapter)
            logger.debug(f"Registered adapter: {adapter.name}")

    def unregister(self, adapter: Adapter):
        """
        Unregister an adapter.

        Args:
            adapter: Adapter instance to remove
        """
        if adapter in self._adapters:
            self._adapters.remove(adapter)
            self._cached_selections = {
                pid: cached_adapter for pid, cached_adapter in self._cached_selections.items()
                if cached_adapter != adapter
            }
            logger.debug(f"Unregistered adapter: {adapter.name}")

    def pick(self, pid: int, force_refresh: bool = False) -> Optional[Adapter]:
        """
        Select the best adapter for a target process.

        Uses framework detection scores combined with adapter probe results
        to select the most confident adapter.

        Args:
            pid: Target process ID
            force_refresh: If True, ignore cached selection

        Returns:
            Best adapter for the process, or None if no suitable adapter
        """
        if not force_refresh and pid in self._cached_selections:
            cached = self._cached_selections[pid]
            if cached in self._adapters:
                return cached
            else:
                del self._cached_selections[pid]

        if not self._adapters:
            logger.warning("No adapters registered")
            return None

        framework_scores = detect_frameworks(pid)
        logger.debug(f"Framework scores for PID {pid}: {framework_scores}")

        adapter_scores = []
        for adapter in self._adapters:
            try:
                probe_confidence = adapter.probe(pid)

                framework_confidence = framework_scores.get(adapter.name, 0.0)

                # Combined score (weighted average)
                # Probe confidence is more important as it's process-specific
                combined_score = (probe_confidence * 0.7) + (framework_confidence * 0.3)

                adapter_scores.append((adapter, combined_score, probe_confidence, framework_confidence))

                logger.debug(f"Adapter {adapter.name}: probe={probe_confidence:.2f}, "
                             f"framework={framework_confidence:.2f}, combined={combined_score:.2f}")

            except Exception as e:
                logger.warning(f"Error probing adapter {adapter.name}: {e}")
                adapter_scores.append((adapter, 0.0, 0.0, 0.0))

        if not adapter_scores:
            return None

        adapter_scores.sort(key=lambda x: x[1], reverse=True)

        best_adapter, best_score, probe_conf, framework_conf = adapter_scores[0]

        if best_score < 0.1:
            logger.warning(f"No adapter scored above threshold for PID {pid}")
            return None

        self._cached_selections[pid] = best_adapter

        logger.info(f"Selected adapter {best_adapter.name} for PID {pid} "
                    f"(score={best_score:.2f}, probe={probe_conf:.2f}, framework={framework_conf:.2f})")

        return best_adapter

    def get_adapters(self) -> List[Adapter]:
        """
        Get list of all registered adapters.

        Returns:
            Copy of adapter list
        """
        return self._adapters.copy()

    def get_adapter_by_name(self, name: str) -> Optional[Adapter]:
        """
        Get adapter by name.

        Args:
            name: Adapter name

        Returns:
            Adapter instance or None if not found
        """
        for adapter in self._adapters:
            if adapter.name == name:
                return adapter
        return None

    def clear_cache(self, pid: int = None):
        """
        Clear cached adapter selections.

        Args:
            pid: If specified, clear cache for this PID only.
                 If None, clear all cached selections.
        """
        if pid is not None:
            self._cached_selections.pop(pid, None)
        else:
            self._cached_selections.clear()
        logger.debug(f"Cleared adapter cache for PID {pid if pid else 'all'}")

    def get_selection_info(self, pid: int) -> Dict[str, Any]:
        """
        Get detailed information about adapter selection for a process.

        Args:
            pid: Target process ID

        Returns:
            Dictionary with selection details and scores
        """
        framework_scores = detect_frameworks(pid)

        adapter_info = []
        selected_adapter = None

        for adapter in self._adapters:
            try:
                probe_confidence = adapter.probe(pid)
                framework_confidence = framework_scores.get(adapter.name, 0.0)
                combined_score = (probe_confidence * 0.7) + (framework_confidence * 0.3)

                info = {
                    "name": adapter.name,
                    "probe_confidence": probe_confidence,
                    "framework_confidence": framework_confidence,
                    "combined_score": combined_score,
                    "capabilities": adapter.get_capabilities()
                }
                adapter_info.append(info)

                if not selected_adapter or combined_score > selected_adapter["combined_score"]:
                    if combined_score >= 0.1:
                        selected_adapter = info

            except Exception as e:
                adapter_info.append({
                    "name": adapter.name,
                    "error": str(e),
                    "probe_confidence": 0.0,
                    "framework_confidence": framework_scores.get(adapter.name, 0.0),
                    "combined_score": 0.0
                })

        return {
            "pid": pid,
            "framework_scores": framework_scores,
            "adapters": adapter_info,
            "selected": selected_adapter["name"] if selected_adapter else None,
            "cached": pid in self._cached_selections
        }


# Global registry instance
_registry = AdapterRegistry()


def pick(pid: int, force_refresh: bool = False) -> Optional[Adapter]:
    """
    Convenience function to pick the best adapter for a process.

    Args:
        pid: Target process ID
        force_refresh: If True, ignore cached selection

    Returns:
        Best adapter for the process
    """
    return _registry.pick(pid, force_refresh)


def register_adapter(adapter: Adapter):
    """
    Register an adapter with the global registry.

    Args:
        adapter: Adapter to register
    """
    _registry.register(adapter)


def get_registry() -> AdapterRegistry:
    """
    Get the global adapter registry.

    Returns:
        Global AdapterRegistry instance
    """
    return _registry
