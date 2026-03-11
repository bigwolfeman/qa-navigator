"""
Normalized selector schema for cross-framework element selection.

Supports UIA, Win32, and CDP selectors in a unified model that allows
adapters to choose the most appropriate method for element identification.

Windows desktop automation layer for QA Navigator.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class Selector:
    """
    A normalized selector that can address UIA, Win32, and CDP elements consistently.

    Each adapter can use whichever channel is most appropriate for the target framework.
    Supports "best effort" matching with confidence scoring.
    """
    # UIA channel - for Windows UI Automation
    uia: Dict[str, Any] = field(default_factory=dict)      # name, automation_id, control_type, path[]

    # Win32 channel - for classic Windows controls
    win32: Dict[str, Any] = field(default_factory=dict)    # hwnd, class, title, child_index

    # CDP channel - for web-based UIs (Chrome DevTools Protocol)
    cdp: Dict[str, Any] = field(default_factory=dict)      # css, xpath, text, frame

    # Human-readable hint for debugging and logging
    hint: Optional[str] = None

    def is_empty(self) -> bool:
        """Check if the selector has any criteria defined."""
        return not (self.uia or self.win32 or self.cdp)

    def score_merge(self, other: 'Selector') -> 'Selector':
        """
        Merge with another selector, combining criteria from all channels.
        Returns a new Selector with combined criteria.
        """
        return Selector(
            uia={**self.uia, **other.uia},
            win32={**self.win32, **other.win32},
            cdp={**self.cdp, **other.cdp},
            hint=other.hint or self.hint
        )

    def get_available_channels(self) -> List[str]:
        """Get list of channels that have selection criteria defined."""
        channels = []
        if self.uia:
            channels.append("uia")
        if self.win32:
            channels.append("win32")
        if self.cdp:
            channels.append("cdp")
        return channels

    def __str__(self) -> str:
        parts = []
        if self.uia:
            parts.append(f"UIA({', '.join(f'{k}={v}' for k, v in self.uia.items())})")
        if self.win32:
            parts.append(f"Win32({', '.join(f'{k}={v}' for k, v in self.win32.items())})")
        if self.cdp:
            parts.append(f"CDP({', '.join(f'{k}={v}' for k, v in self.cdp.items())})")

        result = " | ".join(parts)
        if self.hint:
            result = f"{self.hint}: {result}"
        return result
