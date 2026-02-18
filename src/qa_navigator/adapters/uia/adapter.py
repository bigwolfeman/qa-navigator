"""
UI Automation (UIA) adapter for Windows applications.

Provides automation control for modern Windows applications using the
UI Automation framework. Supports WPF, WinUI, UWP, and other applications
that expose UIA providers.

Ported from WindowsHarness.
"""

import logging
from typing import List, Dict, Any, Optional
try:
    import uiautomation as uia
    import win32process
except ImportError:
    uia = None
    win32process = None

from qa_navigator.adapters.base import Adapter
from qa_navigator.ui_selectors.model import Selector

logger = logging.getLogger(__name__)


class UIAAdapter(Adapter):
    """
    UI Automation adapter for modern Windows applications.

    Uses the uiautomation library to interact with applications that
    provide UI Automation support (WPF, WinUI, UWP, etc.).
    """

    name = "uia"

    def __init__(self):
        """Initialize UIA adapter."""
        self._available = uia is not None and win32process is not None
        if not self._available:
            logger.warning("uiautomation library not available - UIA adapter disabled")

    def probe(self, pid: int) -> float:
        """
        Assess confidence in controlling the target process via UIA.

        Args:
            pid: Target process ID

        Returns:
            Confidence score from 0.0 to 1.0
        """
        if not self._available:
            return 0.0

        try:
            import win32gui
            windows = []

            def enum_windows_callback(hwnd, windows):
                try:
                    _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
                    if found_pid == pid:
                        windows.append(hwnd)
                except:
                    pass
                return True

            win32gui.EnumWindows(enum_windows_callback, windows)

            if windows:
                try:
                    root = uia.ControlFromHandle(windows[0])
                    if root and hasattr(root, 'Exists') and root.Exists():
                        try:
                            children = root.GetChildren()
                            if children:
                                return 0.9
                            else:
                                return 0.7
                        except:
                            return 0.5
                    else:
                        return 0.0
                except:
                    return 0.0
            else:
                return 0.0
        except Exception as e:
            logger.debug(f"UIA probe failed for PID {pid}: {e}")
            return 0.0

    def find(self, selector: Selector, timeout_ms: int = 1000) -> List[Dict[str, Any]]:
        """
        Find UI elements matching the UIA selector criteria.

        Args:
            selector: Normalized selector with UIA criteria
            timeout_ms: Maximum time to wait for elements

        Returns:
            List of element handles with metadata
        """
        if not self._available or not selector.uia:
            return []

        try:
            found_controls = []

            def search_recursive(control):
                try:
                    if self._matches_uia_condition(control, selector.uia):
                        found_controls.append(control)
                    for child in control.GetChildren():
                        search_recursive(child)
                except:
                    pass

            search_root = None
            if "hwnd" in selector.uia:
                try:
                    search_root = uia.ControlFromHandle(int(selector.uia["hwnd"]))
                except:
                    pass

            if not search_root:
                search_root = uia.GetRootControl()

            uia.SetGlobalSearchTimeout(timeout_ms / 1000.0)

            search_recursive(search_root)

            results = []
            for control in found_controls:
                try:
                    if control and control.IsValid():
                        result = {
                            "handle": control.NativeWindowHandle,
                            "name": control.Name or "",
                            "type": control.ControlTypeName,
                            "automation_id": control.AutomationId or "",
                            "class_name": control.ClassName or "",
                            "enabled": control.IsEnabled,
                            "visible": control.IsVisible,
                        }

                        try:
                            rect = control.BoundingRectangle
                            if rect:
                                result["bounds"] = {
                                    "x": rect.left,
                                    "y": rect.top,
                                    "width": rect.width(),
                                    "height": rect.height()
                                }
                        except:
                            pass

                        results.append(result)
                except Exception as e:
                    logger.debug(f"Error processing UIA control: {e}")

            return results

        except Exception as e:
            logger.error(f"UIA find failed: {e}")
            return []

    def act(self, handle: Any, op: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform an action on a UIA element.

        Args:
            handle: Element handle from find()
            op: Operation name
            args: Operation arguments

        Returns:
            Result dictionary
        """
        if not self._available:
            return {"ok": False, "error": "UIA not available"}

        try:
            if isinstance(handle, int):
                control = uia.ControlFromHandle(handle)
            else:
                control = handle

            if not control or not control.IsValid():
                return {"ok": False, "error": "Invalid control handle"}

            if op == "invoke":
                try:
                    invoke_pattern = control.GetInvokePattern()
                    if invoke_pattern:
                        invoke_pattern.Invoke()
                        return {"ok": True}
                    else:
                        control.Click()
                        return {"ok": True}
                except Exception as e:
                    return {"ok": False, "error": f"Invoke failed: {e}"}

            elif op == "set_value" or op == "set_text":
                value = args.get("value", "")
                try:
                    value_pattern = control.GetValuePattern()
                    if value_pattern and not value_pattern.IsReadOnly:
                        value_pattern.SetValue(str(value))
                        return {"ok": True}
                    else:
                        control.SetFocus()
                        control.SendKeys("{Ctrl}a")
                        control.SendKeys(str(value))
                        return {"ok": True}
                except Exception as e:
                    return {"ok": False, "error": f"Set value failed: {e}"}

            elif op == "click":
                try:
                    control.Click()
                    return {"ok": True}
                except Exception as e:
                    return {"ok": False, "error": f"Click failed: {e}"}

            elif op == "select":
                try:
                    selection_pattern = control.GetSelectionItemPattern()
                    if selection_pattern:
                        selection_pattern.Select()
                        return {"ok": True}
                    else:
                        return {"ok": False, "error": "Selection not supported"}
                except Exception as e:
                    return {"ok": False, "error": f"Select failed: {e}"}

            elif op == "toggle":
                try:
                    toggle_pattern = control.GetTogglePattern()
                    if toggle_pattern:
                        toggle_pattern.Toggle()
                        return {"ok": True}
                    else:
                        return {"ok": False, "error": "Toggle not supported"}
                except Exception as e:
                    return {"ok": False, "error": f"Toggle failed: {e}"}

            elif op == "expand":
                try:
                    expand_pattern = control.GetExpandCollapsePattern()
                    if expand_pattern:
                        expand_pattern.Expand()
                        return {"ok": True}
                    else:
                        return {"ok": False, "error": "Expand not supported"}
                except Exception as e:
                    return {"ok": False, "error": f"Expand failed: {e}"}

            elif op == "collapse":
                try:
                    expand_pattern = control.GetExpandCollapsePattern()
                    if expand_pattern:
                        expand_pattern.Collapse()
                        return {"ok": True}
                    else:
                        return {"ok": False, "error": "Collapse not supported"}
                except Exception as e:
                    return {"ok": False, "error": f"Collapse failed: {e}"}

            else:
                return {"ok": False, "error": f"Unknown operation: {op}"}

        except Exception as e:
            logger.error(f"UIA action failed: {e}")
            return {"ok": False, "error": str(e)}

    def get(self, handle: Any, prop: str) -> Any:
        """
        Get a property value from a UIA element.

        Args:
            handle: Element handle from find()
            prop: Property name

        Returns:
            Property value or None
        """
        if not self._available:
            return None

        try:
            if isinstance(handle, int):
                control = uia.ControlFromHandle(handle)
            else:
                control = handle

            if not control or not control.IsValid():
                return None

            if prop == "text" or prop == "name":
                return control.Name
            elif prop == "value":
                try:
                    value_pattern = control.GetValuePattern()
                    if value_pattern:
                        return value_pattern.Value
                except:
                    pass
                return control.Name
            elif prop == "enabled":
                return control.IsEnabled
            elif prop == "visible":
                return control.IsVisible
            elif prop == "bounds":
                try:
                    rect = control.BoundingRectangle
                    if rect:
                        return {
                            "x": rect.left,
                            "y": rect.top,
                            "width": rect.width(),
                            "height": rect.height()
                        }
                except:
                    pass
                return None
            elif prop == "class":
                return control.ClassName
            elif prop == "control_type":
                return control.ControlTypeName
            elif prop == "automation_id":
                return control.AutomationId
            else:
                return None

        except Exception as e:
            logger.debug(f"UIA get property failed: {e}")
            return None

    def _matches_uia_condition(self, control, criteria: Dict[str, Any]) -> bool:
        """Check if a UIA control matches the search criteria."""
        try:
            for key, value in criteria.items():
                if key == "automation_id":
                    if control.AutomationId != value:
                        return False
                elif key == "name":
                    if control.Name != value:
                        return False
                elif key == "control_type":
                    if control.ControlTypeName != f"{value}Control":
                        return False
                elif key == "class_name":
                    if control.ClassName != value:
                        return False
                elif key == "hwnd":
                    continue
            return True
        except:
            return False

    def get_capabilities(self) -> Dict[str, List[str]]:
        """Report supported operations and properties."""
        return {
            "operations": [
                "invoke", "set_value", "set_text", "click", "select",
                "toggle", "expand", "collapse"
            ],
            "properties": [
                "text", "name", "value", "enabled", "visible", "bounds",
                "class", "control_type", "automation_id"
            ]
        }
