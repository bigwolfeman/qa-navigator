"""Windows screenshot capture via BitBlt/DWM. Ported from WindowsHarness."""

import ctypes
import ctypes.wintypes
import time
import logging
from typing import Optional, Tuple
from io import BytesIO
import numpy as np
from PIL import Image, ImageGrab
import win32gui
import win32ui
import win32con
import win32api
from ctypes import windll, wintypes, Structure, byref, sizeof

from .windows import WindowInfo, get_enumerator

logger = logging.getLogger(__name__)

# Windows API constants
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
DWM_TNP_VISIBLE = 0x8
DWM_TNP_OPACITY = 0x4
DWM_TNP_RECTDESTINATION = 0x1
DWM_TNP_RECTSOURCE = 0x2

# DWM constants
DWM_BB_ENABLE = 0x00000001
DWM_BB_BLURREGION = 0x00000002
DWM_BB_TRANSITIONONMAXIMIZED = 0x00000004

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14


class BITMAPINFOHEADER(Structure):
    """Windows BITMAPINFOHEADER structure."""
    _fields_ = [
        ('biSize', wintypes.DWORD),
        ('biWidth', wintypes.LONG),
        ('biHeight', wintypes.LONG),
        ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD),
        ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD),
        ('biXPelsPerMeter', wintypes.LONG),
        ('biYPelsPerMeter', wintypes.LONG),
        ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD)
    ]


class BITMAPINFO(Structure):
    """Windows BITMAPINFO structure."""
    _fields_ = [
        ('bmiHeader', BITMAPINFOHEADER),
        ('bmiColors', wintypes.DWORD * 3)
    ]


class DWM_THUMBNAIL_PROPERTIES(Structure):
    """Windows DWM thumbnail properties structure."""
    _fields_ = [
        ('dwFlags', wintypes.DWORD),
        ('rcDestination', wintypes.RECT),
        ('rcSource', wintypes.RECT),
        ('opacity', wintypes.BYTE),
        ('fVisible', wintypes.BOOL),
        ('fSourceClientAreaOnly', wintypes.BOOL)
    ]


class ScreenCapture:
    """High-performance screen capture with multiple fallback strategies."""

    def __init__(self):
        self._setup_capture_context()
        self.performance_stats = {
            'total_captures': 0,
            'total_time_ms': 0.0,
            'bitblt_captures': 0,
            'dwm_captures': 0,
            'failed_captures': 0
        }

    def _setup_capture_context(self):
        """Set up reusable capture context for better performance."""
        try:
            # Get desktop DC once for reuse
            self.desktop_dc = win32gui.GetDC(None)
            self.mem_dc = win32ui.CreateDCFromHandle(self.desktop_dc).CreateCompatibleDC()
        except Exception as e:
            logger.error(f"Failed to setup capture context: {e}")
            self.desktop_dc = None
            self.mem_dc = None

    def _cleanup_capture_context(self):
        """Clean up capture context."""
        try:
            if self.mem_dc:
                self.mem_dc.DeleteDC()
            if self.desktop_dc:
                win32gui.ReleaseDC(None, self.desktop_dc)
        except Exception as e:
            logger.debug(f"Error cleaning up capture context: {e}")

    def __del__(self):
        """Cleanup on destruction."""
        self._cleanup_capture_context()

    def _get_window_rect_with_frame(self, hwnd: int) -> Optional[Tuple[int, int, int, int]]:
        """Get window rectangle including DWM frame."""
        try:
            # Try to get extended frame bounds (includes drop shadow, etc.)
            rect = wintypes.RECT()
            result = windll.dwmapi.DwmGetWindowAttribute(
                hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                byref(rect), sizeof(rect)
            )
            if result == 0:
                return (rect.left, rect.top, rect.right, rect.bottom)
        except (AttributeError, OSError):
            pass

        # Fallback to standard window rect
        try:
            return win32gui.GetWindowRect(hwnd)
        except:
            return None

    def _is_window_black_frame(self, image: Image.Image) -> bool:
        """Check if the captured image is mostly black (common with some apps)."""
        try:
            # Convert to numpy array for fast analysis
            img_array = np.array(image)

            # Calculate average brightness
            avg_brightness = np.mean(img_array)

            # Consider it a black frame if average brightness is very low
            return avg_brightness < 10  # Threshold for "mostly black"
        except Exception as e:
            logger.debug(f"Error checking black frame: {e}")
            return False

    def _capture_region_bitblt(self, x: int, y: int, width: int, height: int) -> Optional[Image.Image]:
        """Capture screen region using BitBlt (fastest method)."""
        if not self.desktop_dc or not self.mem_dc:
            return None

        try:
            start_time = time.perf_counter()

            # Create bitmap
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(win32ui.CreateDCFromHandle(self.desktop_dc), width, height)
            self.mem_dc.SelectObject(bitmap)

            # Copy screen region to memory DC
            result = self.mem_dc.BitBlt((0, 0), (width, height),
                              win32ui.CreateDCFromHandle(self.desktop_dc),
                              (x, y), SRCCOPY)

            if not result:
                try:
                    bitmap.DeleteObject()
                except:
                    pass
                return None

            # Get bitmap info
            bmp_info = bitmap.GetInfo()
            bmp_str = bitmap.GetBitmapBits(True)

            # Convert to PIL Image
            image = Image.frombuffer(
                'RGB', (bmp_info['bmWidth'], bmp_info['bmHeight']),
                bmp_str, 'raw', 'BGRX', 0, 1
            )

            # Clean up
            try:
                bitmap.DeleteObject()
            except:
                pass

            # Update stats
            end_time = time.perf_counter()
            capture_time_ms = (end_time - start_time) * 1000
            self.performance_stats['bitblt_captures'] += 1
            self.performance_stats['total_time_ms'] += capture_time_ms

            return image

        except Exception as e:
            return None

    def _capture_region_pil(self, x: int, y: int, width: int, height: int, apply_scaling: bool = True) -> Optional[Image.Image]:
        """Capture screen region using PIL ImageGrab (fallback method)."""
        try:
            start_time = time.perf_counter()

            # Use PIL ImageGrab
            image = ImageGrab.grab(bbox=(x, y, x + width, y + height))

            end_time = time.perf_counter()
            capture_time_ms = (end_time - start_time) * 1000

            return image

        except Exception as e:
            return None

    def _capture_window_dwm_thumbnail(self, hwnd: int,
                                     region: Optional[Tuple[int, int, int, int]] = None) -> Optional[Image.Image]:
        """Capture window using DWM thumbnail API (fallback method)."""
        try:
            start_time = time.perf_counter()

            # Create a temporary window for thumbnail destination
            temp_window = win32gui.CreateWindowEx(
                0, 'STATIC', 'TempCapture', 0,
                0, 0, 1, 1, None, None, None, None
            )

            if not temp_window:
                return None

            try:
                # Register thumbnail
                thumbnail_handle = ctypes.c_void_p()
                result = windll.dwmapi.DwmRegisterThumbnail(
                    temp_window, hwnd, byref(thumbnail_handle)
                )

                if result != 0:
                    return None

                try:
                    # Get source window size
                    source_rect = self._get_window_rect_with_frame(hwnd)
                    if not source_rect:
                        return None

                    src_width = source_rect[2] - source_rect[0]
                    src_height = source_rect[3] - source_rect[1]

                    # Set up thumbnail properties
                    props = DWM_THUMBNAIL_PROPERTIES()
                    props.dwFlags = DWM_TNP_VISIBLE | DWM_TNP_RECTDESTINATION | DWM_TNP_RECTSOURCE
                    props.fVisible = True
                    props.opacity = 255

                    # Set source region
                    if region:
                        rel_x, rel_y, rel_w, rel_h = region
                        props.rcSource.left = rel_x
                        props.rcSource.top = rel_y
                        props.rcSource.right = rel_x + rel_w
                        props.rcSource.bottom = rel_y + rel_h
                        props.dwFlags |= DWM_TNP_RECTSOURCE
                        dest_width, dest_height = rel_w, rel_h
                    else:
                        dest_width, dest_height = src_width, src_height

                    # Set destination
                    props.rcDestination.left = 0
                    props.rcDestination.top = 0
                    props.rcDestination.right = dest_width
                    props.rcDestination.bottom = dest_height

                    # Update thumbnail
                    windll.dwmapi.DwmUpdateThumbnailProperties(thumbnail_handle, byref(props))

                    # Resize temp window
                    win32gui.SetWindowPos(temp_window, 0, 0, 0, dest_width, dest_height, 0)

                    # Give DWM time to render
                    time.sleep(0.01)

                    # Capture the thumbnail using BitBlt on the temp window
                    image = self._capture_region_bitblt(0, 0, dest_width, dest_height)

                    # Update stats
                    end_time = time.perf_counter()
                    capture_time_ms = (end_time - start_time) * 1000
                    self.performance_stats['dwm_captures'] += 1
                    self.performance_stats['total_time_ms'] += capture_time_ms

                    logger.debug(f"DWM thumbnail capture completed in {capture_time_ms:.1f}ms")
                    return image

                finally:
                    # Unregister thumbnail
                    windll.dwmapi.DwmUnregisterThumbnail(thumbnail_handle)
            finally:
                # Clean up temp window
                win32gui.DestroyWindow(temp_window)

        except Exception as e:
            logger.debug(f"DWM thumbnail capture failed: {e}")
            return None

    def capture_region(self, x: int, y: int, width: int, height: int,
                      monitor_scale: float = 1.0) -> Optional[Image.Image]:
        """
        Capture a specific screen region.

        Args:
            x, y: Top-left coordinates
            width, height: Region dimensions
            monitor_scale: DPI scale factor for coordinate adjustment

        Returns:
            PIL Image or None if capture failed
        """
        start_time = time.perf_counter()

        try:
            # Adjust coordinates for DPI scaling
            if monitor_scale != 1.0:
                x = int(x * monitor_scale)
                y = int(y * monitor_scale)
                width = int(width * monitor_scale)
                height = int(height * monitor_scale)

            # Validate region
            if width <= 0 or height <= 0:
                logger.error(f"Invalid capture region: {width}x{height}")
                return None

            # Get screen dimensions to validate bounds
            screen_width = win32api.GetSystemMetrics(0)
            screen_height = win32api.GetSystemMetrics(1)

            # Clamp region to screen bounds
            x = max(0, min(x, screen_width - 1))
            y = max(0, min(y, screen_height - 1))
            width = min(width, screen_width - x)
            height = min(height, screen_height - y)

            # Try BitBlt capture first (fastest)
            image = self._capture_region_bitblt(x, y, width, height)

            if image:
                # Scale back down if needed
                if monitor_scale != 1.0:
                    orig_width = int(width / monitor_scale)
                    orig_height = int(height / monitor_scale)
                    image = image.resize((orig_width, orig_height), Image.LANCZOS)

                # Update performance stats
                end_time = time.perf_counter()
                total_time_ms = (end_time - start_time) * 1000
                self.performance_stats['total_captures'] += 1
                self.performance_stats['total_time_ms'] += total_time_ms

                return image

            # Try PIL ImageGrab as fallback
            # For PIL, don't apply scaling since it works with screen coordinates directly
            image = self._capture_region_pil(x, y, width, height)

            if image:
                # PIL ImageGrab doesn't need scaling adjustment
                # Update performance stats
                end_time = time.perf_counter()
                total_time_ms = (end_time - start_time) * 1000
                self.performance_stats['total_captures'] += 1
                self.performance_stats['total_time_ms'] += total_time_ms

                return image

            logger.warning("All capture methods failed for region")
            self.performance_stats['failed_captures'] += 1
            return None

        except Exception as e:
            logger.error(f"Region capture error: {e}")
            self.performance_stats['failed_captures'] += 1
            return None

    def capture_window(self, hwnd: int,
                      region: Optional[Tuple[int, int, int, int]] = None,
                      include_frame: bool = True) -> Optional[Image.Image]:
        """
        Capture a window or window region.

        Args:
            hwnd: Window handle
            region: Optional (x, y, width, height) region within window
            include_frame: Whether to include window frame/borders

        Returns:
            PIL Image or None if capture failed
        """
        if not win32gui.IsWindow(hwnd):
            logger.error(f"Invalid window handle: {hwnd}")
            return None

        start_time = time.perf_counter()

        try:
            # Get window rectangle
            if include_frame:
                window_rect = self._get_window_rect_with_frame(hwnd)
            else:
                window_rect = win32gui.GetClientRect(hwnd)
                # Convert client rect to screen coordinates
                if window_rect:
                    client_point = win32gui.ClientToScreen(hwnd, (0, 0))
                    window_rect = (
                        client_point[0],
                        client_point[1],
                        client_point[0] + window_rect[2],
                        client_point[1] + window_rect[3]
                    )

            if not window_rect:
                logger.error("Could not get window rectangle")
                return None

            window_x, window_y, window_right, window_bottom = window_rect
            window_width = window_right - window_x
            window_height = window_bottom - window_y

            # Determine capture region
            if region:
                rel_x, rel_y, rel_width, rel_height = region
                capture_x = window_x + rel_x
                capture_y = window_y + rel_y
                capture_width = min(rel_width, window_width - rel_x)
                capture_height = min(rel_height, window_height - rel_y)
            else:
                capture_x = window_x
                capture_y = window_y
                capture_width = window_width
                capture_height = window_height

            # Get window's monitor for DPI scaling
            enumerator = get_enumerator()
            monitor = enumerator.get_monitor_for_window(hwnd)
            scale_factor = monitor.scale_factor if monitor else 1.0

            # Try BitBlt first
            image = self.capture_region(capture_x, capture_y, capture_width, capture_height,
                                       scale_factor)

            if image and not self._is_window_black_frame(image):
                # Update performance stats
                end_time = time.perf_counter()
                total_time_ms = (end_time - start_time) * 1000
                logger.debug(f"Window capture completed in {total_time_ms:.1f}ms")
                return image

            # If BitBlt failed or produced black frame, try DWM thumbnail
            logger.debug("BitBlt failed or produced black frame, trying DWM thumbnail")
            dwm_image = self._capture_window_dwm_thumbnail(hwnd, region)

            if dwm_image:
                end_time = time.perf_counter()
                total_time_ms = (end_time - start_time) * 1000
                logger.debug(f"Window capture (DWM) completed in {total_time_ms:.1f}ms")
                return dwm_image

            logger.warning(f"All capture methods failed for window {hwnd}")
            self.performance_stats['failed_captures'] += 1
            return None

        except Exception as e:
            logger.error(f"Window capture error: {e}")
            self.performance_stats['failed_captures'] += 1
            return None

    def capture_window_info(self, window_info: WindowInfo,
                           region: Optional[Tuple[int, int, int, int]] = None) -> Optional[Image.Image]:
        """Capture using WindowInfo object."""
        return self.capture_window(window_info.hwnd, region)

    def capture_full_screen(self, monitor_index: int = 0) -> Optional[Image.Image]:
        """
        Capture full screen of specified monitor.

        Args:
            monitor_index: Monitor index (0 = primary)

        Returns:
            PIL Image or None if capture failed
        """
        try:
            enumerator = get_enumerator()
            monitors = enumerator.get_monitors()

            if monitor_index >= len(monitors):
                logger.error(f"Monitor index {monitor_index} out of range")
                return None

            monitor = monitors[monitor_index]
            rect = monitor.rect

            return self.capture_region(rect[0], rect[1],
                                     rect[2] - rect[0], rect[3] - rect[1],
                                     monitor.scale_factor)
        except Exception as e:
            logger.error(f"Full screen capture error: {e}")
            return None

    def save_image(self, image: Image.Image, filepath: str, quality: int = 95) -> bool:
        """
        Save captured image to file.

        Args:
            image: PIL Image to save
            filepath: Output file path
            quality: JPEG quality (1-100)

        Returns:
            True if saved successfully
        """
        try:
            if filepath.lower().endswith(('.jpg', '.jpeg')):
                image.save(filepath, 'JPEG', quality=quality, optimize=True)
            elif filepath.lower().endswith('.png'):
                image.save(filepath, 'PNG', optimize=True)
            else:
                image.save(filepath)

            logger.debug(f"Image saved to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to save image: {e}")
            return False

    def image_to_bytes(self, image: Image.Image, format: str = 'PNG') -> Optional[bytes]:
        """
        Convert PIL Image to bytes.

        Args:
            image: PIL Image
            format: Output format ('PNG', 'JPEG')

        Returns:
            Image bytes or None if conversion failed
        """
        try:
            buffer = BytesIO()
            if format.upper() == 'JPEG':
                image.save(buffer, format='JPEG', quality=95, optimize=True)
            else:
                image.save(buffer, format='PNG', optimize=True)

            return buffer.getvalue()
        except Exception as e:
            logger.error(f"Failed to convert image to bytes: {e}")
            return None

    def get_performance_stats(self) -> dict:
        """Get capture performance statistics."""
        stats = self.performance_stats.copy()
        if stats['total_captures'] > 0:
            stats['average_time_ms'] = stats['total_time_ms'] / stats['total_captures']
        else:
            stats['average_time_ms'] = 0.0

        return stats

    def reset_performance_stats(self):
        """Reset performance statistics."""
        self.performance_stats = {
            'total_captures': 0,
            'total_time_ms': 0.0,
            'bitblt_captures': 0,
            'dwm_captures': 0,
            'failed_captures': 0
        }


# Module-level instance
_screen_capture = None


def get_screen_capture() -> ScreenCapture:
    """Get shared ScreenCapture instance."""
    global _screen_capture
    if _screen_capture is None:
        _screen_capture = ScreenCapture()
    return _screen_capture


# Convenience functions
def capture_region(x: int, y: int, width: int, height: int) -> Optional[Image.Image]:
    """Capture a screen region."""
    return get_screen_capture().capture_region(x, y, width, height)


def capture_window(hwnd: int, region: Optional[Tuple[int, int, int, int]] = None) -> Optional[Image.Image]:
    """Capture a window."""
    return get_screen_capture().capture_window(hwnd, region)


def capture_window_by_title(title: str, exact_match: bool = False,
                           region: Optional[Tuple[int, int, int, int]] = None) -> Optional[Image.Image]:
    """Capture a window by title."""
    enumerator = get_enumerator()
    matches = enumerator.find_windows_by_title(title, exact_match)

    if not matches:
        return None

    return get_screen_capture().capture_window(matches[0].hwnd, region)


def capture_full_screen(monitor_index: int = 0) -> Optional[Image.Image]:
    """Capture full screen."""
    return get_screen_capture().capture_full_screen(monitor_index)
