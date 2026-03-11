"""
Windows enumeration and window management functionality.

This module provides comprehensive window enumeration, process mapping,
multi-monitor support, and coordinate normalization for the Harness system.

Windows desktop automation layer for QA Navigator.
"""

import ctypes
import ctypes.wintypes
from typing import List, Tuple, Optional
from dataclasses import dataclass
import logging
import psutil
import win32gui
import win32process
import win32api
from ctypes import windll, wintypes, Structure, byref, sizeof, c_int

logger = logging.getLogger(__name__)

# Windows API constants
WS_VISIBLE = 0x10000000
WS_MINIMIZE = 0x20000000
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
SW_HIDE = 0
SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9
SW_SHOW = 5
SW_SHOWDEFAULT = 10
SW_SHOWMAXIMIZED = 3
SW_SHOWMINIMIZED = 2
SW_SHOWMINNOACTIVE = 7
SW_SHOWNA = 8
SW_SHOWNOACTIVATE = 4
SW_SHOWNORMAL = 1

# DPI awareness constants
PROCESS_DPI_UNAWARE = 0
PROCESS_SYSTEM_DPI_AWARE = 1
PROCESS_PER_MONITOR_DPI_AWARE = 2

# Monitor constants
MONITOR_DEFAULTTONULL = 0
MONITOR_DEFAULTTOPRIMARY = 1
MONITOR_DEFAULTTONEAREST = 2


class RECT(Structure):
    """Windows RECT structure."""
    _fields_ = [("left", c_int),
                ("top", c_int),
                ("right", c_int),
                ("bottom", c_int)]


class POINT(Structure):
    """Windows POINT structure."""
    _fields_ = [("x", c_int),
                ("y", c_int)]


class MONITORINFO(Structure):
    """Windows MONITORINFO structure."""
    _fields_ = [("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD)]


@dataclass
class WindowInfo:
    """Information about a window."""
    hwnd: int
    title: str
    class_name: str
    pid: int
    process_name: str
    exe_path: str
    rect: Tuple[int, int, int, int]  # (left, top, right, bottom)
    is_visible: bool
    is_minimized: bool
    is_maximized: bool
    monitor_handle: int
    dpi_scale: float
    logical_rect: Tuple[int, int, int, int]  # DPI-normalized coordinates


@dataclass
class MonitorInfo:
    """Information about a monitor."""
    handle: int
    rect: Tuple[int, int, int, int]  # (left, top, right, bottom)
    work_rect: Tuple[int, int, int, int]  # Working area excluding taskbar
    is_primary: bool
    dpi_x: int
    dpi_y: int
    scale_factor: float
    device_name: str


def initialize_window_manager():
    """Initialize the window management system."""
    logger.info("Initializing window management system")

    try:
        windll.user32.SetProcessDpiAwarenessContext(2)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        logger.info("Set per-monitor DPI awareness")
    except Exception as e:
        logger.warning(f"Could not set DPI awareness: {e}")
        try:
            windll.user32.SetProcessDPIAware()
            logger.info("Set system DPI awareness")
        except Exception as e2:
            logger.warning(f"Could not set system DPI awareness: {e2}")

    logger.info("Window management system initialized successfully")


class WindowEnumerator:
    """Handles enumeration and management of Windows desktop windows."""

    def __init__(self):
        self._setup_dpi_awareness()
        self.monitors: List[MonitorInfo] = []
        self._refresh_monitors()

    def _setup_dpi_awareness(self) -> None:
        """Set up DPI awareness for proper coordinate handling."""
        try:
            windll.shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
        except (AttributeError, OSError):
            try:
                windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                logger.warning("Could not set DPI awareness")

    def _refresh_monitors(self) -> None:
        """Refresh the list of available monitors."""
        self.monitors.clear()

        def monitor_enum_proc(hmonitor, hdc, lprect, lparam):
            monitor_info = MONITORINFO()
            monitor_info.cbSize = sizeof(MONITORINFO)

            if windll.user32.GetMonitorInfoW(hmonitor, byref(monitor_info)):
                try:
                    dpi_x = wintypes.UINT()
                    dpi_y = wintypes.UINT()
                    windll.shcore.GetDpiForMonitor(
                        hmonitor, 0, byref(dpi_x), byref(dpi_y)
                    )
                    dpi_x_val, dpi_y_val = dpi_x.value, dpi_y.value
                except (AttributeError, OSError):
                    dc = windll.user32.GetDC(None)
                    dpi_x_val = windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
                    dpi_y_val = windll.gdi32.GetDeviceCaps(dc, 90)  # LOGPIXELSY
                    windll.user32.ReleaseDC(None, dc)

                scale_factor = dpi_x_val / 96.0  # 96 DPI is 100% scale

                device_name = ""
                try:
                    info = win32api.GetMonitorInfo(hmonitor)
                    device_name = info.get('Device', '')
                except Exception:
                    pass

                monitor = MonitorInfo(
                    handle=hmonitor,
                    rect=(monitor_info.rcMonitor.left, monitor_info.rcMonitor.top,
                          monitor_info.rcMonitor.right, monitor_info.rcMonitor.bottom),
                    work_rect=(monitor_info.rcWork.left, monitor_info.rcWork.top,
                               monitor_info.rcWork.right, monitor_info.rcWork.bottom),
                    is_primary=(monitor_info.dwFlags & 1) != 0,
                    dpi_x=dpi_x_val,
                    dpi_y=dpi_y_val,
                    scale_factor=scale_factor,
                    device_name=device_name
                )
                self.monitors.append(monitor)

            return True

        MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR,
                                             wintypes.HDC, ctypes.POINTER(RECT),
                                             wintypes.LPARAM)
        windll.user32.EnumDisplayMonitors(None, None,
                                          MONITORENUMPROC(monitor_enum_proc), 0)

    def get_monitor_for_window(self, hwnd: int) -> Optional[MonitorInfo]:
        """Get the monitor that contains the given window."""
        hmonitor = windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not hmonitor:
            return None

        for monitor in self.monitors:
            if monitor.handle == hmonitor:
                return monitor

        return None

    def _normalize_coordinates(self, rect: Tuple[int, int, int, int],
                               monitor: MonitorInfo) -> Tuple[int, int, int, int]:
        """Convert physical coordinates to logical coordinates."""
        if monitor.scale_factor == 1.0:
            return rect

        left, top, right, bottom = rect
        scale = monitor.scale_factor

        return (
            int(left / scale),
            int(top / scale),
            int(right / scale),
            int(bottom / scale)
        )

    def _is_window_cloaked(self, hwnd: int) -> bool:
        """Check if a window is cloaked (hidden by DWM)."""
        try:
            cloaked = wintypes.DWORD()
            result = windll.dwmapi.DwmGetWindowAttribute(
                hwnd, 14,  # DWMWA_CLOAKED
                byref(cloaked), sizeof(cloaked)
            )
            return result == 0 and cloaked.value != 0
        except (AttributeError, OSError):
            return False

    def _get_window_info(self, hwnd: int) -> Optional[WindowInfo]:
        """Get detailed information about a window."""
        if not win32gui.IsWindow(hwnd):
            return None

        try:
            title = win32gui.GetWindowText(hwnd)
            class_name = win32gui.GetClassName(hwnd)

            try:
                rect = win32gui.GetWindowRect(hwnd)
            except Exception:
                return None

            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                process = psutil.Process(pid)
                process_name = process.name()
                exe_path = process.exe()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                pid = 0
                process_name = ""
                exe_path = ""

            style = win32gui.GetWindowLong(hwnd, GWL_STYLE)
            is_visible = bool(style & WS_VISIBLE)
            is_minimized = bool(style & WS_MINIMIZE)

            placement = win32gui.GetWindowPlacement(hwnd)
            is_maximized = (placement[1] == SW_SHOWMAXIMIZED)

            if self._is_window_cloaked(hwnd) and not is_visible:
                return None

            monitor = self.get_monitor_for_window(hwnd)
            if not monitor:
                return None

            logical_rect = self._normalize_coordinates(rect, monitor)

            return WindowInfo(
                hwnd=hwnd,
                title=title,
                class_name=class_name,
                pid=pid,
                process_name=process_name,
                exe_path=exe_path,
                rect=rect,
                is_visible=is_visible,
                is_minimized=is_minimized,
                is_maximized=is_maximized,
                monitor_handle=monitor.handle,
                dpi_scale=monitor.scale_factor,
                logical_rect=logical_rect
            )

        except Exception as e:
            logger.debug(f"Error getting window info for {hwnd}: {e}")
            return None

    def _should_include_window(self, window_info: WindowInfo) -> bool:
        """Determine if a window should be included in enumeration results."""
        if not window_info.is_visible:
            return False

        if not window_info.title.strip() and not window_info.process_name:
            return False

        rect = window_info.logical_rect
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        if width < 50 or height < 50:
            return False

        try:
            ex_style = win32gui.GetWindowLong(window_info.hwnd, GWL_EXSTYLE)
            if (ex_style & WS_EX_TOOLWINDOW) and len(window_info.title.strip()) < 3:
                return False
            if ex_style & WS_EX_NOACTIVATE:
                return False
        except Exception:
            pass

        return True

    def enumerate_windows(self, include_minimized: bool = True) -> List[WindowInfo]:
        """
        Enumerate all relevant desktop windows.

        Args:
            include_minimized: Whether to include minimized windows

        Returns:
            List of WindowInfo objects for relevant windows
        """
        self._refresh_monitors()
        windows = []

        def enum_window_proc(hwnd, lparam):
            window_info = self._get_window_info(hwnd)
            if window_info and self._should_include_window(window_info):
                if include_minimized or not window_info.is_minimized:
                    windows.append(window_info)
            return True

        win32gui.EnumWindows(enum_window_proc, None)

        windows.sort(key=lambda w: w.title.lower())

        logger.info(f"Enumerated {len(windows)} windows")
        return windows

    def get_window_by_hwnd(self, hwnd: int) -> Optional[WindowInfo]:
        """Get window information by handle."""
        return self._get_window_info(hwnd)

    def find_windows_by_title(self, title_pattern: str,
                              exact_match: bool = False) -> List[WindowInfo]:
        """
        Find windows by title pattern.

        Args:
            title_pattern: Title to search for
            exact_match: If True, requires exact match; otherwise partial match

        Returns:
            List of matching WindowInfo objects
        """
        windows = self.enumerate_windows()
        matches = []

        pattern = title_pattern.lower()

        for window in windows:
            title = window.title.lower()
            if exact_match:
                if title == pattern:
                    matches.append(window)
            else:
                if pattern in title:
                    matches.append(window)

        return matches

    def find_windows_by_process(self, process_name: str) -> List[WindowInfo]:
        """
        Find windows by process name.

        Args:
            process_name: Process name to search for (e.g., 'notepad.exe')

        Returns:
            List of matching WindowInfo objects
        """
        windows = self.enumerate_windows()
        matches = []

        process_pattern = process_name.lower()

        for window in windows:
            if process_pattern in window.process_name.lower():
                matches.append(window)

        return matches

    def get_foreground_window(self) -> Optional[WindowInfo]:
        """Get information about the currently focused window."""
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            return self._get_window_info(hwnd)
        return None

    def get_monitors(self) -> List[MonitorInfo]:
        """Get information about all monitors."""
        self._refresh_monitors()
        return self.monitors.copy()

    def get_primary_monitor(self) -> Optional[MonitorInfo]:
        """Get the primary monitor."""
        for monitor in self.monitors:
            if monitor.is_primary:
                return monitor
        return None


def create_window_enumerator() -> WindowEnumerator:
    """Factory function to create a WindowEnumerator instance."""
    return WindowEnumerator()


_enumerator = None


def get_enumerator() -> WindowEnumerator:
    """Get a shared WindowEnumerator instance."""
    global _enumerator
    if _enumerator is None:
        _enumerator = WindowEnumerator()
    return _enumerator


def enumerate_windows(include_minimized: bool = True) -> List[WindowInfo]:
    """Convenience function to enumerate windows."""
    return get_enumerator().enumerate_windows(include_minimized)


def find_window_by_title(title: str, exact_match: bool = False) -> Optional[WindowInfo]:
    """Convenience function to find a single window by title."""
    matches = get_enumerator().find_windows_by_title(title, exact_match)
    return matches[0] if matches else None


def find_window_by_process(process_name: str) -> Optional[WindowInfo]:
    """Convenience function to find a single window by process name."""
    matches = get_enumerator().find_windows_by_process(process_name)
    return matches[0] if matches else None
