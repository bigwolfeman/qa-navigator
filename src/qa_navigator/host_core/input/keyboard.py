"""
Keyboard Operations Controller

Provides comprehensive keyboard automation using SendInput with proper scan codes.
Supports IME-safe text input, international layouts, and modifier key combinations.

Ported from WindowsHarness.
"""

import ctypes
from ctypes import wintypes, Structure, Union, POINTER, byref, windll
import time
import threading
import queue
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import IntEnum
import logging

logger = logging.getLogger(__name__)

# Windows API Constants
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

# Virtual Key Codes
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt key
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_APPS = 0x5D  # Menu key

class ModifierKey(IntEnum):
    SHIFT = VK_SHIFT
    CTRL = VK_CONTROL
    ALT = VK_MENU
    WIN = VK_LWIN

@dataclass
class KeyAction:
    """Represents a keyboard action to be queued"""
    action_type: str
    text: Optional[str] = None
    key_code: Optional[int] = None
    scan_code: Optional[int] = None
    modifiers: Optional[List[ModifierKey]] = None
    duration: Optional[float] = None
    speed_wpm: Optional[int] = None
    timestamp: float = 0.0

# Windows API Structures
class KEYBDINPUT(Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", POINTER(wintypes.ULONG))
    ]

class INPUT_UNION(Union):
    _fields_ = [("ki", KEYBDINPUT)]

class INPUT(Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION)
    ]

class KeyboardController:
    """High-performance keyboard controller with scan code support and IME compatibility"""

    # Comprehensive virtual key code mapping
    VK_MAP = {
        # Letters (A-Z)
        **{chr(i): i for i in range(ord('A'), ord('Z') + 1)},
        # Numbers (0-9)
        **{str(i): ord('0') + i for i in range(10)},
        # Function keys
        **{f"F{i}": 0x70 + i - 1 for i in range(1, 25)},
        # Special keys
        'SPACE': 0x20, 'ENTER': 0x0D, 'RETURN': 0x0D, 'TAB': 0x09, 'BACKSPACE': 0x08,
        'DELETE': 0x2E, 'INSERT': 0x2D, 'HOME': 0x24, 'END': 0x23, 'PAGEUP': 0x21, 'PAGEDOWN': 0x22,
        'UP': 0x26, 'DOWN': 0x28, 'LEFT': 0x25, 'RIGHT': 0x27,
        'ESCAPE': 0x1B, 'ESC': 0x1B, 'CAPSLOCK': 0x14, 'NUMLOCK': 0x90, 'SCROLLLOCK': 0x91,
        'PRINTSCREEN': 0x2C, 'PAUSE': 0x13, 'BREAK': 0x03,
        # Numpad
        'NUMPAD0': 0x60, 'NUMPAD1': 0x61, 'NUMPAD2': 0x62, 'NUMPAD3': 0x63, 'NUMPAD4': 0x64,
        'NUMPAD5': 0x65, 'NUMPAD6': 0x66, 'NUMPAD7': 0x67, 'NUMPAD8': 0x68, 'NUMPAD9': 0x69,
        'MULTIPLY': 0x6A, 'ADD': 0x6B, 'SEPARATOR': 0x6C, 'SUBTRACT': 0x6D, 'DECIMAL': 0x6E, 'DIVIDE': 0x6F,
        # Symbols and punctuation
        'SEMICOLON': 0xBA, 'EQUALS': 0xBB, 'COMMA': 0xBC, 'MINUS': 0xBD, 'PERIOD': 0xBE, 'SLASH': 0xBF,
        'GRAVE': 0xC0, 'LBRACKET': 0xDB, 'BACKSLASH': 0xDC, 'RBRACKET': 0xDD, 'QUOTE': 0xDE,
        # Media keys
        'VOLUME_MUTE': 0xAD, 'VOLUME_DOWN': 0xAE, 'VOLUME_UP': 0xAF,
        'MEDIA_NEXT_TRACK': 0xB0, 'MEDIA_PREV_TRACK': 0xB1, 'MEDIA_STOP': 0xB2, 'MEDIA_PLAY_PAUSE': 0xB3,
    }

    # Scan code mappings for reliability
    SCAN_CODE_MAP = {
        # Letters
        'A': 0x1E, 'B': 0x30, 'C': 0x2E, 'D': 0x20, 'E': 0x12, 'F': 0x21, 'G': 0x22, 'H': 0x23,
        'I': 0x17, 'J': 0x24, 'K': 0x25, 'L': 0x26, 'M': 0x32, 'N': 0x31, 'O': 0x18, 'P': 0x19,
        'Q': 0x10, 'R': 0x13, 'S': 0x1F, 'T': 0x14, 'U': 0x16, 'V': 0x2F, 'W': 0x11, 'X': 0x2D, 'Y': 0x15, 'Z': 0x2C,
        # Numbers
        '1': 0x02, '2': 0x03, '3': 0x04, '4': 0x05, '5': 0x06, '6': 0x07, '7': 0x08, '8': 0x09, '9': 0x0A, '0': 0x0B,
        # Function keys
        **{f"F{i}": 0x3B + i - 1 for i in range(1, 11)},
        'F11': 0x57, 'F12': 0x58,
        # Special keys
        'SPACE': 0x39, 'ENTER': 0x1C, 'TAB': 0x0F, 'BACKSPACE': 0x0E, 'ESCAPE': 0x01,
        'UP': 0xC8, 'DOWN': 0xD0, 'LEFT': 0xCB, 'RIGHT': 0xCD,
        'HOME': 0xC7, 'END': 0xCF, 'PAGEUP': 0xC9, 'PAGEDOWN': 0xD1,
        'DELETE': 0xD3, 'INSERT': 0xD2,
        # Modifiers
        'SHIFT': 0x2A, 'CTRL': 0x1D, 'ALT': 0x38, 'WIN': 0xE05B,
    }

    def __init__(self, safety=None, validator=None, monitor=None):
        self.safety = safety
        self.validator = validator
        self.monitor = monitor

        self._action_queue = queue.Queue()
        self._processing_thread = None
        self._stop_processing = threading.Event()
        self._rate_limit_delay = 0.001  # 1ms default between actions
        self._last_action_time = 0.0

        # Typing settings
        self._default_wpm = 300  # Words per minute
        self._min_key_delay = 0.001  # Minimum delay between keys
        self._max_key_delay = 0.1    # Maximum delay between keys

        # IME and Unicode support
        self._ime_safe_mode = True
        self._unicode_fallback = True

        # Modifier state tracking
        self._held_modifiers = set()

        self._start_processing_thread()

    def _start_processing_thread(self):
        """Start the action processing thread"""
        self._processing_thread = threading.Thread(target=self._process_actions, daemon=True)
        self._processing_thread.start()

    def _process_actions(self):
        """Process queued keyboard actions"""
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
                logger.error(f"Error processing keyboard action: {e}")
                if self.monitor:
                    self.monitor.record_error("keyboard_processing", str(e))

    def _execute_action(self, action: KeyAction) -> bool:
        """Execute a single keyboard action"""
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

            if action.action_type == "type":
                success = self._perform_type(action.text, action.speed_wpm)
            elif action.action_type == "key":
                success = self._perform_key(action.key_code, action.scan_code, action.modifiers)
            elif action.action_type == "hotkey":
                success = self._perform_hotkey(action.text)
            elif action.action_type == "hold":
                success = self._perform_hold(action.key_code, action.scan_code)
            elif action.action_type == "release":
                success = self._perform_release(action.key_code, action.scan_code)

            end_time = time.time()
            self._last_action_time = end_time

            if self.monitor:
                self.monitor.record_action(
                    action_type=f"keyboard_{action.action_type}",
                    duration=end_time - start_time,
                    success=success
                )

            return success

        except Exception as e:
            logger.error(f"Error executing keyboard action {action.action_type}: {e}")
            if self.monitor:
                self.monitor.record_error(f"keyboard_{action.action_type}", str(e))
            return False

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

    def _create_keyboard_input(self, vk_code=0, scan_code=0, flags=0) -> INPUT:
        """Create a keyboard input structure"""
        kb_input = INPUT()
        kb_input.type = INPUT_KEYBOARD
        kb_input.union.ki.wVk = vk_code
        kb_input.union.ki.wScan = scan_code
        kb_input.union.ki.dwFlags = flags
        kb_input.union.ki.time = 0
        kb_input.union.ki.dwExtraInfo = None
        return kb_input

    def _get_vk_and_scan_code(self, key: str) -> Tuple[int, int]:
        """Get virtual key code and scan code for a key"""
        key_upper = key.upper()
        vk_code = self.VK_MAP.get(key_upper, 0)
        scan_code = self.SCAN_CODE_MAP.get(key_upper, 0)
        return vk_code, scan_code

    def _perform_type(self, text: str, speed_wpm: Optional[int] = None) -> bool:
        """Type text with configurable speed"""
        if not text:
            return True

        if self.validator and not self.validator.validate_text_input(text):
            return False

        speed_wpm = speed_wpm or self._default_wpm

        # Calculate delay between characters based on WPM
        # Assume average word length of 5 characters
        chars_per_minute = speed_wpm * 5
        delay_per_char = 60.0 / chars_per_minute
        delay_per_char = max(self._min_key_delay, min(self._max_key_delay, delay_per_char))

        success_count = 0
        total_chars = len(text)

        for i, char in enumerate(text):
            if self.safety and not self.safety.can_perform_input():
                return False

            if self._type_character(char):
                success_count += 1

            if i < total_chars - 1:
                time.sleep(delay_per_char)

        return success_count == total_chars

    def _type_character(self, char: str) -> bool:
        """Type a single character"""
        if char == '\n':
            return self._press_key('ENTER')
        elif char == '\t':
            return self._press_key('TAB')
        elif char == '\r':
            return True  # Skip carriage return

        if 32 <= ord(char) <= 126:
            shifted_chars = {
                '!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
                '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\', ':': ';', '"': "'", '<': ',', '>': '.', '?': '/'
            }

            if char in shifted_chars:
                base_char = shifted_chars[char]
                vk_code, scan_code = self._get_vk_and_scan_code(base_char)
                if vk_code:
                    return self._send_key_with_modifiers(vk_code, scan_code, [ModifierKey.SHIFT])
            elif char.isupper():
                vk_code, scan_code = self._get_vk_and_scan_code(char)
                if vk_code:
                    return self._send_key_with_modifiers(vk_code, scan_code, [ModifierKey.SHIFT])
            else:
                vk_code, scan_code = self._get_vk_and_scan_code(char)
                if vk_code:
                    return self._press_key_by_codes(vk_code, scan_code)

        if self._unicode_fallback:
            return self._type_unicode_character(char)

        return False

    def _type_unicode_character(self, char: str) -> bool:
        """Type a character using Unicode input method"""
        try:
            inputs = []

            inputs.append(self._create_keyboard_input(
                vk_code=0,
                scan_code=ord(char),
                flags=KEYEVENTF_UNICODE
            ))

            inputs.append(self._create_keyboard_input(
                vk_code=0,
                scan_code=ord(char),
                flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
            ))

            return self._send_input(inputs)
        except Exception as e:
            logger.error(f"Failed to type Unicode character '{char}': {e}")
            return False

    def _press_key(self, key: str) -> bool:
        """Press and release a single key"""
        vk_code, scan_code = self._get_vk_and_scan_code(key)
        if not vk_code:
            return False
        return self._press_key_by_codes(vk_code, scan_code)

    def _press_key_by_codes(self, vk_code: int, scan_code: int) -> bool:
        """Press and release a key by its codes"""
        inputs = [
            self._create_keyboard_input(vk_code, scan_code, KEYEVENTF_SCANCODE),
            self._create_keyboard_input(vk_code, scan_code, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
        ]
        return self._send_input(inputs)

    def _send_key_with_modifiers(self, vk_code: int, scan_code: int, modifiers: List[ModifierKey]) -> bool:
        """Send a key press with modifier keys"""
        inputs = []

        for modifier in modifiers:
            mod_vk, mod_scan = self._get_vk_and_scan_code(modifier.name)
            inputs.append(self._create_keyboard_input(mod_vk, mod_scan, KEYEVENTF_SCANCODE))

        inputs.append(self._create_keyboard_input(vk_code, scan_code, KEYEVENTF_SCANCODE))
        inputs.append(self._create_keyboard_input(vk_code, scan_code, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP))

        for modifier in reversed(modifiers):
            mod_vk, mod_scan = self._get_vk_and_scan_code(modifier.name)
            inputs.append(self._create_keyboard_input(mod_vk, mod_scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP))

        return self._send_input(inputs)

    def _perform_key(self, key_code: Optional[int], scan_code: Optional[int], modifiers: Optional[List[ModifierKey]]) -> bool:
        """Perform a key press with optional modifiers"""
        if key_code and scan_code:
            if modifiers:
                return self._send_key_with_modifiers(key_code, scan_code, modifiers)
            else:
                return self._press_key_by_codes(key_code, scan_code)
        return False

    def _perform_hotkey(self, hotkey: str) -> bool:
        """Perform a hotkey combination (e.g., 'Ctrl+S', 'Alt+Tab')"""
        parts = [part.strip() for part in hotkey.split('+')]
        if len(parts) < 2:
            return self._press_key(parts[0])

        modifiers = []
        main_key = parts[-1]

        for mod_str in parts[:-1]:
            mod_str_upper = mod_str.upper()
            if mod_str_upper in ['CTRL', 'CONTROL']:
                modifiers.append(ModifierKey.CTRL)
            elif mod_str_upper == 'ALT':
                modifiers.append(ModifierKey.ALT)
            elif mod_str_upper in ['SHIFT']:
                modifiers.append(ModifierKey.SHIFT)
            elif mod_str_upper in ['WIN', 'WINDOWS', 'CMD']:
                modifiers.append(ModifierKey.WIN)

        vk_code, scan_code = self._get_vk_and_scan_code(main_key)
        if not vk_code:
            return False

        return self._send_key_with_modifiers(vk_code, scan_code, modifiers)

    def _perform_hold(self, key_code: Optional[int], scan_code: Optional[int]) -> bool:
        """Hold down a key (press without release)"""
        if key_code and scan_code:
            inputs = [self._create_keyboard_input(key_code, scan_code, KEYEVENTF_SCANCODE)]
            success = self._send_input(inputs)
            if success:
                self._held_modifiers.add((key_code, scan_code))
            return success
        return False

    def _perform_release(self, key_code: Optional[int], scan_code: Optional[int]) -> bool:
        """Release a held key"""
        if key_code and scan_code:
            inputs = [self._create_keyboard_input(key_code, scan_code, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)]
            success = self._send_input(inputs)
            if success:
                self._held_modifiers.discard((key_code, scan_code))
            return success
        return False

    # Public API Methods

    def type(self, text: str, speed_wpm: int = 300) -> bool:
        """Type text with configurable speed"""
        action = KeyAction("type", text=text, speed_wpm=speed_wpm, timestamp=time.time())
        self._action_queue.put(action)
        return True

    def type_text(self, text: str, speed_wpm: int = 300) -> bool:
        """Type text with configurable speed (alias for type method)"""
        return self.type(text, speed_wpm)

    def press_key(self, key: str) -> bool:
        """Press and release a single key"""
        vk_code, scan_code = self._get_vk_and_scan_code(key)
        action = KeyAction("key", key_code=vk_code, scan_code=scan_code, timestamp=time.time())
        self._action_queue.put(action)
        return True

    def hotkey(self, combination: str) -> bool:
        """Execute a hotkey combination (e.g., 'Ctrl+S', 'Alt+Tab')"""
        action = KeyAction("hotkey", text=combination, timestamp=time.time())
        self._action_queue.put(action)
        return True

    def send_hotkey(self, combination: str) -> bool:
        """Execute a hotkey combination (alias for hotkey method)"""
        return self.hotkey(combination)

    def hold_key(self, key: str) -> bool:
        """Hold down a key without releasing"""
        vk_code, scan_code = self._get_vk_and_scan_code(key)
        action = KeyAction("hold", key_code=vk_code, scan_code=scan_code, timestamp=time.time())
        self._action_queue.put(action)
        return True

    def release_key(self, key: str) -> bool:
        """Release a previously held key"""
        vk_code, scan_code = self._get_vk_and_scan_code(key)
        action = KeyAction("release", key_code=vk_code, scan_code=scan_code, timestamp=time.time())
        self._action_queue.put(action)
        return True

    def release_all_keys(self) -> bool:
        """Release all currently held keys"""
        for key_code, scan_code in list(self._held_modifiers):
            self.release_key_by_codes(key_code, scan_code)
        return True

    def release_key_by_codes(self, key_code: int, scan_code: int) -> bool:
        """Release a key by its codes"""
        action = KeyAction("release", key_code=key_code, scan_code=scan_code, timestamp=time.time())
        self._action_queue.put(action)
        return True

    def set_typing_speed(self, wpm: int):
        """Set default typing speed in words per minute"""
        self._default_wpm = max(10, min(1000, wpm))

    def set_ime_safe_mode(self, enabled: bool):
        """Enable/disable IME safe mode"""
        self._ime_safe_mode = enabled

    def set_unicode_fallback(self, enabled: bool):
        """Enable/disable Unicode fallback for international characters"""
        self._unicode_fallback = enabled

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

    def get_held_keys(self) -> List[Tuple[int, int]]:
        """Get list of currently held keys"""
        return list(self._held_modifiers)

    def cleanup(self):
        """Clean up resources"""
        self.release_all_keys()

        self._stop_processing.set()
        self.cancel_all_actions()

        self._action_queue.put(None)

        if self._processing_thread and self._processing_thread.is_alive():
            self._processing_thread.join(timeout=2.0)

        logger.info("Keyboard controller cleaned up")
