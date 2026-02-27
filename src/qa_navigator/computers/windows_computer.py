"""Windows desktop BaseComputer implementation.

Wraps the WindowsHarness host_core modules (capture, mouse, keyboard,
window management) into Google ADK's BaseComputer interface. This enables
Gemini computer-use agents to control the full Windows desktop - not just
browsers, but any application.

Only importable on Windows (requires Win32 APIs).

Ported from WindowsHarness host_core modules.
"""

import asyncio
import subprocess
import time
from io import BytesIO
from pathlib import Path
from typing import Literal, Optional

from google.adk.tools.computer_use.base_computer import (
    BaseComputer,
    ComputerEnvironment,
    ComputerState,
)
from rich.console import Console

# Windows-only imports - guarded at the computers/__init__.py level
from ..host_core.capture import ScreenCapture
from ..host_core.input.mouse import MouseController
from ..host_core.input.keyboard import KeyboardController
from ..host_core.windows import WindowEnumerator, get_enumerator
from ..host_core.focus import FocusController

console = Console()

# Map human-readable key names to VK-compatible names for KeyboardController
KEY_NAME_MAP = {
    "control": "CTRL",
    "ctrl": "CTRL",
    "alt": "ALT",
    "shift": "SHIFT",
    "meta": "WIN",
    "command": "WIN",
    "enter": "RETURN",
    "return": "RETURN",
    "escape": "ESCAPE",
    "esc": "ESCAPE",
    "backspace": "BACKSPACE",
    "delete": "DELETE",
    "tab": "TAB",
    "space": "SPACE",
    "pageup": "PAGEUP",
    "pagedown": "PAGEDOWN",
    "home": "HOME",
    "end": "END",
    "left": "LEFT",
    "right": "RIGHT",
    "up": "UP",
    "down": "DOWN",
    "insert": "INSERT",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
    "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
    "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
}


class WindowsComputer(BaseComputer):
    """Windows desktop automation via host_core modules.

    Controls the actual Windows desktop: screenshots via BitBlt/DWM,
    mouse via SendInput, keyboard via SendInput with Unicode fallback.
    Can target a specific window or the full desktop.

    Coordinates arrive pre-normalized from virtual 1000x1000 to actual
    pixels by the ADK ComputerUseTool layer.
    """

    def __init__(
        self,
        screen_size: tuple[int, int] = (1920, 1080),
        target_window_title: Optional[str] = None,
        settle_time: float = 0.5,
        search_engine_url: str = "https://www.google.com",
        recording_dir: Optional[str] = None,
    ):
        """
        Args:
            screen_size: Desktop resolution (width, height).
            target_window_title: If set, focus this window for testing.
                If None, captures the full desktop.
            settle_time: Seconds to wait after actions before screenshot.
            search_engine_url: URL for the search() method.
            recording_dir: Directory to save screen recording (MP4 via ffmpeg).
                ffmpeg must be on PATH. If None, no recording is made.
        """
        self._screen_size = screen_size
        self._target_window_title = target_window_title
        self._settle_time = settle_time
        self._search_engine_url = search_engine_url
        self._recording_dir = recording_dir

        self._capture: Optional[ScreenCapture] = None
        self._mouse: Optional[MouseController] = None
        self._keyboard: Optional[KeyboardController] = None
        self._enumerator: Optional[WindowEnumerator] = None
        self._focus: Optional[FocusController] = None
        self._target_hwnd: Optional[int] = None
        self._last_screenshot: Optional[bytes] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._video_path: Optional[Path] = None

    async def initialize(self) -> None:
        """Initialize all host_core controllers. Safe to call multiple times."""
        if self._capture is not None:
            return
        console.print("[bold yellow]Initializing Windows desktop automation...[/]")

        self._capture = ScreenCapture()
        self._mouse = MouseController()
        self._keyboard = KeyboardController()
        self._enumerator = get_enumerator()
        self._focus = FocusController()

        # If targeting a specific window, find and focus it
        if self._target_window_title:
            windows = self._enumerator.find_windows_by_title(self._target_window_title)
            if windows:
                self._target_hwnd = windows[0].hwnd
                self._focus.activate_window(self._target_hwnd)
                console.print(f"[green]Targeting window: {windows[0].title}[/]")
            else:
                console.print(f"[yellow]Window '{self._target_window_title}' not found, using full desktop[/]")

        # Start screen recording via ffmpeg if requested
        if self._recording_dir:
            Path(self._recording_dir).mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            self._video_path = Path(self._recording_dir) / f"qa_run_{timestamp}.mp4"
            w, h = self._screen_size
            self._ffmpeg_proc = subprocess.Popen(
                [
                    "ffmpeg", "-y",
                    "-f", "gdigrab",
                    "-framerate", "10",
                    "-video_size", f"{w}x{h}",
                    "-i", "desktop",
                    "-vcodec", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-preset", "ultrafast",
                    str(self._video_path),
                ],
                stdin=subprocess.PIPE,   # so we can send 'q' to stop gracefully
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            console.print(f"[bold cyan]Recording to: {self._video_path}[/]")

        console.print("[bold green]Windows desktop automation ready.[/]")

    async def close(self) -> None:
        """Cleanup controllers and finalize screen recording."""
        # Stop ffmpeg gracefully (send 'q' to stdin, wait for file to finalize)
        if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
            try:
                self._ffmpeg_proc.stdin.write(b"q")
                self._ffmpeg_proc.stdin.flush()
                self._ffmpeg_proc.wait(timeout=15)
            except Exception:
                self._ffmpeg_proc.kill()
            console.print(f"[bold green]Recording saved: {self._video_path}[/]")

        if self._mouse:
            self._mouse.cleanup()
        if self._keyboard:
            self._keyboard.cleanup()

    # --- BaseComputer required methods ---

    async def screen_size(self) -> tuple[int, int]:
        return self._screen_size

    async def environment(self) -> ComputerEnvironment:
        # ENVIRONMENT_UNSPECIFIED defaults to browser in ADK, but we're desktop
        # This is the closest match for non-browser environments
        return ComputerEnvironment.ENVIRONMENT_UNSPECIFIED

    async def current_state(self) -> ComputerState:
        """Capture current screen state as PNG."""
        await asyncio.sleep(self._settle_time)

        if self._target_hwnd:
            image = self._capture.capture_window(self._target_hwnd)
        else:
            image = self._capture.capture_full_screen()

        png_bytes = self._capture.image_to_bytes(image, format="PNG")
        self._last_screenshot = png_bytes

        return ComputerState(screenshot=png_bytes, url=None)

    async def open_web_browser(self) -> ComputerState:
        """Open the default web browser."""
        subprocess.Popen(["cmd", "/c", "start", self._search_engine_url])
        await asyncio.sleep(2.0)  # Give browser time to open
        return await self.current_state()

    async def click_at(self, x: int, y: int) -> ComputerState:
        """Click at screen coordinates."""
        self._mouse.click(x, y)
        self._mouse.wait_for_queue_empty()
        return await self.current_state()

    async def hover_at(self, x: int, y: int) -> ComputerState:
        """Move mouse to coordinates without clicking."""
        self._mouse.move(x, y)
        self._mouse.wait_for_queue_empty()
        return await self.current_state()

    async def type_text_at(
        self,
        x: int,
        y: int,
        text: str,
        press_enter: bool = True,
        clear_before_typing: bool = True,
    ) -> ComputerState:
        """Click at position and type text."""
        # Click to focus the input
        self._mouse.click(x, y)
        self._mouse.wait_for_queue_empty()
        await asyncio.sleep(0.2)

        # Clear existing content
        if clear_before_typing:
            self._keyboard.hotkey("Ctrl+A")
            self._keyboard.wait_for_queue_empty()
            self._keyboard.press_key("DELETE")
            self._keyboard.wait_for_queue_empty()
            await asyncio.sleep(0.1)

        # Type the text
        self._keyboard.type_text(text)
        self._keyboard.wait_for_queue_empty()

        # Press Enter if requested
        if press_enter:
            self._keyboard.press_key("RETURN")
            self._keyboard.wait_for_queue_empty()

        return await self.current_state()

    async def scroll_document(
        self, direction: Literal["up", "down", "left", "right"]
    ) -> ComputerState:
        """Scroll the active document/page."""
        if direction == "down":
            self._keyboard.press_key("PAGEDOWN")
        elif direction == "up":
            self._keyboard.press_key("PAGEUP")
        elif direction == "left":
            # Horizontal scroll via mouse wheel
            self._mouse.scroll("left", 5)
        elif direction == "right":
            self._mouse.scroll("right", 5)
        else:
            raise ValueError(f"Unsupported direction: {direction}")

        self._keyboard.wait_for_queue_empty()
        self._mouse.wait_for_queue_empty()
        return await self.current_state()

    async def scroll_at(
        self,
        x: int,
        y: int,
        direction: Literal["up", "down", "left", "right"],
        magnitude: int,
    ) -> ComputerState:
        """Scroll at a specific position."""
        # Move to position first
        self._mouse.move(x, y)
        self._mouse.wait_for_queue_empty()
        await asyncio.sleep(0.1)

        # Convert magnitude to scroll clicks (3 is roughly one "tick")
        clicks = max(1, magnitude // 30)
        self._mouse.scroll(direction, clicks)
        self._mouse.wait_for_queue_empty()
        return await self.current_state()

    async def wait(self, seconds: int) -> ComputerState:
        """Wait for the specified number of seconds."""
        await asyncio.sleep(seconds)
        return await self.current_state()

    async def go_back(self) -> ComputerState:
        """Navigate back (Alt+Left in most applications)."""
        self._keyboard.hotkey("Alt+LEFT")
        self._keyboard.wait_for_queue_empty()
        await asyncio.sleep(0.5)
        return await self.current_state()

    async def go_forward(self) -> ComputerState:
        """Navigate forward (Alt+Right in most applications)."""
        self._keyboard.hotkey("Alt+RIGHT")
        self._keyboard.wait_for_queue_empty()
        await asyncio.sleep(0.5)
        return await self.current_state()

    async def search(self) -> ComputerState:
        """Open default browser to search engine."""
        return await self.navigate(self._search_engine_url)

    async def navigate(self, url: str) -> ComputerState:
        """Open a URL in the default browser."""
        subprocess.Popen(["cmd", "/c", "start", url])
        await asyncio.sleep(2.0)  # Give browser time to load
        return await self.current_state()

    async def key_combination(self, keys: list[str]) -> ComputerState:
        """Press a key combination (e.g., ['Control', 'C'])."""
        # Normalize key names
        normalized = []
        for key in keys:
            mapped = KEY_NAME_MAP.get(key.lower(), key.upper())
            normalized.append(mapped)

        # Build hotkey string for KeyboardController (e.g., "Ctrl+C")
        hotkey_str = "+".join(normalized)
        self._keyboard.hotkey(hotkey_str)
        self._keyboard.wait_for_queue_empty()
        return await self.current_state()

    async def drag_and_drop(
        self, x: int, y: int, destination_x: int, destination_y: int
    ) -> ComputerState:
        """Drag from (x,y) to (destination_x, destination_y)."""
        self._mouse.drag(x, y, destination_x, destination_y)
        self._mouse.wait_for_queue_empty()
        return await self.current_state()

    # --- Windows-specific extensions ---

    @property
    def video_path(self) -> Optional[Path]:
        """Path to the screen recording MP4, available after close()."""
        return self._video_path

    def get_last_screenshot(self) -> Optional[bytes]:
        """Return the last captured screenshot without triggering a new capture."""
        return self._last_screenshot

    async def focus_target_window(self) -> bool:
        """Re-focus the target window if one was specified."""
        if self._target_hwnd:
            result = self._focus.activate_window(self._target_hwnd)
            return result.name == "SUCCESS"
        return False

    async def list_windows(self) -> list[dict]:
        """List all visible windows on the desktop."""
        windows = self._enumerator.enumerate_windows(include_minimized=False)
        return [
            {
                "hwnd": w.hwnd,
                "title": w.title,
                "process": w.process_name,
                "rect": w.rect,
            }
            for w in windows
        ]

    async def set_target_window(self, title: str) -> bool:
        """Change the target window by title."""
        windows = self._enumerator.find_windows_by_title(title)
        if windows:
            self._target_hwnd = windows[0].hwnd
            self._focus.activate_window(self._target_hwnd)
            return True
        return False

    async def get_ui_tree(self) -> dict:
        """Return UIA element tree for the target window (or full desktop).

        Provides element names, control types, automation IDs, and screen
        coordinates so the agent can find interactive elements precisely.
        Depth limited to 5 levels to keep response concise.
        """
        try:
            import uiautomation as uia  # type: ignore[import]
        except ImportError:
            return {"error": "uiautomation package not installed", "elements": []}

        def _dump(control, depth: int, max_depth: int) -> list:
            if depth > max_depth:
                return []
            try:
                bounds = None
                try:
                    rect = control.BoundingRectangle
                    if rect:
                        bounds = {
                            "x": rect.left,
                            "y": rect.top,
                            "w": rect.width(),
                            "h": rect.height(),
                            "cx": rect.left + rect.width() // 2,
                            "cy": rect.top + rect.height() // 2,
                        }
                except Exception:
                    pass

                node: dict = {
                    "name": control.Name or "",
                    "type": control.ControlTypeName,
                    "id": control.AutomationId or "",
                    "enabled": control.IsEnabled,
                }
                if bounds:
                    node["bounds"] = bounds

                children: list = []
                try:
                    for child in control.GetChildren():
                        children.extend(_dump(child, depth + 1, max_depth))
                except Exception:
                    pass
                if children:
                    node["children"] = children

                return [node]
            except Exception:
                return []

        try:
            if self._target_hwnd:
                root = uia.ControlFromHandle(self._target_hwnd)
                max_depth = 5
            else:
                root = uia.GetRootControl()
                max_depth = 3  # Shallower for full desktop to limit size

            elements = _dump(root, depth=0, max_depth=max_depth)
            return {"elements": elements}
        except Exception as e:
            return {"error": str(e), "elements": []}
