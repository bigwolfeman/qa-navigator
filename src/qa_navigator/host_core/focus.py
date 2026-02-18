"""
Focus control and window activation functionality.

This module provides robust window activation with multiple fallback strategies
to achieve a 95% success rate for bringing windows to the foreground.

Ported from WindowsHarness.
"""

import time
import logging
from typing import Optional, Tuple, List
from enum import Enum
import win32gui
import win32process
import win32con
from ctypes import windll

from .windows import WindowInfo, get_enumerator

logger = logging.getLogger(__name__)

# Windows API constants
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

# SetWindowPos flags
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOREDRAW = 0x0008
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080
SWP_NOCOPYBITS = 0x0100
SWP_NOREPOSITION = 0x0200

# Special HWND values for SetWindowPos
HWND_TOP = 0
HWND_BOTTOM = 1
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

# Alt-tab constants
VK_MENU = 0x12  # Alt key
VK_TAB = 0x09   # Tab key
KEYEVENTF_KEYUP = 0x0002


class ActivationStrategy(Enum):
    """Different strategies for window activation."""
    BASIC = "basic"
    SWITCH_TO_THIS_WINDOW = "switch_to_this_window"
    ALLOW_SET_FOREGROUND = "allow_set_foreground"
    MINIMIZE_RESTORE = "minimize_restore"
    ALT_TAB_PULSE = "alt_tab_pulse"
    FORCE_FOREGROUND = "force_foreground"


class ActivationResult(Enum):
    """Results of window activation attempts."""
    SUCCESS = "success"
    FAILED = "failed"
    ALREADY_ACTIVE = "already_active"
    WINDOW_NOT_FOUND = "window_not_found"
    ACCESS_DENIED = "access_denied"


class FocusController:
    """Handles window focus and activation with multiple strategies."""

    def __init__(self):
        self.retry_count = 3
        self.retry_delay = 0.1
        self.verification_delay = 0.05

    def _is_window_foreground(self, hwnd: int) -> bool:
        """Check if the window is currently in the foreground."""
        try:
            current_hwnd = win32gui.GetForegroundWindow()
            return current_hwnd == hwnd
        except Exception:
            return False

    def _get_window_state(self, hwnd: int) -> Tuple[bool, bool, bool]:
        """Get window state: (is_visible, is_minimized, is_maximized)."""
        try:
            if not win32gui.IsWindow(hwnd):
                return False, False, False

            placement = win32gui.GetWindowPlacement(hwnd)
            is_minimized = (placement[1] == SW_SHOWMINIMIZED)
            is_maximized = (placement[1] == SW_SHOWMAXIMIZED)
            is_visible = win32gui.IsWindowVisible(hwnd)

            return is_visible, is_minimized, is_maximized
        except Exception:
            return False, False, False

    def _basic_activation(self, hwnd: int) -> bool:
        """Basic window activation using SetForegroundWindow."""
        try:
            is_visible, is_minimized, is_maximized = self._get_window_state(hwnd)

            if is_minimized:
                win32gui.ShowWindow(hwnd, SW_RESTORE)
                time.sleep(self.verification_delay)
            elif not is_visible:
                win32gui.ShowWindow(hwnd, SW_SHOW)
                time.sleep(self.verification_delay)

            result = win32gui.SetForegroundWindow(hwnd)
            time.sleep(self.verification_delay)

            return result and self._is_window_foreground(hwnd)
        except Exception as e:
            logger.debug(f"Basic activation failed: {e}")
            return False

    def _switch_to_this_window_activation(self, hwnd: int) -> bool:
        """Use SwitchToThisWindow for activation."""
        try:
            windll.user32.SwitchToThisWindow(hwnd, True)
            time.sleep(self.verification_delay * 2)

            return self._is_window_foreground(hwnd)
        except Exception as e:
            logger.debug(f"SwitchToThisWindow activation failed: {e}")
            return False

    def _allow_set_foreground_activation(self, hwnd: int) -> bool:
        """Use AllowSetForegroundWindow for same-user scenarios."""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            windll.user32.AllowSetForegroundWindow(pid)
            time.sleep(self.verification_delay)

            return self._basic_activation(hwnd)
        except Exception as e:
            logger.debug(f"AllowSetForegroundWindow activation failed: {e}")
            return False

    def _minimize_restore_activation(self, hwnd: int) -> bool:
        """Minimize then restore window to force activation."""
        try:
            is_visible, is_minimized, is_maximized = self._get_window_state(hwnd)

            if not is_minimized:
                win32gui.ShowWindow(hwnd, SW_MINIMIZE)
                time.sleep(self.verification_delay)

            if is_maximized:
                win32gui.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
            else:
                win32gui.ShowWindow(hwnd, SW_RESTORE)

            time.sleep(self.verification_delay * 2)

            win32gui.SetForegroundWindow(hwnd)
            time.sleep(self.verification_delay)

            return self._is_window_foreground(hwnd)
        except Exception as e:
            logger.debug(f"Minimize-restore activation failed: {e}")
            return False

    def _alt_tab_pulse_activation(self, hwnd: int) -> bool:
        """Use Alt+Tab simulation to activate window."""
        try:
            is_visible, is_minimized, is_maximized = self._get_window_state(hwnd)

            if is_minimized:
                win32gui.ShowWindow(hwnd, SW_RESTORE)
                time.sleep(self.verification_delay)

            windll.user32.keybd_event(VK_MENU, 0, 0, 0)           # Alt down
            time.sleep(0.01)
            windll.user32.keybd_event(VK_TAB, 0, 0, 0)            # Tab down
            time.sleep(0.01)
            windll.user32.keybd_event(VK_TAB, 0, KEYEVENTF_KEYUP, 0)   # Tab up
            time.sleep(0.01)
            windll.user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)  # Alt up

            time.sleep(self.verification_delay * 3)

            win32gui.SetForegroundWindow(hwnd)
            time.sleep(self.verification_delay)

            return self._is_window_foreground(hwnd)
        except Exception as e:
            logger.debug(f"Alt+Tab pulse activation failed: {e}")
            return False

    def _force_foreground_activation(self, hwnd: int) -> bool:
        """Force foreground using various low-level techniques."""
        try:
            current_foreground = win32gui.GetForegroundWindow()

            if current_foreground == hwnd:
                return True

            current_thread = win32process.GetWindowThreadProcessId(current_foreground)[0] if current_foreground else 0
            target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]

            if current_thread and current_thread != target_thread:
                windll.user32.AttachThreadInput(current_thread, target_thread, True)

            win32gui.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0,
                                  SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            time.sleep(self.verification_delay)

            win32gui.SetForegroundWindow(hwnd)
            time.sleep(self.verification_delay)

            win32gui.BringWindowToTop(hwnd)
            time.sleep(self.verification_delay)

            if current_thread and current_thread != target_thread:
                windll.user32.AttachThreadInput(current_thread, target_thread, False)

            return self._is_window_foreground(hwnd)
        except Exception as e:
            logger.debug(f"Force foreground activation failed: {e}")
            return False

    def activate_window(self, hwnd: int,
                        strategies: Optional[List[ActivationStrategy]] = None) -> ActivationResult:
        """
        Activate a window using multiple strategies.

        Args:
            hwnd: Window handle to activate
            strategies: List of strategies to try (uses default order if None)

        Returns:
            ActivationResult indicating the outcome
        """
        if not win32gui.IsWindow(hwnd):
            return ActivationResult.WINDOW_NOT_FOUND

        if self._is_window_foreground(hwnd):
            return ActivationResult.ALREADY_ACTIVE

        if strategies is None:
            strategies = [
                ActivationStrategy.BASIC,
                ActivationStrategy.SWITCH_TO_THIS_WINDOW,
                ActivationStrategy.ALLOW_SET_FOREGROUND,
                ActivationStrategy.MINIMIZE_RESTORE,
                ActivationStrategy.FORCE_FOREGROUND,
                ActivationStrategy.ALT_TAB_PULSE,
            ]

        for strategy in strategies:
            logger.debug(f"Trying activation strategy: {strategy.value}")

            for attempt in range(self.retry_count):
                try:
                    success = False

                    if strategy == ActivationStrategy.BASIC:
                        success = self._basic_activation(hwnd)
                    elif strategy == ActivationStrategy.SWITCH_TO_THIS_WINDOW:
                        success = self._switch_to_this_window_activation(hwnd)
                    elif strategy == ActivationStrategy.ALLOW_SET_FOREGROUND:
                        success = self._allow_set_foreground_activation(hwnd)
                    elif strategy == ActivationStrategy.MINIMIZE_RESTORE:
                        success = self._minimize_restore_activation(hwnd)
                    elif strategy == ActivationStrategy.ALT_TAB_PULSE:
                        success = self._alt_tab_pulse_activation(hwnd)
                    elif strategy == ActivationStrategy.FORCE_FOREGROUND:
                        success = self._force_foreground_activation(hwnd)

                    if success:
                        logger.info(f"Window activated successfully using {strategy.value} "
                                    f"(attempt {attempt + 1})")
                        return ActivationResult.SUCCESS

                    if attempt < self.retry_count - 1:
                        time.sleep(self.retry_delay)

                except Exception as e:
                    logger.debug(f"Strategy {strategy.value} attempt {attempt + 1} failed: {e}")
                    if attempt < self.retry_count - 1:
                        time.sleep(self.retry_delay)

        logger.warning(f"Failed to activate window {hwnd} with all strategies")
        return ActivationResult.FAILED

    def activate_window_info(self, window_info: WindowInfo) -> ActivationResult:
        """Activate a window using WindowInfo object."""
        return self.activate_window(window_info.hwnd)

    def minimize_window(self, hwnd: int) -> bool:
        """Minimize a window."""
        try:
            if not win32gui.IsWindow(hwnd):
                return False

            win32gui.ShowWindow(hwnd, SW_MINIMIZE)
            time.sleep(self.verification_delay)

            _, is_minimized, _ = self._get_window_state(hwnd)
            return is_minimized
        except Exception as e:
            logger.error(f"Failed to minimize window {hwnd}: {e}")
            return False

    def maximize_window(self, hwnd: int) -> bool:
        """Maximize a window."""
        try:
            if not win32gui.IsWindow(hwnd):
                return False

            win32gui.ShowWindow(hwnd, SW_MAXIMIZE)
            time.sleep(self.verification_delay)

            _, _, is_maximized = self._get_window_state(hwnd)
            return is_maximized
        except Exception as e:
            logger.error(f"Failed to maximize window {hwnd}: {e}")
            return False

    def restore_window(self, hwnd: int) -> bool:
        """Restore a window from minimized or maximized state."""
        try:
            if not win32gui.IsWindow(hwnd):
                return False

            win32gui.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(self.verification_delay)

            _, is_minimized, is_maximized = self._get_window_state(hwnd)
            return not (is_minimized or is_maximized)
        except Exception as e:
            logger.error(f"Failed to restore window {hwnd}: {e}")
            return False

    def close_window(self, hwnd: int) -> bool:
        """Close a window."""
        try:
            if not win32gui.IsWindow(hwnd):
                return False

            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            time.sleep(self.verification_delay * 5)  # Give time for graceful close

            return not win32gui.IsWindow(hwnd)
        except Exception as e:
            logger.error(f"Failed to close window {hwnd}: {e}")
            return False

    def move_window(self, hwnd: int, x: int, y: int, width: int, height: int) -> bool:
        """Move and resize a window."""
        try:
            if not win32gui.IsWindow(hwnd):
                return False

            win32gui.MoveWindow(hwnd, x, y, width, height, True)
            time.sleep(self.verification_delay)

            rect = win32gui.GetWindowRect(hwnd)
            return (rect[0] == x and rect[1] == y and
                    rect[2] - rect[0] == width and rect[3] - rect[1] == height)
        except Exception as e:
            logger.error(f"Failed to move window {hwnd}: {e}")
            return False

    def get_activation_stats(self) -> dict:
        """Get statistics about activation success rates (for testing)."""
        return {
            "total_attempts": 0,
            "successful_activations": 0,
            "success_rate": 0.0,
            "strategy_success_rates": {}
        }


_focus_controller = None


def get_focus_controller() -> FocusController:
    """Get shared FocusController instance."""
    global _focus_controller
    if _focus_controller is None:
        _focus_controller = FocusController()
    return _focus_controller


def activate_window(hwnd: int) -> ActivationResult:
    """Activate a window by handle."""
    return get_focus_controller().activate_window(hwnd)


def activate_window_by_title(title: str, exact_match: bool = False) -> ActivationResult:
    """Activate a window by title."""
    enumerator = get_enumerator()
    matches = enumerator.find_windows_by_title(title, exact_match)

    if not matches:
        return ActivationResult.WINDOW_NOT_FOUND

    return get_focus_controller().activate_window(matches[0].hwnd)


def activate_window_by_process(process_name: str) -> ActivationResult:
    """Activate a window by process name."""
    enumerator = get_enumerator()
    matches = enumerator.find_windows_by_process(process_name)

    if not matches:
        return ActivationResult.WINDOW_NOT_FOUND

    return get_focus_controller().activate_window(matches[0].hwnd)


def minimize_window(hwnd: int) -> bool:
    """Minimize a window."""
    return get_focus_controller().minimize_window(hwnd)


def maximize_window(hwnd: int) -> bool:
    """Maximize a window."""
    return get_focus_controller().maximize_window(hwnd)


def restore_window(hwnd: int) -> bool:
    """Restore a window."""
    return get_focus_controller().restore_window(hwnd)
