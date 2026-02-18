"""
Comprehensive Input and Clipboard Services

Provides reliable keyboard, mouse, clipboard, and hotkey operations with safety
mechanisms. Exposes module-level convenience functions backed by global singleton
instances of MouseOperations, KeyboardOperations, and ClipboardManager.

Ported from WindowsHarness.
"""

import ctypes
import ctypes.wintypes
import logging
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum

from ctypes import wintypes
import win32api
import win32con
import win32clipboard

logger = logging.getLogger(__name__)

# Windows API constants
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt key
VK_LWIN = 0x5B
VK_RWIN = 0x5C

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

class ButtonType(Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"

@dataclass
class InputResult:
    """Result of input operation"""
    success: bool
    message: str
    execution_time_ms: float
    coordinates: Optional[Tuple[int, int]] = None

@dataclass
class InputStats:
    """Input operation statistics"""
    total_operations: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    avg_execution_time_ms: float = 0.0
    last_error: Optional[str] = None

# Windows API structures
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))
    ]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    _fields_ = [("type", wintypes.DWORD), ("_input", _INPUT)]

class MouseOperations:
    """Advanced mouse operations with SendInput"""

    def __init__(self, config: Dict[str, Any]):
        self.click_delay_ms = config.get("click_delay_ms", 100)
        self.move_duration_ms = config.get("move_duration_ms", 200)
        self.scroll_lines = config.get("scroll_lines", 3)

        self.screen_width = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        self.screen_height = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)

        logger.info(f"Mouse operations initialized - Screen: {self.screen_width}x{self.screen_height}")

    def click(self, x: int, y: int, button: ButtonType = ButtonType.LEFT,
              double_click: bool = False) -> InputResult:
        """Perform mouse click at coordinates"""
        start_time = time.time()

        try:
            if not self._validate_coordinates(x, y):
                return InputResult(
                    success=False,
                    message=f"Invalid coordinates: ({x}, {y})",
                    execution_time_ms=0
                )

            self._move_to(x, y)
            time.sleep(self.click_delay_ms / 1000.0)

            if button == ButtonType.LEFT:
                down_flag = MOUSEEVENTF_LEFTDOWN
                up_flag = MOUSEEVENTF_LEFTUP
            elif button == ButtonType.RIGHT:
                down_flag = MOUSEEVENTF_RIGHTDOWN
                up_flag = MOUSEEVENTF_RIGHTUP
            elif button == ButtonType.MIDDLE:
                down_flag = MOUSEEVENTF_MIDDLEDOWN
                up_flag = MOUSEEVENTF_MIDDLEUP
            else:
                return InputResult(False, f"Invalid button type: {button}", 0)

            clicks = 2 if double_click else 1

            for _ in range(clicks):
                self._send_mouse_input(0, 0, 0, down_flag)
                time.sleep(0.01)  # Brief delay between down and up

                self._send_mouse_input(0, 0, 0, up_flag)

                if double_click and clicks > 1:
                    time.sleep(0.05)  # Delay between clicks in double-click

            execution_time = (time.time() - start_time) * 1000

            return InputResult(
                success=True,
                message=f"{'Double-' if double_click else ''}Clicked {button.value} at ({x}, {y})",
                execution_time_ms=execution_time,
                coordinates=(x, y)
            )

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            logger.error(f"Mouse click failed: {e}")
            return InputResult(False, str(e), execution_time)

    def move(self, x: int, y: int, duration_ms: Optional[int] = None) -> InputResult:
        """Move mouse cursor smoothly to coordinates"""
        start_time = time.time()

        try:
            if not self._validate_coordinates(x, y):
                return InputResult(False, f"Invalid coordinates: ({x}, {y})", 0)

            current_pos = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(current_pos))

            start_x, start_y = current_pos.x, current_pos.y
            duration = duration_ms or self.move_duration_ms

            if duration > 0:
                steps = max(10, duration // 10)  # At least 10 steps
                for i in range(steps + 1):
                    progress = i / steps
                    progress = self._ease_in_out(progress)

                    current_x = int(start_x + (x - start_x) * progress)
                    current_y = int(start_y + (y - start_y) * progress)

                    self._move_to(current_x, current_y)
                    time.sleep(duration / 1000.0 / steps)
            else:
                self._move_to(x, y)

            execution_time = (time.time() - start_time) * 1000

            return InputResult(
                success=True,
                message=f"Moved to ({x}, {y})",
                execution_time_ms=execution_time,
                coordinates=(x, y)
            )

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            logger.error(f"Mouse move failed: {e}")
            return InputResult(False, str(e), execution_time)

    def scroll(self, x: int, y: int, direction: str, amount: int = 3) -> InputResult:
        """Scroll at coordinates"""
        start_time = time.time()

        try:
            if not self._validate_coordinates(x, y):
                return InputResult(False, f"Invalid coordinates: ({x}, {y})", 0)

            self._move_to(x, y)
            time.sleep(0.1)

            if direction.lower() in ['up', 'down']:
                scroll_delta = 120 * amount  # Standard scroll wheel delta
                if direction.lower() == 'down':
                    scroll_delta = -scroll_delta

                self._send_mouse_input(0, 0, scroll_delta, MOUSEEVENTF_WHEEL)
            else:
                return InputResult(False, f"Invalid scroll direction: {direction}", 0)

            execution_time = (time.time() - start_time) * 1000

            return InputResult(
                success=True,
                message=f"Scrolled {direction} {amount} lines at ({x}, {y})",
                execution_time_ms=execution_time,
                coordinates=(x, y)
            )

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            logger.error(f"Mouse scroll failed: {e}")
            return InputResult(False, str(e), execution_time)

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int,
             button: ButtonType = ButtonType.LEFT, duration_ms: int = 500) -> InputResult:
        """Perform drag operation"""
        start_time = time.time()

        try:
            if not self._validate_coordinates(start_x, start_y) or not self._validate_coordinates(end_x, end_y):
                return InputResult(False, "Invalid drag coordinates", 0)

            if button == ButtonType.LEFT:
                down_flag = MOUSEEVENTF_LEFTDOWN
                up_flag = MOUSEEVENTF_LEFTUP
            else:
                return InputResult(False, f"Drag not supported for {button.value} button", 0)

            self._move_to(start_x, start_y)
            time.sleep(0.1)

            self._send_mouse_input(0, 0, 0, down_flag)
            time.sleep(0.05)

            self.move(end_x, end_y, duration_ms)

            self._send_mouse_input(0, 0, 0, up_flag)

            execution_time = (time.time() - start_time) * 1000

            return InputResult(
                success=True,
                message=f"Dragged from ({start_x}, {start_y}) to ({end_x}, {end_y})",
                execution_time_ms=execution_time,
                coordinates=(end_x, end_y)
            )

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            logger.error(f"Mouse drag failed: {e}")
            return InputResult(False, str(e), execution_time)

    def _move_to(self, x: int, y: int):
        """Move cursor to absolute coordinates"""
        abs_x = int((x * 65535) / self.screen_width)
        abs_y = int((y * 65535) / self.screen_height)

        self._send_mouse_input(abs_x, abs_y, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)

    def _send_mouse_input(self, x: int, y: int, data: int, flags: int):
        """Send mouse input using SendInput"""
        mouse_input = MOUSEINPUT(
            dx=x, dy=y, mouseData=data, dwFlags=flags,
            time=0, dwExtraInfo=None
        )

        input_struct = INPUT(type=INPUT_MOUSE)
        input_struct._input.mi = mouse_input

        result = ctypes.windll.user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))
        if result == 0:
            raise Exception("SendInput failed for mouse")

    def _validate_coordinates(self, x: int, y: int) -> bool:
        """Validate screen coordinates"""
        return 0 <= x <= self.screen_width and 0 <= y <= self.screen_height

    def _ease_in_out(self, t: float) -> float:
        """Smooth easing function for mouse movement"""
        return t * t * (3.0 - 2.0 * t)

class KeyboardOperations:
    """Advanced keyboard operations with SendInput"""

    # Virtual key code mappings
    VK_CODES = {
        'backspace': 0x08, 'tab': 0x09, 'enter': 0x0D, 'shift': 0x10,
        'ctrl': 0x11, 'alt': 0x12, 'pause': 0x13, 'caps': 0x14,
        'escape': 0x1B, 'space': 0x20, 'pageup': 0x21, 'pagedown': 0x22,
        'end': 0x23, 'home': 0x24, 'left': 0x25, 'up': 0x26,
        'right': 0x27, 'down': 0x28, 'insert': 0x2D, 'delete': 0x2E,
        'win': 0x5B, 'menu': 0x5D, 'f1': 0x70, 'f2': 0x71,
        'f3': 0x72, 'f4': 0x73, 'f5': 0x74, 'f6': 0x75,
        'f7': 0x76, 'f8': 0x77, 'f9': 0x78, 'f10': 0x79,
        'f11': 0x7A, 'f12': 0x7B, 'numlock': 0x90, 'scroll': 0x91
    }

    def __init__(self, config: Dict[str, Any]):
        self.typing_speed_wpm = config.get("typing_speed_wpm", 300)
        self.key_delay_ms = config.get("key_delay_ms", 50)

        # Calculate typing delay from WPM
        # Average word length is 5 characters
        chars_per_minute = self.typing_speed_wpm * 5
        self.char_delay_ms = 60000 / chars_per_minute  # Convert to milliseconds

        logger.info(f"Keyboard operations initialized - WPM: {self.typing_speed_wpm}, Delay: {self.char_delay_ms:.1f}ms/char")

    def type_text(self, text: str, speed_wpm: Optional[int] = None) -> InputResult:
        """Type text with specified speed"""
        start_time = time.time()

        try:
            if not text:
                return InputResult(False, "Empty text provided", 0)

            if speed_wpm:
                char_delay = 60000 / (speed_wpm * 5) / 1000.0  # Convert to seconds
            else:
                char_delay = self.char_delay_ms / 1000.0

            for char in text:
                if char == '\n':
                    self._send_key(self.VK_CODES['enter'])
                elif char == '\t':
                    self._send_key(self.VK_CODES['tab'])
                else:
                    self._send_unicode_char(char)

                if char_delay > 0:
                    time.sleep(char_delay)

            execution_time = (time.time() - start_time) * 1000

            return InputResult(
                success=True,
                message=f"Typed {len(text)} characters at {speed_wpm or self.typing_speed_wpm} WPM",
                execution_time_ms=execution_time
            )

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            logger.error(f"Type text failed: {e}")
            return InputResult(False, str(e), execution_time)

    def send_hotkey(self, hotkey: str) -> InputResult:
        """Send hotkey combination (e.g., 'ctrl+c', 'alt+tab')"""
        start_time = time.time()

        try:
            keys = [key.strip().lower() for key in hotkey.split('+')]

            if not keys:
                return InputResult(False, "Invalid hotkey format", 0)

            vk_keys = []
            for key in keys:
                if key in self.VK_CODES:
                    vk_keys.append(self.VK_CODES[key])
                elif len(key) == 1 and key.isalnum():
                    vk_keys.append(ord(key.upper()))
                else:
                    return InputResult(False, f"Unknown key: {key}", 0)

            for vk_key in vk_keys:
                self._send_key_down(vk_key)
                time.sleep(0.01)  # Brief delay between keys

            time.sleep(0.05)  # Hold keys briefly

            for vk_key in reversed(vk_keys):
                self._send_key_up(vk_key)
                time.sleep(0.01)

            execution_time = (time.time() - start_time) * 1000

            return InputResult(
                success=True,
                message=f"Sent hotkey: {hotkey}",
                execution_time_ms=execution_time
            )

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            logger.error(f"Send hotkey failed: {e}")
            return InputResult(False, str(e), execution_time)

    def send_key(self, key: str, hold_duration_ms: int = 0) -> InputResult:
        """Send single key press"""
        start_time = time.time()

        try:
            if key.lower() in self.VK_CODES:
                vk_key = self.VK_CODES[key.lower()]
            elif len(key) == 1 and key.isalnum():
                vk_key = ord(key.upper())
            else:
                return InputResult(False, f"Unknown key: {key}", 0)

            if hold_duration_ms > 0:
                self._send_key_down(vk_key)
                time.sleep(hold_duration_ms / 1000.0)
                self._send_key_up(vk_key)
            else:
                self._send_key(vk_key)

            execution_time = (time.time() - start_time) * 1000

            return InputResult(
                success=True,
                message=f"Sent key: {key}" + (f" (held {hold_duration_ms}ms)" if hold_duration_ms > 0 else ""),
                execution_time_ms=execution_time
            )

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            logger.error(f"Send key failed: {e}")
            return InputResult(False, str(e), execution_time)

    def _send_key(self, vk_key: int):
        """Send key press and release"""
        self._send_key_down(vk_key)
        time.sleep(0.01)
        self._send_key_up(vk_key)

    def _send_key_down(self, vk_key: int):
        """Send key down event"""
        scan_code = ctypes.windll.user32.MapVirtualKeyW(vk_key, 0)

        kb_input = KEYBDINPUT(
            wVk=vk_key,
            wScan=scan_code,
            dwFlags=KEYEVENTF_SCANCODE,
            time=0,
            dwExtraInfo=None
        )

        input_struct = INPUT(type=INPUT_KEYBOARD)
        input_struct._input.ki = kb_input

        result = ctypes.windll.user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))
        if result == 0:
            raise Exception("SendInput failed for key down")

    def _send_key_up(self, vk_key: int):
        """Send key up event"""
        scan_code = ctypes.windll.user32.MapVirtualKeyW(vk_key, 0)

        kb_input = KEYBDINPUT(
            wVk=vk_key,
            wScan=scan_code,
            dwFlags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP,
            time=0,
            dwExtraInfo=None
        )

        input_struct = INPUT(type=INPUT_KEYBOARD)
        input_struct._input.ki = kb_input

        result = ctypes.windll.user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))
        if result == 0:
            raise Exception("SendInput failed for key up")

    def _send_unicode_char(self, char: str):
        """Send Unicode character using KEYEVENTF_UNICODE"""
        unicode_val = ord(char)

        kb_input = KEYBDINPUT(
            wVk=0,
            wScan=unicode_val,
            dwFlags=KEYEVENTF_UNICODE,
            time=0,
            dwExtraInfo=None
        )

        input_struct = INPUT(type=INPUT_KEYBOARD)
        input_struct._input.ki = kb_input

        result = ctypes.windll.user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))
        if result == 0:
            raise Exception("SendInput failed for Unicode char down")

        kb_input.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
        input_struct._input.ki = kb_input

        result = ctypes.windll.user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))
        if result == 0:
            raise Exception("SendInput failed for Unicode char up")

class ClipboardManager:
    """Advanced clipboard operations with retry logic"""

    def __init__(self):
        self.max_retries = 5
        self.retry_delay_ms = 100
        logger.info("Clipboard manager initialized")

    def set_text(self, text: str) -> InputResult:
        """Set clipboard text with retry logic"""
        start_time = time.time()

        for attempt in range(self.max_retries):
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text)
                win32clipboard.CloseClipboard()

                execution_time = (time.time() - start_time) * 1000
                return InputResult(
                    success=True,
                    message=f"Set clipboard text ({len(text)} chars)",
                    execution_time_ms=execution_time
                )

            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay_ms / 1000.0)
                    continue
                else:
                    execution_time = (time.time() - start_time) * 1000
                    logger.error(f"Set clipboard text failed: {e}")
                    return InputResult(False, str(e), execution_time)
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except Exception as cleanup_error:
                    logger.warning(f"Failed to close clipboard during cleanup: {cleanup_error}")

    def get_text(self) -> Tuple[bool, str]:
        """Get clipboard text with retry logic"""
        for attempt in range(self.max_retries):
            try:
                win32clipboard.OpenClipboard()
                text = win32clipboard.GetClipboardData(win32clipboard.CF_TEXT)
                win32clipboard.CloseClipboard()

                if isinstance(text, bytes):
                    text = text.decode('utf-8', errors='ignore')

                return True, text

            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay_ms / 1000.0)
                    continue
                else:
                    logger.error(f"Get clipboard text failed: {e}")
                    return False, str(e)
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except Exception as cleanup_error:
                    logger.warning(f"Failed to close clipboard during cleanup: {cleanup_error}")

        return False, "Max retries exceeded"

    def paste(self) -> InputResult:
        """Paste clipboard content using Ctrl+V"""
        keyboard = KeyboardOperations({"typing_speed_wpm": 300, "key_delay_ms": 50})
        return keyboard.send_hotkey("ctrl+v")

# Global instances
_mouse_ops = None
_keyboard_ops = None
_clipboard_manager = None
_input_stats = InputStats()

def initialize_input_services(config: Dict[str, Any]):
    """Initialize global input service instances"""
    global _mouse_ops, _keyboard_ops, _clipboard_manager

    _mouse_ops = MouseOperations(config)
    _keyboard_ops = KeyboardOperations(config)
    _clipboard_manager = ClipboardManager()

    logger.info("Input services initialized")

# Public API functions
def click(x: int, y: int, button: str = "left", double_click: bool = False) -> InputResult:
    """Click at coordinates"""
    if not _mouse_ops:
        raise RuntimeError("Input services not initialized")

    button_type = ButtonType(button)
    result = _mouse_ops.click(x, y, button_type, double_click)

    _input_stats.total_operations += 1
    if result.success:
        _input_stats.successful_operations += 1
    else:
        _input_stats.failed_operations += 1
        _input_stats.last_error = result.message

    return result

def move_mouse(x: int, y: int, duration_ms: int = 200) -> InputResult:
    """Move mouse to coordinates"""
    if not _mouse_ops:
        raise RuntimeError("Input services not initialized")

    return _mouse_ops.move(x, y, duration_ms)

def type_text(text: str, speed_wpm: int = 300) -> InputResult:
    """Type text at specified speed"""
    if not _keyboard_ops:
        raise RuntimeError("Input services not initialized")

    result = _keyboard_ops.type_text(text, speed_wpm)

    _input_stats.total_operations += 1
    if result.success:
        _input_stats.successful_operations += 1
    else:
        _input_stats.failed_operations += 1
        _input_stats.last_error = result.message

    return result

def send_hotkey(hotkey: str) -> InputResult:
    """Send hotkey combination"""
    if not _keyboard_ops:
        raise RuntimeError("Input services not initialized")

    return _keyboard_ops.send_hotkey(hotkey)

def scroll(x: int, y: int, direction: str = "down", amount: int = 3) -> InputResult:
    """Scroll at the given screen coordinates.
    - direction: 'up' or 'down'
    - amount: scroll steps (default 3)
    """
    if not _mouse_ops:
        raise RuntimeError("Input services not initialized")
    d = str(direction or "down").lower()
    if d not in ("up", "down"):
        d = "down"
    return _mouse_ops.scroll(int(x), int(y), d, int(amount))

def set_clipboard(text: str) -> InputResult:
    """Set clipboard text"""
    if not _clipboard_manager:
        raise RuntimeError("Input services not initialized")

    return _clipboard_manager.set_text(text)

def get_clipboard() -> Tuple[bool, str]:
    """Get clipboard text"""
    if not _clipboard_manager:
        raise RuntimeError("Input services not initialized")

    return _clipboard_manager.get_text()

def get_input_stats() -> InputStats:
    """Get input operation statistics"""
    if _input_stats.total_operations > 0:
        _input_stats.avg_execution_time_ms = (
            _input_stats.successful_operations / _input_stats.total_operations
        ) * 100  # Simplified calculation

    return _input_stats
