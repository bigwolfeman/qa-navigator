"""
WineComputer — BaseComputer backend for Wine/XWayland on Linux.

Runs a Windows native application under Wine, captures screenshots via grim
(Wayland), and sends mouse/keyboard input via ydotool. Works on KDE/Sway/
any wlroots-based compositor. The Wine window is moved to (0,0) on the
primary monitor and the screenshot is cropped to that region so ADK gets
clean 1:1 coordinates.
"""

from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from google.adk.tools.computer_use.base_computer import BaseComputer, ComputerState

DISPLAY = os.environ.get("DISPLAY", ":0")
WAYLAND_DISPLAY = os.environ.get("WAYLAND_DISPLAY", "wayland-1")


class WineComputer(BaseComputer):
    """Computer backend that drives a Wine Windows application on Wayland."""

    def __init__(
        self,
        exe_path: str,
        window_title_substr: str,
        screen_size: tuple[int, int] = (1280, 900),
        launch_wait: float = 5.0,
        recording_dir: Optional[str] = None,
    ):
        """
        Args:
            exe_path:            Path to the Windows .exe (or Wine built-in like 'notepad.exe').
            window_title_substr: Substring to match the Wine window title.
            screen_size:         Width × height for the captured region (also used by ADK).
            launch_wait:         Seconds to wait after launching the exe for the window to appear.
            recording_dir:       If set, each screenshot is saved there as a frame sequence.
        """
        self._exe = exe_path
        self._title = window_title_substr
        self._w, self._h = screen_size
        self._launch_wait = launch_wait
        self._recording_dir = recording_dir
        self._recording_path: Optional[str] = None

        self._proc: Optional[subprocess.Popen] = None
        self._win_x = 0
        self._win_y = 0
        self._win_w = self._w
        self._win_h = self._h
        self._frame_count = 0
        self._initialized = False

    # ── BaseComputer interface ────────────────────────────────────────────────

    async def initialize(self) -> None:
        if self._initialized:
            return
        print(f"[WineComputer] Launching {self._exe!r}...")
        env = {**os.environ, "DISPLAY": DISPLAY, "WINEDEBUG": "-all"}
        self._proc = subprocess.Popen(
            ["wine", self._exe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        # Wait for the window to appear
        print(f"[WineComputer] Waiting up to {self._launch_wait}s for window '{self._title}'...")
        deadline = time.time() + self._launch_wait
        while time.time() < deadline:
            await asyncio.sleep(0.5)
            win_info = await asyncio.get_event_loop().run_in_executor(
                None, self._find_window
            )
            if win_info:
                break
        else:
            raise RuntimeError(
                f"[WineComputer] Window matching '{self._title}' not found after {self._launch_wait}s"
            )

        # Track where the window actually landed (KDE/Wayland may ignore move hints)
        print(f"[WineComputer] Found '{win_info['name']}' @ "
              f"{win_info['w']}x{win_info['h']}+{win_info['x']}+{win_info['y']}")
        self._win_x = win_info["x"]
        self._win_y = win_info["y"]
        self._win_w = win_info["w"]
        self._win_h = win_info["h"]
        # Update our canonical screen size to match the actual window
        self._w, self._h = self._win_w, self._win_h
        await asyncio.sleep(0.3)

        if self._recording_dir:
            Path(self._recording_dir).mkdir(parents=True, exist_ok=True)

        self._initialized = True
        print("[WineComputer] Ready.")

    async def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        subprocess.run(["pkill", "wineserver"], capture_output=True)

    async def screenshot(self) -> bytes:
        """Return a JPEG screenshot of the Wine window region."""
        raw_path = f"/tmp/wine_ss_{os.getpid()}.png"
        jpg_path = f"/tmp/wine_ss_{os.getpid()}.jpg"

        region = f"{self._win_x},{self._win_y} {self._win_w}x{self._win_h}"
        result = subprocess.run(
            ["grim", "-g", region, raw_path],
            env={**os.environ, "WAYLAND_DISPLAY": WAYLAND_DISPLAY},
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"grim failed: {result.stderr.decode()}")

        # Convert to JPEG at reasonable quality
        subprocess.run(
            ["convert", raw_path, "-quality", "75", jpg_path],
            capture_output=True,
        )

        with open(jpg_path, "rb") as f:
            data = f.read()

        # Save frame if recording
        if self._recording_dir:
            frame_path = Path(self._recording_dir) / f"frame_{self._frame_count:06d}.png"
            Path(raw_path).rename(str(frame_path))
            self._frame_count += 1
        else:
            for p in (raw_path, jpg_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

        return data

    async def current_state(self) -> ComputerState:
        ss = await self.screenshot()
        return ComputerState(screenshot=ss, url="")

    async def click_at(self, x: int, y: int) -> ComputerState:
        """Click at (x, y) relative to the Wine window's top-left corner."""
        abs_x = self._win_x + x
        abs_y = self._win_y + y
        subprocess.run(
            ["ydotool", "mousemove", "--absolute", f"-x", str(abs_x), f"-y", str(abs_y)],
            capture_output=True,
        )
        await asyncio.sleep(0.1)
        subprocess.run(["ydotool", "click", "0x1"], capture_output=True)  # left click
        await asyncio.sleep(0.3)
        return await self.current_state()

    async def double_click_at(self, x: int, y: int) -> ComputerState:
        abs_x = self._win_x + x
        abs_y = self._win_y + y
        subprocess.run(
            ["ydotool", "mousemove", "--absolute", "-x", str(abs_x), "-y", str(abs_y)],
            capture_output=True,
        )
        await asyncio.sleep(0.1)
        subprocess.run(["ydotool", "click", "0x1"], capture_output=True)
        await asyncio.sleep(0.1)
        subprocess.run(["ydotool", "click", "0x1"], capture_output=True)
        await asyncio.sleep(0.3)
        return await self.current_state()

    async def type_text(self, text: str) -> ComputerState:
        """Type text using wtype."""
        subprocess.run(
            ["wtype", text],
            env={**os.environ, "WAYLAND_DISPLAY": WAYLAND_DISPLAY},
            capture_output=True,
        )
        await asyncio.sleep(0.3)
        return await self.current_state()

    async def key(self, key_combo: str) -> ComputerState:
        """Press a key combination (e.g. 'ctrl+a', 'Return', 'ctrl+s')."""
        # ydotool uses X keysym names
        subprocess.run(
            ["ydotool", "key", key_combo],
            capture_output=True,
        )
        await asyncio.sleep(0.3)
        return await self.current_state()

    # ── Required BaseComputer abstract methods ────────────────────────────────

    def screen_size(self) -> tuple[int, int]:
        return (self._w, self._h)

    @property
    def environment(self) -> str:
        return "desktop"

    async def navigate(self, url: str) -> ComputerState:
        """Not applicable for native apps — returns current state."""
        return await self.current_state()

    async def go_back(self) -> ComputerState:
        return await self.current_state()

    async def go_forward(self) -> ComputerState:
        return await self.current_state()

    async def open_web_browser(self, url: str) -> ComputerState:
        return await self.current_state()

    async def search(self, query: str) -> ComputerState:
        return await self.current_state()

    async def wait(self, duration: float = 1.0) -> ComputerState:
        await asyncio.sleep(duration)
        return await self.current_state()

    async def hover_at(self, x: int, y: int) -> ComputerState:
        abs_x = self._win_x + x
        abs_y = self._win_y + y
        subprocess.run(
            ["ydotool", "mousemove", "--absolute", "-x", str(abs_x), "-y", str(abs_y)],
            capture_output=True,
        )
        await asyncio.sleep(0.2)
        return await self.current_state()

    async def key_combination(self, keys: list[str]) -> ComputerState:
        combo = "+".join(keys)
        subprocess.run(["ydotool", "key", combo], capture_output=True)
        await asyncio.sleep(0.3)
        return await self.current_state()

    async def drag_and_drop(
        self, start_x: int, start_y: int, end_x: int, end_y: int
    ) -> ComputerState:
        abs_sx = self._win_x + start_x
        abs_sy = self._win_y + start_y
        abs_ex = self._win_x + end_x
        abs_ey = self._win_y + end_y
        subprocess.run(
            ["ydotool", "mousemove", "--absolute", "-x", str(abs_sx), "-y", str(abs_sy)],
            capture_output=True,
        )
        await asyncio.sleep(0.1)
        subprocess.run(["ydotool", "mousedown", "0x1"], capture_output=True)
        await asyncio.sleep(0.1)
        subprocess.run(
            ["ydotool", "mousemove", "--absolute", "-x", str(abs_ex), "-y", str(abs_ey)],
            capture_output=True,
        )
        await asyncio.sleep(0.1)
        subprocess.run(["ydotool", "mouseup", "0x1"], capture_output=True)
        await asyncio.sleep(0.3)
        return await self.current_state()

    async def scroll_at(
        self, x: int, y: int, direction: str, amount: int = 3
    ) -> ComputerState:
        return await self.scroll(x, y, direction, amount)

    async def scroll_document(self, direction: str, amount: int = 3) -> ComputerState:
        return await self.scroll(self._w // 2, self._h // 2, direction, amount)

    async def type_text_at(
        self,
        x: int,
        y: int,
        text: str,
        press_enter: bool = False,
        clear_before_typing: bool = False,
    ) -> ComputerState:
        await self.click_at(x, y)
        if clear_before_typing:
            await self.key("ctrl+a")
            await self.key("Delete")
        await self.type_text(text)
        if press_enter:
            await self.key("Return")
        return await self.current_state()

    async def scroll(self, x: int, y: int, direction: str, amount: int = 3) -> ComputerState:
        abs_x = self._win_x + x
        abs_y = self._win_y + y
        subprocess.run(
            ["ydotool", "mousemove", "--absolute", "-x", str(abs_x), "-y", str(abs_y)],
            capture_output=True,
        )
        await asyncio.sleep(0.1)
        btn = "0x4" if direction == "up" else "0x5"  # scroll wheel
        for _ in range(amount):
            subprocess.run(["ydotool", "click", btn], capture_output=True)
            await asyncio.sleep(0.05)
        return await self.current_state()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_window(self) -> Optional[dict]:
        """Scan X11 window tree for a window matching _title."""
        try:
            from Xlib import display as xdisplay
            d = xdisplay.Display(DISPLAY)
            root = d.screen().root

            def search(w):
                try:
                    name = w.get_wm_name()
                    if name and self._title.lower() in name.lower():
                        geom = w.get_geometry()
                        return {"id": w.id, "name": name,
                                "x": geom.x, "y": geom.y,
                                "w": geom.width, "h": geom.height}
                except Exception:
                    pass
                try:
                    for c in w.query_tree().children:
                        r = search(c)
                        if r:
                            return r
                except Exception:
                    pass
                return None

            return search(root)
        except Exception as e:
            print(f"[WineComputer] _find_window error: {e}")
            return None

    def _move_window(self, win_id: int, x: int, y: int, w: int, h: int) -> None:
        """Move & resize an X11 window using Xlib."""
        try:
            from Xlib import display as xdisplay, X
            d = xdisplay.Display(DISPLAY)
            win = d.create_resource_object("window", win_id)
            # Map the window first (in case it's not visible)
            win.map()
            d.sync()
            time.sleep(0.2)
            # Set size hints and move
            win.configure(x=x, y=y, width=w, height=h)
            d.sync()
        except Exception as e:
            print(f"[WineComputer] _move_window error: {e}")
