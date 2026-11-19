"""Use one physical-pixel coordinate space for the native overlay and mouse hook."""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
user32.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
user32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
user32.GetThreadDpiAwarenessContext.restype = wintypes.HANDLE
user32.GetAwarenessFromDpiAwarenessContext.argtypes = [wintypes.HANDLE]
user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int


def enable_dpi_awareness():
    # The packaged manifest sets the process default before any library loads.
    # The call also covers source runs; a manifest-set default returns access denied.
    user32.SetProcessDpiAwarenessContext(wintypes.HANDLE(-4))  # PER_MONITOR_AWARE_V2
    user32.SetThreadDpiAwarenessContext(wintypes.HANDLE(-4))


def current_awareness():
    return user32.GetAwarenessFromDpiAwarenessContext(user32.GetThreadDpiAwarenessContext())
