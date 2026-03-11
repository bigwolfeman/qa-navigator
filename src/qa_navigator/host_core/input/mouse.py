"""
Mouse Operations Controller

Provides comprehensive mouse automation using SendInput for maximum compatibility.
Supports DPI-aware coordinates, rate limiting, and precise movement control.

Windows desktop automation layer for QA Navigator.
"""

import ctypes
from ctypes import wintypes, Structure, POINTER, byref, windll
import time
import threading
import math
import queue
from typing import Tuple, Optional, List, Union
from dataclasses import dataclass
from enum import IntEnum
import logging

logger = logging.getLogger(__name__)

# Windows API Constants
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000

INPUT_MOUSE = 0
WHEEL_DELTA = 120

class MouseButton(IntEnum):
    LEFT = 1
    RIGHT = 2
    MIDDLE = 4

class EasingType(IntEnum):
    LINEAR = 0
    EASE_IN = 1
    EASE_OUT = 2
    EASE_IN_OUT = 3

@dataclass
class MouseAction:
    """Represents a mouse action to be queued"""
    action_type: str
    x: Optional[int] = None
    y: Optional[int] = None
    button: Optional[MouseButton] = None
    duration: Optional[float] = None
    amount: Optional[int] = None
    direction: Optional[str] = None
    easing: Optional[EasingType] = None
    timestamp: float = 0.0

# Windows API Structures
class POINT(Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class MOUSEINPUT(Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]

class INPUT(Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION)
    ]

class MouseController:
    """High-performance mouse controller with DPI awareness and safety features"""

    def __init__(self, safety=None, validator=None, monitor=None):
        self.safety = safety
        self.validator = validator
        self.monitor = monitor

        self._action_queue = queue.Queue()
        self._processing_thread = None
        self._stop_processing = threading.Event()
        self._rate_limit_delay = 0.001  # 1ms default between actions
        self._last_action_time = 0.0

        # DPI awareness
        self._dpi_scale_x = 1.0
        self._dpi_scale_y = 1.0
        self._screen_width = 0
        self._screen_height = 0

        # Movement settings
        self._movement_speed = 1000  # pixels per second
        self._min_movement_time = 0.01  # minimum time for any movement
        self._max_movement_time = 2.0   # maximum time for any movement

        self._initialize_system_info()
        self._start_processing_thread()

    def _initialize_system_info(self):
        """Initialize DPI and screen information"""
        try:
            windll.user32.SetProcessDPIAware()

            self._screen_width = windll.user32.GetSystemMetrics(0)   # SM_CXSCREEN
            self._screen_height = windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN

            hdc = windll.user32.GetDC(0)
            if hdc:
                dpi_x = windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
                dpi_y = windll.gdi32.GetDeviceCaps(hdc, 90)  # LOGPIXELSY
                windll.user32.ReleaseDC(0, hdc)

                self._dpi_scale_x = dpi_x / 96.0
                self._dpi_scale_y = dpi_y / 96.0

            logger.info(f"Screen: {self._screen_width}x{self._screen_height}, DPI: {self._dpi_scale_x:.2f}x{self._dpi_scale_y:.2f}")
        except Exception as e:
            logger.error(f"Failed to initialize system info: {e}")
            self._screen_width = 1920
            self._screen_height = 1080

    def _start_processing_thread(self):
        """Start the action processing thread"""
        self._processing_thread = threading.Thread(target=self._process_actions, daemon=True)
        self._processing_thread.start()

    def _process_actions(self):
        """Process queued mouse actions"""
        while not self._stop_processing.is_set():
            try:
                action = self._action_queue.get(timeout=0.1)
                if action is None:  # Sentinel value to stop
                    break

                self._execute_action(action)
                self._action_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing mouse action: {e}")
                if self.monitor:
                    self.monitor.record_error("mouse_processing", str(e))

    def _execute_action(self, action: MouseAction):
        """Execute a single mouse action"""
        try:
            if self.safety and not self.safety.can_perform_input():
                logger.warning("Input blocked by safety system")
                return False

            current_time = time.time()
            time_since_last = current_time - self._last_action_time
            if time_since_last < self._rate_limit_delay:
                time.sleep(self._rate_limit_delay - time_since_last)

            start_time = time.time()
            success = False

            if action.action_type == "click":
                success = self._perform_click(action.x, action.y, action.button)
            elif action.action_type == "double_click":
                success = self._perform_double_click(action.x, action.y, action.button)
            elif action.action_type == "move":
                success = self._perform_move(action.x, action.y, action.duration, action.easing)
            elif action.action_type == "drag":
                success = self._perform_drag(action.x, action.y, action.duration, action.easing)
            elif action.action_type == "scroll":
                success = self._perform_scroll(action.direction, action.amount)

            end_time = time.time()
            self._last_action_time = end_time

            if self.monitor:
                self.monitor.record_action(
                    action_type=f"mouse_{action.action_type}",
                    duration=end_time - start_time,
                    success=success
                )

            return success

        except Exception as e:
            logger.error(f"Error executing mouse action {action.action_type}: {e}")
            if self.monitor:
                self.monitor.record_error(f"mouse_{action.action_type}", str(e))
            return False

    def _to_absolute_coordinates(self, x: int, y: int) -> Tuple[int, int]:
        """Convert screen coordinates to absolute SendInput coordinates"""
        abs_x = int((x * 65535) / self._screen_width)
        abs_y = int((y * 65535) / self._screen_height)
        return abs_x, abs_y

    def _send_input(self, inputs: List[INPUT]) -> bool:
        """Send input events using SendInput API"""
        try:
            array_type = INPUT * len(inputs)
            input_array = array_type(*inputs)
            result = windll.user32.SendInput(len(inputs), input_array, ctypes.sizeof(INPUT))
            return result == len(inputs)
        except Exception as e:
            logger.error(f"SendInput failed: {e}")
            return False

    def _create_mouse_input(self, x=0, y=0, flags=0, data=0) -> INPUT:
        """Create a mouse input structure"""
        mouse_input = INPUT()
        mouse_input.type = INPUT_MOUSE
        mouse_input.union.mi.dx = x
        mouse_input.union.mi.dy = y
        mouse_input.union.mi.dwFlags = flags
        mouse_input.union.mi.mouseData = data
        mouse_input.union.mi.time = 0
        mouse_input.union.mi.dwExtraInfo = None
        return mouse_input

    def _perform_click(self, x: int, y: int, button: MouseButton) -> bool:
        """Perform a mouse click"""
        if self.validator and not self.validator.validate_coordinates(x, y):
            return False

        abs_x, abs_y = self._to_absolute_coordinates(x, y)

        if button == MouseButton.LEFT:
            down_flag = MOUSEEVENTF_LEFTDOWN
            up_flag = MOUSEEVENTF_LEFTUP
        elif button == MouseButton.RIGHT:
            down_flag = MOUSEEVENTF_RIGHTDOWN
            up_flag = MOUSEEVENTF_RIGHTUP
        elif button == MouseButton.MIDDLE:
            down_flag = MOUSEEVENTF_MIDDLEDOWN
            up_flag = MOUSEEVENTF_MIDDLEUP
        else:
            return False

        inputs = [
            self._create_mouse_input(abs_x, abs_y, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE),
            self._create_mouse_input(0, 0, down_flag),
            self._create_mouse_input(0, 0, up_flag)
        ]

        return self._send_input(inputs)

    def _perform_double_click(self, x: int, y: int, button: MouseButton) -> bool:
        """Perform a double click"""
        if not self._perform_click(x, y, button):
            return False

        time.sleep(0.05)  # Small delay between clicks
        return self._perform_click(x, y, button)

    def _perform_move(self, x: int, y: int, duration: Optional[float] = None, easing: Optional[EasingType] = None) -> bool:
        """Perform smooth mouse movement"""
        if self.validator and not self.validator.validate_coordinates(x, y):
            return False

        current_pos = POINT()
        if not windll.user32.GetCursorPos(byref(current_pos)):
            return False

        start_x, start_y = current_pos.x, current_pos.y
        target_x, target_y = x, y

        distance = math.sqrt((target_x - start_x) ** 2 + (target_y - start_y) ** 2)
        if distance < 1:
            return True  # Already at target

        if duration is None:
            duration = max(self._min_movement_time, min(self._max_movement_time, distance / self._movement_speed))

        if easing is None:
            easing = EasingType.EASE_OUT

        steps = max(10, int(duration * 100))  # 100 steps per second minimum
        step_delay = duration / steps

        for i in range(steps + 1):
            progress = i / steps

            if easing == EasingType.EASE_IN:
                progress = progress ** 2
            elif easing == EasingType.EASE_OUT:
                progress = 1 - (1 - progress) ** 2
            elif easing == EasingType.EASE_IN_OUT:
                progress = 0.5 * (2 * progress) ** 2 if progress < 0.5 else 1 - 0.5 * (2 - 2 * progress) ** 2

            current_x = int(start_x + (target_x - start_x) * progress)
            current_y = int(start_y + (target_y - start_y) * progress)

            abs_x, abs_y = self._to_absolute_coordinates(current_x, current_y)
            mouse_input = self._create_mouse_input(abs_x, abs_y, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)

            if not self._send_input([mouse_input]):
                return False

            if i < steps:  # Don't sleep after the last step
                time.sleep(step_delay)

            if self.safety and not self.safety.can_perform_input():
                return False

        return True

    def _perform_drag(self, target_x: int, target_y: int, duration: Optional[float] = None, easing: Optional[EasingType] = None) -> bool:
        """Perform drag operation (assumes button is already held down)"""
        return self._perform_move(target_x, target_y, duration, easing)

    def _perform_scroll(self, direction: str, amount: int) -> bool:
        """Perform scroll operation"""
        if direction not in ["up", "down", "left", "right"]:
            return False

        delta = amount * WHEEL_DELTA
        if direction in ["down", "left"]:
            delta = -delta

        flags = MOUSEEVENTF_WHEEL if direction in ["up", "down"] else MOUSEEVENTF_HWHEEL

        mouse_input = self._create_mouse_input(0, 0, flags, delta)
        return self._send_input([mouse_input])

    # Public API Methods

    def click(self, x: int, y: int, button: Union[str, MouseButton] = MouseButton.LEFT) -> bool:
        """Perform a mouse click"""
        if isinstance(button, str):
            button = {"left": MouseButton.LEFT, "right": MouseButton.RIGHT, "middle": MouseButton.MIDDLE}.get(button.lower(), MouseButton.LEFT)

        action = MouseAction("click", x=x, y=y, button=button, timestamp=time.time())
        self._action_queue.put(action)
        return True

    def double_click(self, x: int, y: int, button: Union[str, MouseButton] = MouseButton.LEFT) -> bool:
        """Perform a double click"""
        if isinstance(button, str):
            button = {"left": MouseButton.LEFT, "right": MouseButton.RIGHT, "middle": MouseButton.MIDDLE}.get(button.lower(), MouseButton.LEFT)

        action = MouseAction("double_click", x=x, y=y, button=button, timestamp=time.time())
        self._action_queue.put(action)
        return True

    def right_click(self, x: int, y: int) -> bool:
        """Perform a right click"""
        return self.click(x, y, MouseButton.RIGHT)

    def middle_click(self, x: int, y: int) -> bool:
        """Perform a middle click"""
        return self.click(x, y, MouseButton.MIDDLE)

    def move(self, x: int, y: int, duration: Optional[float] = None, easing: str = "ease_out") -> bool:
        """Move mouse to coordinates with smooth animation"""
        easing_type = {"linear": EasingType.LINEAR, "ease_in": EasingType.EASE_IN,
                      "ease_out": EasingType.EASE_OUT, "ease_in_out": EasingType.EASE_IN_OUT}.get(easing.lower(), EasingType.EASE_OUT)

        action = MouseAction("move", x=x, y=y, duration=duration, easing=easing_type, timestamp=time.time())
        self._action_queue.put(action)
        return True

    def move_to(self, x: int, y: int, duration: Optional[float] = None, easing: str = "ease_out") -> bool:
        """Move mouse to coordinates with smooth animation (alias for move method)"""
        return self.move(x, y, duration, easing)

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int, button: Union[str, MouseButton] = MouseButton.LEFT, duration: Optional[float] = None) -> bool:
        """Perform drag and drop operation"""
        if isinstance(button, str):
            button = {"left": MouseButton.LEFT, "right": MouseButton.RIGHT, "middle": MouseButton.MIDDLE}.get(button.lower(), MouseButton.LEFT)

        if not self.move(start_x, start_y):
            return False

        abs_x, abs_y = self._to_absolute_coordinates(start_x, start_y)
        down_flag = {MouseButton.LEFT: MOUSEEVENTF_LEFTDOWN, MouseButton.RIGHT: MOUSEEVENTF_RIGHTDOWN, MouseButton.MIDDLE: MOUSEEVENTF_MIDDLEDOWN}[button]
        up_flag = {MouseButton.LEFT: MOUSEEVENTF_LEFTUP, MouseButton.RIGHT: MOUSEEVENTF_RIGHTUP, MouseButton.MIDDLE: MOUSEEVENTF_MIDDLEUP}[button]

        down_input = self._create_mouse_input(0, 0, down_flag)
        if not self._send_input([down_input]):
            return False

        action = MouseAction("drag", x=end_x, y=end_y, duration=duration, easing=EasingType.EASE_OUT, timestamp=time.time())
        self._action_queue.put(action)

        time.sleep(duration or 0.5)
        up_input = self._create_mouse_input(0, 0, up_flag)
        return self._send_input([up_input])

    def scroll(self, direction: str, amount: int = 3) -> bool:
        """Scroll in the specified direction"""
        action = MouseAction("scroll", direction=direction.lower(), amount=amount, timestamp=time.time())
        self._action_queue.put(action)
        return True

    def get_position(self) -> Tuple[int, int]:
        """Get current mouse position"""
        pos = POINT()
        if windll.user32.GetCursorPos(byref(pos)):
            return pos.x, pos.y
        return 0, 0

    def set_speed(self, pixels_per_second: int):
        """Set mouse movement speed"""
        self._movement_speed = max(100, min(10000, pixels_per_second))

    def set_rate_limit(self, delay_ms: int):
        """Set rate limiting delay between actions"""
        self._rate_limit_delay = max(0.001, delay_ms / 1000.0)

    def wait_for_queue_empty(self, timeout: float = 5.0) -> bool:
        """Wait for action queue to be empty"""
        start_time = time.time()
        while not self._action_queue.empty():
            if time.time() - start_time > timeout:
                return False
            time.sleep(0.01)
        return True

    def cancel_all_actions(self):
        """Cancel all pending actions"""
        while not self._action_queue.empty():
            try:
                self._action_queue.get_nowait()
                self._action_queue.task_done()
            except queue.Empty:
                break

    def cleanup(self):
        """Clean up resources"""
        self._stop_processing.set()
        self.cancel_all_actions()

        self._action_queue.put(None)

        if self._processing_thread and self._processing_thread.is_alive():
            self._processing_thread.join(timeout=2.0)

        logger.info("Mouse controller cleaned up")
