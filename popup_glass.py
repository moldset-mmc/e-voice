"""Windows compositor backdrop shared by the app's popup windows."""
import ctypes
from ctypes import wintypes
import math

import win32api
import win32con as wc
import win32gui as gui


class AccentPolicy(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in ('state', 'flags', 'color', 'animation')]


class CompositionData(ctypes.Structure):
    _fields_ = [('attribute', wintypes.DWORD), ('data', ctypes.c_void_p), ('size', ctypes.c_size_t)]


user32 = ctypes.WinDLL('user32', use_last_error=True)
accent = getattr(user32, 'SetWindowCompositionAttribute', None)
if accent:
    accent.argtypes = [wintypes.HWND, ctypes.POINTER(CompositionData)]
    accent.restype = wintypes.BOOL
set_owner = user32.SetWindowLongPtrW
set_owner.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
set_owner.restype = ctypes.c_ssize_t
NO_REDIRECTION_BITMAP = 0x00200000
get_owner = user32.GetWindow
get_owner.argtypes = [wintypes.HWND, wintypes.UINT]
get_owner.restype = wintypes.HWND
SUBCLASS = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                              wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
controls = ctypes.WinDLL('comctl32', use_last_error=True)
controls.SetWindowSubclass.argtypes = [wintypes.HWND, SUBCLASS, ctypes.c_size_t, ctypes.c_size_t]
controls.SetWindowSubclass.restype = wintypes.BOOL
controls.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
controls.DefSubclassProc.restype = ctypes.c_ssize_t
controls.RemoveWindowSubclass.argtypes = [wintypes.HWND, SUBCLASS, ctypes.c_size_t]
controls.RemoveWindowSubclass.restype = wintypes.BOOL


def native_handle(window):
    return gui.GetAncestor(window.winfo_id(), wc.GA_ROOT)


def _clip(hwnd, rect, radius):
    region = gui.CreateRoundRectRgn(*rect, 2 * radius, 2 * radius)
    try:
        gui.SetWindowRgn(hwnd, region, True)
    except gui.error:
        gui.DeleteObject(region)
        raise


class GlassBackdrop:
    """DWM blurs behind a separate window. The Tk layer owns all text and controls."""

    def __init__(self, window):
        self.window = window
        self.hwnd = self.front = self.owner = None
        self.geometry = self.region = None
        self.acrylic = False
        self.progress = 0.0
        self.visible = False
        self.switches = {}
        self.pressed_switch = None
        window.bind('<Configure>', self._configured, add='+')
        window.bind('<Unmap>', self._unmapped, add='+')
        window.bind('<Destroy>', self._destroyed, add='+')

    def _create(self):
        self.front = native_handle(self.window)
        self.owner = get_owner(self.front, wc.GW_OWNER) or 0
        flags = wc.WS_EX_NOACTIVATE | wc.WS_EX_TOOLWINDOW
        self.hwnd = gui.CreateWindowEx(flags | NO_REDIRECTION_BITMAP, 'STATIC', '', wc.WS_POPUP | wc.SS_NOTIFY,
                                       0, 0, 1, 1, self.owner, 0, win32api.GetModuleHandle(None), None)
        # WCA_ACCENT_POLICY / ACCENT_ENABLE_ACRYLICBLURBEHIND; tint is AABBGGRR.
        policy = AccentPolicy(4, 2, 0x85F3EBDF, 0)
        data = CompositionData(19, ctypes.addressof(policy), ctypes.sizeof(policy))
        self.acrylic = bool(accent and accent(self.hwnd, ctypes.byref(data)))
        if not self.acrylic:
            gui.DestroyWindow(self.hwnd)
            self.hwnd = gui.CreateWindowEx(flags | wc.WS_EX_LAYERED, 'STATIC', '',
                                           wc.WS_POPUP | wc.SS_WHITERECT | wc.SS_NOTIFY,
                                           0, 0, 1, 1, self.owner, 0, win32api.GetModuleHandle(None), None)
            gui.SetLayeredWindowAttributes(self.hwnd, 0, 225, wc.LWA_ALPHA)
        # The compositor keeps the interactive face directly above its material.
        set_owner(self.front, wc.GWL_HWNDPARENT, self.hwnd)
        self._input_callback = SUBCLASS(self._material_input)
        controls.SetWindowSubclass(self.hwnd, self._input_callback, 1, 0)

    def _material_input(self, hwnd, message, wparam, lparam, subclass, data):
        # Windows hit-tests through color-keyed padding. Route clicks there to
        # the corresponding Tk control, including the spaces in switch rows.
        if self.visible and message in (wc.WM_LBUTTONDOWN, wc.WM_LBUTTONUP, wc.WM_MOUSEMOVE):
            try:
                point = gui.ClientToScreen(hwnd, (ctypes.c_short(lparam).value,
                                                  ctypes.c_short(lparam >> 16).value))
                if message in (wc.WM_LBUTTONDOWN, wc.WM_LBUTTONUP):
                    switch = next((widget for handle, widget in self.switches.items()
                                   if gui.PtInRect(gui.GetWindowRect(handle), point)), None)
                    if message == wc.WM_LBUTTONDOWN and switch is not None:
                        self.pressed_switch = switch
                        gui.SetCapture(hwnd)
                        return 0
                    if message == wc.WM_LBUTTONUP and self.pressed_switch is not None:
                        pressed, self.pressed_switch = self.pressed_switch, None
                        if gui.GetCapture() == hwnd:
                            gui.ReleaseCapture()
                        if switch is pressed:
                            self.window.after_idle(pressed.invoke)
                        return 0
                target = self.front
                while True:
                    local = gui.ScreenToClient(target, point)
                    child = gui.ChildWindowFromPointEx(target, local, wc.CWP_SKIPINVISIBLE | wc.CWP_SKIPDISABLED)
                    if not child or child == target:
                        break
                    target = child
                gui.PostMessage(target, message, wparam, win32api.MAKELONG(local[0] & 0xffff, local[1] & 0xffff))
                return 0
            except gui.error:
                pass  # A widget may be going away during dismissal.
        elif message == wc.WM_CAPTURECHANGED:
            self.pressed_switch = None
        return controls.DefSubclassProc(hwnd, message, wparam, lparam)

    def show(self):
        if not self.hwnd:
            self._create()
        self.visible = True
        self.geometry = self.region = None
        self.sync(self.progress)

    def sync(self, progress):
        self.progress = min(1.0, max(0.0, progress))
        if not self.visible or not self.hwnd:
            return
        left, top, right, bottom = gui.GetWindowRect(self.front)
        size = (right - left, bottom - top)
        if min(size) < 2:
            return
        bounds = (left, top, *size)
        width, height = size
        scale = max(1, self.window.winfo_fpixels('1i') / 96)
        radius = max(6, round(15 * scale))
        reveal = 1 - (1 - self.progress) ** 3
        inset = round((1 - reveal) * min(width * .12, 26 * scale))
        clip = (inset, 0, width - inset + 1, max(2, math.ceil(height * reveal)) + 1)
        if self.region != clip:
            _clip(self.hwnd, clip, radius)
            _clip(self.front, clip, radius)
            self.region = clip
        if bounds != self.geometry:
            self.geometry = bounds
            gui.SetWindowPos(self.hwnd, self.front, *bounds, wc.SWP_SHOWWINDOW | wc.SWP_NOACTIVATE)

    def hide(self):
        self.visible = False
        if self.hwnd and gui.IsWindow(self.hwnd):
            gui.ShowWindow(self.hwnd, wc.SW_HIDE)

    def _configured(self, event):
        if event.widget is self.window and self.visible:
            self.sync(self.progress)

    def _unmapped(self, event):
        if event.widget is self.window:
            self.hide()

    def _destroyed(self, event):
        if event.widget is self.window and self.hwnd:
            self.visible = False
            if gui.IsWindow(self.front) and get_owner(self.front, wc.GW_OWNER) == self.hwnd:
                set_owner(self.front, wc.GWL_HWNDPARENT, self.owner if gui.IsWindow(self.owner) else 0)
            if gui.IsWindow(self.hwnd):
                controls.RemoveWindowSubclass(self.hwnd, self._input_callback, 1)
                gui.DestroyWindow(self.hwnd)
            self.hwnd = None
