"""
Framework detection for UI automation.

Analyzes process modules to determine the most appropriate automation
method (UIA, Win32, CDP) based on loaded libraries and frameworks.

Windows desktop automation layer for QA Navigator.
"""

import os
import psutil
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class FrameworkInfo:
    """Information about detected UI frameworks in a process."""
    name: str
    confidence: float
    indicators: List[str]  # Modules/evidence that led to this detection


def detect_frameworks(pid: int) -> Dict[str, float]:
    """
    Detect UI frameworks in a target process.

    Analyzes loaded modules to determine the best automation approach.
    Returns confidence scores for each framework type.

    Args:
        pid: Target process ID

    Returns:
        Dictionary mapping framework names to confidence scores (0.0-1.0)
        - "uia": UI Automation confidence
        - "win32": Win32 controls confidence
        - "cdp": Chrome DevTools Protocol confidence
    """
    try:
        process = psutil.Process(pid)

        module_names = set()
        try:
            for mem_map in process.memory_maps():
                if hasattr(mem_map, 'path') and mem_map.path:
                    module_name = os.path.basename(mem_map.path).lower()
                    module_names.add(module_name)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            try:
                exe_path = process.exe()
                if exe_path:
                    module_names.add(os.path.basename(exe_path).lower())
            except:
                pass

        scores = {}

        # UIA detection - WPF, WinUI, modern Windows apps
        uia_indicators = {
            'presentationframework.dll': 0.9,  # WPF
            'presentationcore.dll': 0.9,       # WPF
            'wpfgfx_v0400.dll': 0.9,          # WPF graphics
            'microsoft.ui.xaml.dll': 0.95,     # WinUI 3
            'windows.ui.xaml.dll': 0.9,        # WinUI 2/UWP
            'uiautomationcore.dll': 0.7,       # UIA provider
            'uiautomationtypes.dll': 0.6,      # UIA types
            'windowscodecs.dll': 0.3,          # WIC (weak indicator)
        }

        uia_score = 0.0
        for module, weight in uia_indicators.items():
            if module in module_names:
                uia_score = max(uia_score, weight)

        if not uia_score:
            uia_score = 0.3

        scores['uia'] = min(uia_score, 1.0)

        # Win32 detection - classic controls and legacy apps
        win32_indicators = {
            'comctl32.dll': 0.8,       # Common controls
            'user32.dll': 0.6,         # Basic Windows API
            'gdi32.dll': 0.4,          # GDI drawing
            'shell32.dll': 0.3,        # Shell integration
            'comdlg32.dll': 0.5,       # Common dialogs
            'mfc140.dll': 0.7,         # MFC applications
            'mfc100.dll': 0.7,         # MFC applications
            'mfc90.dll': 0.7,          # MFC applications
            'atl100.dll': 0.6,         # ATL applications
        }

        win32_score = 0.0
        for module, weight in win32_indicators.items():
            if module in module_names:
                win32_score = max(win32_score, weight)

        if not win32_score:
            win32_score = 0.4

        scores['win32'] = min(win32_score, 1.0)

        # CDP detection - Chromium-based apps
        cdp_indicators = {
            'libcef.dll': 0.95,              # CEF framework
            'webview2loader.dll': 0.9,       # WebView2
            'msedgewebview2.exe': 0.9,       # WebView2 process
            'chrome_elf.dll': 0.9,           # Chrome/Chromium
            'chrome.dll': 0.95,              # Chrome
            'blink_core.dll': 0.9,           # Blink engine
            'v8.dll': 0.8,                   # V8 JavaScript
            'electron.exe': 0.95,            # Electron apps
        }

        cdp_score = 0.0
        for module, weight in cdp_indicators.items():
            if module in module_names:
                cdp_score = max(cdp_score, weight)

        scores['cdp'] = min(cdp_score, 1.0)

        return scores

    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return {
            'uia': 0.3,    # Assume basic UIA support
            'win32': 0.4,  # Assume basic Win32 support
            'cdp': 0.0     # No CDP without evidence
        }


def get_framework_details(pid: int) -> List[FrameworkInfo]:
    """
    Get detailed information about detected frameworks.

    Args:
        pid: Target process ID

    Returns:
        List of FrameworkInfo objects with detailed detection results
    """
    try:
        process = psutil.Process(pid)
        module_names = set()

        try:
            for mem_map in process.memory_maps():
                if hasattr(mem_map, 'path') and mem_map.path:
                    module_name = os.path.basename(mem_map.path).lower()
                    module_names.add(module_name)
        except:
            pass

        frameworks = []
        scores = detect_frameworks(pid)

        # UIA framework info
        uia_indicators = []
        if 'presentationframework.dll' in module_names:
            uia_indicators.append('WPF (PresentationFramework)')
        if 'microsoft.ui.xaml.dll' in module_names:
            uia_indicators.append('WinUI 3')
        if 'windows.ui.xaml.dll' in module_names:
            uia_indicators.append('WinUI 2/UWP')
        if 'uiautomationcore.dll' in module_names:
            uia_indicators.append('UIA Core')

        if not uia_indicators:
            uia_indicators.append('Default Windows UIA support')

        frameworks.append(FrameworkInfo(
            name="uia",
            confidence=scores['uia'],
            indicators=uia_indicators
        ))

        # Win32 framework info
        win32_indicators = []
        if 'comctl32.dll' in module_names:
            win32_indicators.append('Common Controls')
        if any(mfc in module_names for mfc in ['mfc140.dll', 'mfc100.dll', 'mfc90.dll']):
            win32_indicators.append('MFC Application')
        if 'atl100.dll' in module_names:
            win32_indicators.append('ATL Application')

        if not win32_indicators:
            win32_indicators.append('Basic Win32 API')

        frameworks.append(FrameworkInfo(
            name="win32",
            confidence=scores['win32'],
            indicators=win32_indicators
        ))

        # CDP framework info
        cdp_indicators = []
        if 'libcef.dll' in module_names:
            cdp_indicators.append('CEF Framework')
        if 'webview2loader.dll' in module_names:
            cdp_indicators.append('WebView2')
        if 'chrome_elf.dll' in module_names:
            cdp_indicators.append('Chromium Engine')
        if 'electron.exe' in module_names:
            cdp_indicators.append('Electron Application')

        if cdp_indicators:
            frameworks.append(FrameworkInfo(
                name="cdp",
                confidence=scores['cdp'],
                indicators=cdp_indicators
            ))

        return frameworks

    except Exception:
        return []


def is_elevated_process(pid: int) -> Optional[bool]:
    """
    Check if a process is running with elevated privileges.

    Args:
        pid: Process ID

    Returns:
        True if elevated, False if not, None if cannot determine
    """
    try:
        process = psutil.Process(pid)
        return None  # TODO: Implement proper elevation check using Windows APIs
    except:
        return None
