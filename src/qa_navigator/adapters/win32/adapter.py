"""
Win32 adapter for classic Windows controls.

Provides automation control for traditional Windows applications using
Win32 APIs and control messages. Does not hijack user input - uses
direct control messages instead.

Windows desktop automation layer for QA Navigator.
"""

import logging
import ctypes
import ctypes.wintypes as wt
from typing import List, Dict, Any, Optional
try:
    import win32gui
    import win32con
    import win32api
    import win32process
except ImportError:
    win32gui = win32con = win32api = win32process = None

from qa_navigator.adapters.base import Adapter
from qa_navigator.ui_selectors.model import Selector

logger = logging.getLogger(__name__)


class Win32Adapter(Adapter):
    """
    Win32 adapter for classic Windows controls.

    Uses Win32 APIs and window messages to control traditional Windows
    applications without hijacking global input.
    """

    name = "win32"

    def __init__(self):
        """Initialize Win32 adapter."""
        self._available = win32gui is not None and win32process is not None
        if not self._available:
            logger.warning("pywin32 not available - Win32 adapter disabled")

    def probe(self, pid: int) -> float:
        """
        Assess confidence in controlling the target process via Win32.

        Args:
            pid: Target process ID

        Returns:
            Confidence score from 0.0 to 1.0
        """
        if not self._available:
            return 0.0

        try:
            windows = self._find_process_windows(pid)

            if windows:
                control_classes = {'Edit', 'Button', 'Static', 'ComboBox', 'ListBox', 'SysTreeView32', 'SysListView32'}

                controls_found = 0
                for hwnd in windows:
                    try:
                        class_name = win32gui.GetClassName(hwnd)
                        if class_name in control_classes:
                            controls_found += 1
                    except:
                        pass

                if controls_found > 0:
                    return 0.7
                else:
                    return 0.4
            else:
                return 0.1

        except Exception as e:
            logger.debug(f"Win32 probe failed for PID {pid}: {e}")
            return 0.2

    def find(self, selector: Selector, timeout_ms: int = 1000) -> List[Dict[str, Any]]:
        """
        Find Windows controls matching the Win32 selector criteria.

        Args:
            selector: Normalized selector with Win32 criteria
            timeout_ms: Maximum time to wait for elements (not used in Win32)

        Returns:
            List of window handles with metadata
        """
        if not self._available or not selector.win32:
            return []

        try:
            results = []

            if "hwnd" in selector.win32:
                hwnd = int(selector.win32["hwnd"])
                if win32gui.IsWindow(hwnd):
                    info = self._get_window_info(hwnd)
                    if self._matches_criteria(info, selector.win32):
                        results.append(info)
                return results

            search_windows = []

            if "title" in selector.win32:
                title = selector.win32["title"]

                def enum_windows_proc(hwnd, param):
                    try:
                        window_title = win32gui.GetWindowText(hwnd)
                        if title in window_title or window_title == title:
                            param.append(hwnd)
                    except:
                        pass
                    return True

                win32gui.EnumWindows(enum_windows_proc, search_windows)
            else:
                def enum_all_proc(hwnd, param):
                    param.append(hwnd)
                    return True

                win32gui.EnumWindows(enum_all_proc, search_windows)

            for hwnd in search_windows:
                info = self._get_window_info(hwnd)
                if self._matches_criteria(info, selector.win32):
                    results.append(info)

                child_windows = []

                def enum_child_proc(child_hwnd, param):
                    param.append(child_hwnd)
                    return True

                try:
                    win32gui.EnumChildWindows(hwnd, enum_child_proc, child_windows)

                    for child_hwnd in child_windows:
                        child_info = self._get_window_info(child_hwnd)
                        if self._matches_criteria(child_info, selector.win32):
                            results.append(child_info)
                except:
                    pass

            return results

        except Exception as e:
            logger.error(f"Win32 find failed: {e}")
            return []

    def act(self, handle: Any, op: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform an action on a Win32 control.

        Args:
            handle: Window handle (HWND)
            op: Operation name
            args: Operation arguments

        Returns:
            Result dictionary
        """
        if not self._available:
            return {"ok": False, "error": "Win32 not available"}

        try:
            hwnd = int(handle)

            if not win32gui.IsWindow(hwnd):
                return {"ok": False, "error": "Invalid window handle"}

            if op == "click":
                try:
                    result = win32gui.SendMessage(hwnd, win32con.BM_CLICK, 0, 0)
                    return {"ok": True, "result": result}
                except Exception as e:
                    return {"ok": False, "error": f"Click failed: {e}"}

            elif op == "set_text":
                text = args.get("value", "")
                try:
                    result = win32gui.SendMessage(hwnd, win32con.WM_SETTEXT, 0, text)
                    return {"ok": True, "result": result}
                except Exception as e:
                    return {"ok": False, "error": f"Set text failed: {e}"}

            elif op == "get_text":
                try:
                    text = win32gui.GetWindowText(hwnd)
                    return {"ok": True, "text": text}
                except Exception as e:
                    return {"ok": False, "error": f"Get text failed: {e}"}

            elif op == "select":
                index = args.get("index", 0)
                text = args.get("text", "")

                try:
                    class_name = win32gui.GetClassName(hwnd)

                    if "ComboBox" in class_name:
                        if text:
                            result = win32gui.SendMessage(hwnd, win32con.CB_SELECTSTRING, -1, text)
                        else:
                            result = win32gui.SendMessage(hwnd, win32con.CB_SETCURSEL, index, 0)
                        return {"ok": True, "result": result}

                    elif "ListBox" in class_name:
                        if text:
                            result = win32gui.SendMessage(hwnd, win32con.LB_SELECTSTRING, -1, text)
                        else:
                            result = win32gui.SendMessage(hwnd, win32con.LB_SETCURSEL, index, 0)
                        return {"ok": True, "result": result}

                    else:
                        return {"ok": False, "error": "Selection not supported for this control type"}

                except Exception as e:
                    return {"ok": False, "error": f"Select failed: {e}"}

            elif op == "check":
                try:
                    result = win32gui.SendMessage(hwnd, win32con.BM_SETCHECK, win32con.BST_CHECKED, 0)
                    return {"ok": True, "result": result}
                except Exception as e:
                    return {"ok": False, "error": f"Check failed: {e}"}

            elif op == "uncheck":
                try:
                    result = win32gui.SendMessage(hwnd, win32con.BM_SETCHECK, win32con.BST_UNCHECKED, 0)
                    return {"ok": True, "result": result}
                except Exception as e:
                    return {"ok": False, "error": f"Uncheck failed: {e}"}

            else:
                return {"ok": False, "error": f"Unknown operation: {op}"}

        except Exception as e:
            logger.error(f"Win32 action failed: {e}")
            return {"ok": False, "error": str(e)}

    def get(self, handle: Any, prop: str) -> Any:
        """
        Get a property value from a Win32 control.

        Args:
            handle: Window handle (HWND)
            prop: Property name

        Returns:
            Property value or None
        """
        if not self._available:
            return None

        try:
            hwnd = int(handle)

            if not win32gui.IsWindow(hwnd):
                return None

            if prop == "text" or prop == "title":
                return win32gui.GetWindowText(hwnd)
            elif prop == "class":
                return win32gui.GetClassName(hwnd)
            elif prop == "enabled":
                return win32gui.IsWindowEnabled(hwnd)
            elif prop == "visible":
                return win32gui.IsWindowVisible(hwnd)
            elif prop == "bounds":
                try:
                    rect = win32gui.GetWindowRect(hwnd)
                    return {
                        "x": rect[0],
                        "y": rect[1],
                        "width": rect[2] - rect[0],
                        "height": rect[3] - rect[1]
                    }
                except:
                    return None
            elif prop == "pid":
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    return pid
                except:
                    return None
            else:
                return None

        except Exception as e:
            logger.debug(f"Win32 get property failed: {e}")
            return None

    def get_capabilities(self) -> Dict[str, List[str]]:
        """Report supported operations and properties."""
        return {
            "operations": [
                "click", "set_text", "get_text", "select", "check", "uncheck"
            ],
            "properties": [
                "text", "title", "class", "enabled", "visible", "bounds", "pid"
            ]
        }

    def _find_process_windows(self, pid: int) -> List[int]:
        """Find all windows belonging to a process."""
        windows = []

        def enum_proc(hwnd, param):
            try:
                _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
                if window_pid == pid:
                    param.append(hwnd)
            except:
                pass
            return True

        try:
            win32gui.EnumWindows(enum_proc, windows)
        except:
            pass

        return windows

    def _get_window_info(self, hwnd: int) -> Dict[str, Any]:
        """Get information about a window."""
        try:
            return {
                "handle": hwnd,
                "title": win32gui.GetWindowText(hwnd),
                "class": win32gui.GetClassName(hwnd),
                "enabled": win32gui.IsWindowEnabled(hwnd),
                "visible": win32gui.IsWindowVisible(hwnd)
            }
        except:
            return {"handle": hwnd, "title": "", "class": "", "enabled": False, "visible": False}

    def _matches_criteria(self, window_info: Dict[str, Any], criteria: Dict[str, Any]) -> bool:
        """Check if a window matches the search criteria."""
        for key, value in criteria.items():
            if key == "hwnd":
                continue
            elif key == "title":
                if value not in window_info.get("title", ""):
                    return False
            elif key == "class":
                if value != window_info.get("class", ""):
                    return False
            elif key == "enabled":
                if value != window_info.get("enabled", False):
                    return False
            elif key == "visible":
                if value != window_info.get("visible", False):
                    return False

        return True
