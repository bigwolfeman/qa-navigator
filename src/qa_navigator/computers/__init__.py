"""BaseComputer implementations for QA Navigator.

Two implementations:
- QAPlaywrightComputer: Browser-only testing (works on Linux/Windows/Mac)
- WindowsComputer: Full desktop testing (Windows only, uses Win32 APIs)
"""

import sys

from .playwright_computer import QAPlaywrightComputer

__all__ = ["QAPlaywrightComputer"]

# WindowsComputer is only available on Windows
if sys.platform == "win32":
    from .windows_computer import WindowsComputer
    __all__.append("WindowsComputer")
