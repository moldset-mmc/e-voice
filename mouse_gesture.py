"""A passive triple-left-click gesture. Never consumes clicks or stores input history."""
import ctypes
from ctypes import wintypes
import os
import threading
import time
import queue

from windows_io import user32, kernel32


class TripleClick:
    def __init__(self, interval=0.5, distance=8):
        self.interval = interval
        self.distance = distance
        self.reset()

    def reset(self):
        self.count = 0
        self.last = None
        self.down = None

    def press(self, point, now):
        self.down = (point, now)

    def release(self, point, now):
        down, self.down = self.down, None
        if down is None or now - down[1] > self.interval or not self.near(point, down[0]):
            self.reset()
            return False
        if self.last and now - self.last[1] <= self.interval and self.near(point, self.last[0]):
            self.count += 1
        else:
            self.count = 1
        self.last = (point, now)
        if self.count == 3:
            self.reset()
            return True
        return False

    def near(self, first, second):
        return max(abs(first[0] - second[0]), abs(first[1] - second[1])) <= self.distance


class MOUSE_EVENT(ctypes.Structure):
    _fields_ = [('pt', wintypes.POINT), ('mouseData', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('extraInfo', ctypes.c_size_t)]


HOOK_PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOK_PROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetDoubleClickTime.restype = wintypes.UINT
user32.GetDpiForSystem.restype = wintypes.UINT


class MouseGesture:
    def __init__(self, events):
        self.events = events
        self.started = threading.Event()
        self.thread_id = None
        self.error = None
        self.hook = None
        self.left_down = False
        self.raw_events = queue.SimpleQueue()
        self.detector = TripleClick(interval=max(0.6, user32.GetDoubleClickTime() / 1000),
                                    distance=max(12, round(12 * user32.GetDpiForSystem() / 96)))
        self.callback = HOOK_PROC(self.on_mouse)
        self.worker = threading.Thread(target=self.process_events, name='mouse-gesture-detection', daemon=True)
        self.worker.start()
        self.thread = threading.Thread(target=self.run, name='mouse-gesture', daemon=True)
        self.thread.start()
        if not self.started.wait(3):
            raise RuntimeError('Не удалось запустить вызов кнопки мышью.')
        if self.error:
            raise RuntimeError(self.error)

    def on_mouse(self, code, message, data):
        # The hook must return immediately. Window lookup and gesture detection
        # happen outside it so a slow application cannot time out this hook.
        try:
            if code >= 0 and message in (0x0200, 0x0201, 0x0202, 0x0204, 0x0207, 0x020A):
                if message in (0x0201, 0x0202):
                    self.left_down = message == 0x0201
                if message != 0x0200 or self.left_down:
                    event = ctypes.cast(data, ctypes.POINTER(MOUSE_EVENT)).contents
                    self.raw_events.put((message, (event.pt.x, event.pt.y), event.time / 1000))
        except Exception:
            pass
        return user32.CallNextHookEx(None, code, message, data)

    def process_events(self):
        while True:
            item = self.raw_events.get()
            if item is None:
                return
            message, point, now = item
            try:
                if message == 0x0200:
                    if self.detector.down and not self.detector.near(point, self.detector.down[0]):
                        self.detector.reset()
                elif message in (0x0204, 0x0207, 0x020A):
                    self.detector.reset()
                else:
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(user32.WindowFromPoint(wintypes.POINT(*point)), ctypes.byref(pid))
                    if pid.value == os.getpid():
                        self.detector.reset()
                    elif message == 0x0201:
                        self.detector.press(point, now)
                    elif self.detector.release(point, now):
                        self.events.put(('summon_float', point))
            except Exception:
                self.detector.reset()

    def run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        message = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        self.hook = user32.SetWindowsHookExW(14, self.callback, None, 0)  # WH_MOUSE_LL
        if not self.hook:
            self.error = 'Windows не разрешила вызов кнопки тройным щелчком.'
            self.started.set()
            return
        self.started.set()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWindowsHookEx(self.hook)
            self.hook = None

    def close(self):
        if self.thread.is_alive() and self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(2)
        self.raw_events.put(None)
        self.worker.join(2)
