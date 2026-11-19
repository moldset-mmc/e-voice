"""Native Windows microphone controls that never activate their window."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import os
import traceback
import time

import win32api
import win32con
import win32gui
from floating_surface import FloatingSurface
from glass_theme import CAPTIONS, GLASS_ALPHA
from windows_io import activate_target, foreground, over_taskbar

CLASS_NAME = 'LocalVoiceFloatingControl'
RESTORE_MESSAGE = win32con.WM_APP + 19
ICON_PATH = Path(__file__).resolve().parent / 'assets' / 'evoice.ico'
MAIN_DIAMETER, MENU_DIAMETER, BUTTON_GAP = 46, 26, 5
WIDTH, HEIGHT = MAIN_DIAMETER + MENU_DIAMETER + BUTTON_GAP, MAIN_DIAMETER
CURSOR_GAP_DIP = 16
FADE_IN_SECONDS = 0.24
FADE_OUT_SECONDS = 0.12
ICON_CHANGE_SECONDS = 0.11
FADE_FRAME_MS = 16
FIRST_VISIBLE_ALPHA = 32
REVEAL_TOLERANCE_DIP = 6
user32 = ctypes.WinDLL('user32', use_last_error=True)
WINDOW_PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
SUBCLASS_PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                  wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
controls = ctypes.WinDLL('comctl32', use_last_error=True)
controls.SetWindowSubclass.argtypes = [wintypes.HWND, SUBCLASS_PROC, ctypes.c_size_t, ctypes.c_size_t]
controls.SetWindowSubclass.restype = wintypes.BOOL
controls.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
controls.DefSubclassProc.restype = ctypes.c_ssize_t


class WNDCLASS(ctypes.Structure):
    _fields_ = [('style', wintypes.UINT), ('lpfnWndProc', WINDOW_PROC),
                ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HICON),
                ('hCursor', wintypes.HANDLE), ('hbrBackground', wintypes.HANDLE),
                ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR)]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
user32.RegisterClassW.restype = wintypes.WORD
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT
user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
user32.GetSystemMetricsForDpi.restype = ctypes.c_int


def fit_to_work_area(position, work, size=(WIDTH, HEIGHT)):
    """Keep the whole control inside one real monitor, including uneven layouts."""
    x, y = position
    left, top, right, bottom = work
    return (max(left, min(int(x), right - size[0])),
            max(top, min(int(y), bottom - size[1])))


def control_metrics(dpi=96, on_left=True):
    scale = dpi / 96
    main = round(MAIN_DIAMETER * scale)
    menu = round(MENU_DIAMETER * scale)
    width = main + round(BUTTON_GAP * scale) + menu
    return {'size': (width, main), 'action': (width - main if on_left else 0, 0, main, main),
            'menu': (0 if on_left else width - menu, (main - menu) // 2, menu, menu)}


def placement_near_cursor(cursor, work, dpi=96):
    cursor_x, cursor_y = cursor
    left_space = cursor_x - work[0]
    right_space = work[2] - cursor_x
    gap = round(CURSOR_GAP_DIP * dpi / 96)
    size = control_metrics(dpi)['size']
    width, _ = size
    on_left = left_space >= width + gap or (right_space < width + gap and left_space >= right_space)
    x = cursor_x - gap - width if on_left else cursor_x + gap
    y = cursor_y - size[1] // 2
    return fit_to_work_area((x, y), work, size), on_left


class FloatingControl:
    def __init__(self, events, position=None, schedule=None):
        self.events = events
        self.schedule = schedule
        self.fade_version = 0
        self.hiding = False
        self.opacity = GLASS_ALPHA
        self.state = 'loading'
        self.icon_generation = 0
        self.icon_transition = (None, 1.0)
        self.metrics = None
        self.hovered = None
        self.on_left = True
        self.press_part = None
        self.dragging = False
        self.surface = FloatingSurface()
        self.dpi = 96
        self.width, self.height = WIDTH, HEIGHT
        self.drag_origin = None
        self.drag_focus = None
        self.instance = win32api.GetModuleHandle(None)
        self.class_name = CLASS_NAME
        self.hwnd = None
        self.point_cursor = win32gui.LoadCursor(0, win32con.IDC_HAND)
        self.move_cursor = win32gui.LoadCursor(0, win32con.IDC_SIZEALL)
        cls = WNDCLASS()
        cls.hInstance = self.instance
        cls.lpszClassName = self.class_name
        # ctypes acquires the GIL when Windows calls us from Tcl's native event loop.
        # Keep the callback alive until after DestroyWindow and UnregisterClass.
        self.native_proc = WINDOW_PROC(self.dispatch)
        cls.lpfnWndProc = self.native_proc
        cls.hCursor = self.point_cursor
        self.icon = win32gui.LoadImage(0, str(ICON_PATH), win32con.IMAGE_ICON, 32, 32, win32con.LR_LOADFROMFILE)
        cls.hIcon = self.icon
        if not user32.RegisterClassW(ctypes.byref(cls)):
            raise ctypes.WinError(ctypes.get_last_error())
        work = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0)))['Work']
        x, y = position or (work[2] - 310, work[3] - 90)
        monitor = win32api.MonitorFromPoint((x, y), win32con.MONITOR_DEFAULTTONEAREST)
        x, y = fit_to_work_area((x, y), win32api.GetMonitorInfo(monitor)['Work'])
        self.hwnd = user32.CreateWindowExW(
            win32con.WS_EX_TOPMOST | win32con.WS_EX_APPWINDOW | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_LAYERED,
            self.class_name, 'E Voice — быстрая кнопка',
            win32con.WS_POPUP | win32con.WS_CLIPCHILDREN,
            x, y, WIDTH, HEIGHT, 0, 0, self.instance, None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.button = win32gui.CreateWindow('BUTTON', 'Подготовка…',
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.BS_OWNERDRAW,
            7, 7, 32, 32, self.hwnd, 1001, self.instance, None)
        self.menu_button = win32gui.CreateWindow('BUTTON', 'Открыть меню',
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.BS_OWNERDRAW,
            0, 0, 26, 26, self.hwnd, 1002, self.instance, None)
        self.child_proc = SUBCLASS_PROC(self.child_dispatch)
        for child in (self.button, self.menu_button):
            if not controls.SetWindowSubclass(child, self.child_proc, 1, 0):
                raise OSError('Не удалось подключить управление плавающей кнопкой')
        self.apply_layout(user32.GetDpiForWindow(self.hwnd) or 96)
        win32gui.EnableWindow(self.button, False)
        self.set_opacity(GLASS_ALPHA)

    def dispatch(self, hwnd, message, wparam, lparam):
        try:
            return self.window_proc(hwnd, message, wparam, lparam)
        except Exception:
            traceback.print_exc()
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def child_dispatch(self, hwnd, message, wparam, lparam, subclass_id, reference):
        try:
            if message == win32con.WM_SETCURSOR:
                win32gui.SetCursor(self.move_cursor if self.dragging else self.point_cursor)
                return 1
            if message == win32con.BM_CLICK:
                if win32gui.IsWindowEnabled(hwnd):
                    self.click('menu' if hwnd == self.menu_button else 'action')
                return 0
            if message in (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.WM_MOUSEMOVE):
                self.pointer_message(hwnd, message, wparam, lparam)
                return 0
            if message in (win32con.WM_CONTEXTMENU, win32con.WM_RBUTTONUP):
                self.events.put(('quick_menu', None))
                return 0
        except Exception:
            traceback.print_exc()
        return controls.DefSubclassProc(hwnd, message, wparam, lparam)

    def window_proc(self, hwnd, message, wparam, lparam):
        if message == win32con.WM_ERASEBKGND:
            return 1
        if message == win32con.WM_PAINT:
            _, paint = win32gui.BeginPaint(hwnd)
            win32gui.EndPaint(hwnd, paint)
            return 0
        if message == win32con.WM_DRAWITEM:
            # The layered surface draws both controls; native BUTTONs provide input and accessibility.
            return 1
        if message == win32con.WM_MOUSEACTIVATE:
            return win32con.MA_NOACTIVATE
        if message in (win32con.WM_CONTEXTMENU, win32con.WM_RBUTTONUP):
            self.events.put(('quick_menu', None))
            return 0
        if message == RESTORE_MESSAGE:
            self.events.put(('show_float', None))
            return 0
        if message == win32con.WM_SYSCOMMAND:
            command = wparam & 0xFFF0
            if command == win32con.SC_MINIMIZE:
                self.events.put(('icon_float', False))
                return 0
            if command == win32con.SC_RESTORE:
                self.events.put(('icon_float', not self.is_shown() or not over_taskbar()))
                return 0
        if message == win32con.WM_ACTIVATE and (wparam & 0xFFFF) != win32con.WA_INACTIVE:
            if self.is_shown() and not self.drag_origin and over_taskbar():
                self.events.put(('icon_float', False))
        if message == win32con.WM_COMMAND and ((wparam >> 16) & 0xFFFF) == win32con.BN_CLICKED:
            control = wparam & 0xFFFF
            if control == 1001:
                self.click('action')
            elif control == 1002:
                self.click('menu')
            return 0
        if message in (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.WM_MOUSEMOVE):
            self.pointer_message(hwnd, message, wparam, lparam)
            return 0
        if message in (win32con.WM_CANCELMODE, win32con.WM_CAPTURECHANGED) and self.drag_origin:
            self.end_drag()
            return 0
        if message == win32con.WM_CLOSE:
            self.events.put(('hide_float', None))
            return 0
        if message == 0x02E0:  # WM_DPICHANGED: our explicit placement owns the position.
            self.events.put(('fit_float', None))
            return 0
        if message in (win32con.WM_DISPLAYCHANGE, win32con.WM_SETTINGCHANGE):
            self.events.put(('fit_float', None))
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def update(self, state):
        previous, self.state = self.state, state
        self.icon_generation += 1
        self.icon_transition = (None, 1.0)
        if previous != state and 'recording' in (previous, state) and self.schedule and self.is_shown():
            self.transition_icon(previous)
        caption = CAPTIONS[state]
        win32gui.SetWindowText(self.button, caption)
        win32gui.SetWindowText(self.hwnd, 'E Voice — ' + caption)
        win32gui.EnableWindow(self.button, state in ('ready', 'recording'))
        self.paint()

    def transition_icon(self, previous):
        generation, started = self.icon_generation, time.monotonic()
        self.icon_transition = (previous, 0.0)

        def frame():
            if not self.hwnd or generation != self.icon_generation:
                return
            progress = min(1.0, (time.monotonic() - started) / ICON_CHANGE_SECONDS)
            self.icon_transition = (previous, round(progress * 10) / 10) if progress < 1.0 else (None, 1.0)
            self.paint()
            if progress < 1.0:
                self.schedule(FADE_FRAME_MS, frame)

        self.schedule(FADE_FRAME_MS, frame)

    def visible(self, enabled):
        self.end_drag()
        if not enabled:
            if self.is_shown() and not self.hiding:
                self.hiding = True
                self.animate_opacity(0, FADE_OUT_SECONDS, self.finish_minimize)
            return
        if self.is_shown():
            if self.hiding:
                self.fade_in()
            self.keep_on_top()
            return
        self.set_opacity(0)
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
        self.ensure_on_screen()
        self.keep_on_top()
        self.fade_in()

    def finish_minimize(self):
        self.hiding = False
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWMINNOACTIVE)
        self.set_opacity(GLASS_ALPHA)

    def apply_layout(self, dpi, on_left=None):
        if on_left is None:
            on_left = self.on_left
        if self.metrics and self.dpi == dpi and self.on_left == on_left:
            return
        self.dpi, self.on_left = dpi, on_left
        self.drag_threshold = (max(2, user32.GetSystemMetricsForDpi(win32con.SM_CXDRAG, dpi)),
                               max(2, user32.GetSystemMetricsForDpi(win32con.SM_CYDRAG, dpi)))
        self.metrics = control_metrics(dpi, on_left)
        self.width, self.height = self.metrics['size']
        win32gui.SetWindowPos(self.hwnd, 0, 0, 0, self.width, self.height,
                             win32con.SWP_NOMOVE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
        # Give Windows the same two hit regions as the artwork, including during fades.
        regions = []
        for part in ('action', 'menu'):
            x, y, w, h = self.metrics[part]
            regions.append(win32gui.CreateRoundRectRgn(x, y, x + w + 1, y + h + 1, w, h))
        shape, satellite = regions
        try:
            win32gui.CombineRgn(shape, shape, satellite, win32con.RGN_OR)
            win32gui.SetWindowRgn(self.hwnd, shape, True)
        except win32gui.error:
            win32gui.DeleteObject(shape)
            raise
        finally:
            win32gui.DeleteObject(satellite)
        win32gui.MoveWindow(self.button, *self.metrics['action'], True)
        win32gui.MoveWindow(self.menu_button, *self.metrics['menu'], True)
        self.paint()

    def part_at(self, point):
        if not self.metrics:
            return None
        px, py = win32gui.ScreenToClient(self.hwnd, point)
        for part in ('action', 'menu'):
            x, y, width, height = self.metrics[part]
            radius = width / 2
            if (px - x - radius) ** 2 + (py - y - height / 2) ** 2 <= radius ** 2:
                return part
        return None

    def pointer_message(self, hwnd, message, wparam, lparam):
        if not self.metrics or (message != win32con.WM_LBUTTONDOWN and not self.drag_origin):
            return
        local = (ctypes.c_short(lparam & 0xffff).value, ctypes.c_short((lparam >> 16) & 0xffff).value)
        point = win32gui.ClientToScreen(hwnd, local)
        if message == win32con.WM_LBUTTONDOWN:
            part = self.part_at(point)
            if part:
                self.begin_drag(point, part)
        elif message == win32con.WM_LBUTTONUP:
            self.drag_to(point)
            click = self.press_part if not self.dragging and self.part_at(point) == self.press_part else None
            self.end_drag()
            self.click(click)
        elif (wparam & win32con.MK_LBUTTON) and (user32.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000):
            self.drag_to(point)
        else:
            self.end_drag()

    def click(self, part):
        if part == 'menu':
            self.events.put(('quick_menu', None))
        elif part == 'action' and self.state in ('ready', 'recording'):
            self.events.put(('toggle', None))

    def paint(self):
        if self.hwnd and self.metrics and not win32gui.IsIconic(self.hwnd):
            self.surface.draw(self.hwnd, self.metrics, self.state, self.hovered, self.press_part,
                              self.icon_transition, self.opacity)

    def refresh_hover(self):
        if not self.is_shown():
            return
        point = win32gui.GetCursorPos()
        hit = win32gui.WindowFromPoint(point)
        hovered = self.part_at(point) if hit in (self.hwnd, self.button, self.menu_button) else None
        if hovered == self.hovered:
            return
        self.hovered = hovered
        self.paint()

    def set_opacity(self, opacity):
        self.opacity = opacity
        self.paint()

    def fade_in(self):
        self.hiding = False
        self.set_opacity(max(FIRST_VISIBLE_ALPHA, self.opacity))
        self.animate_opacity(GLASS_ALPHA, FADE_IN_SECONDS)

    def animate_opacity(self, target, duration, finish=None):
        self.fade_version += 1
        version, started = self.fade_version, time.monotonic()
        if not self.schedule:
            self.set_opacity(target)
            if finish:
                finish()
            return
        initial = self.opacity

        def frame():
            if not self.hwnd or version != self.fade_version:
                return
            fraction = min(1.0, (time.monotonic() - started) / duration)
            eased = fraction * fraction * (3 - 2 * fraction)
            opacity = round(initial + (target - initial) * eased)
            if opacity != self.opacity:
                self.set_opacity(opacity)
            if fraction < 1:
                self.schedule(FADE_FRAME_MS, frame)
            elif finish:
                finish()

        self.schedule(FADE_FRAME_MS, frame)

    def is_shown(self):
        return bool(self.hwnd and win32gui.IsWindowVisible(self.hwnd) and not win32gui.IsIconic(self.hwnd))

    def keep_on_top(self):
        if self.is_shown():
            win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                 win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

    def begin_drag(self, cursor=None, part=None):
        left, top, _, _ = win32gui.GetWindowRect(self.hwnd)
        self.drag_focus = foreground()
        self.drag_origin = (cursor or win32gui.GetCursorPos(), (left, top))
        self.press_part, self.dragging = part, False
        win32gui.SetCapture(self.hwnd)
        self.paint()

    def drag_to(self, cursor):
        if not self.drag_origin:
            return
        (start_x, start_y), (left, top) = self.drag_origin
        dx, dy = cursor[0] - start_x, cursor[1] - start_y
        if not self.dragging:
            if abs(dx) < self.drag_threshold[0] and abs(dy) < self.drag_threshold[1]:
                return
            self.dragging = True
            # Only a confirmed drag borrows focus for capture outside the window.
            activate_target((self.hwnd, os.getpid()))
            win32gui.SetCursor(self.move_cursor)
        self.move_to((left + dx, top + dy), cursor)

    def end_drag(self):
        if not self.drag_origin:
            return
        target, self.drag_focus = self.drag_focus, None
        moved = self.dragging
        self.press_part, self.dragging = None, False
        self.drag_origin = None
        if win32gui.GetCapture() == self.hwnd:
            win32gui.ReleaseCapture()
        if moved:
            left, top, _, _ = win32gui.GetWindowRect(self.hwnd)
            self.events.put(('float_position', (left, top)))
            if target and foreground()[0] == self.hwnd and target[0] != self.hwnd:
                activate_target(target)
        self.paint()

    def move_to(self, position, monitor_point=None):
        monitor = win32api.MonitorFromPoint(monitor_point or position, win32con.MONITOR_DEFAULTTONEAREST)
        x, y = fit_to_work_area(position, win32api.GetMonitorInfo(monitor)['Work'], (self.width, self.height))
        win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, x, y, 0, 0,
                             win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

    def show_at(self, cursor=None):
        self.end_drag()
        cursor = cursor or win32gui.GetCursorPos()
        monitor = win32api.MonitorFromPoint(cursor, win32con.MONITOR_DEFAULTTONEAREST)
        work = win32api.GetMonitorInfo(monitor)['Work']
        same_monitor = monitor == win32api.MonitorFromWindow(self.hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        dpi = user32.GetDpiForWindow(self.hwnd) or 96
        position, on_left = placement_near_cursor(cursor, work, dpi)
        shown = self.is_shown()
        if shown and same_monitor and self.dpi == dpi and self.on_left == on_left:
            left, top, _, _ = win32gui.GetWindowRect(self.hwnd)
            if max(abs(left - position[0]), abs(top - position[1])) <= REVEAL_TOLERANCE_DIP * dpi / 96:
                if self.hiding:
                    self.fade_in()
                self.keep_on_top()
                return

        # The window stays transparent until it reaches its final position.
        self.set_opacity(0)
        if not shown:
            win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
        if not same_monitor:
            self.move_to(cursor, cursor)
            dpi = user32.GetDpiForWindow(self.hwnd) or 96
            position, on_left = placement_near_cursor(cursor, work, dpi)
        self.apply_layout(dpi, on_left)
        self.move_to(position, cursor)
        self.fade_in()

    def ensure_on_screen(self):
        if not self.hwnd or win32gui.IsIconic(self.hwnd):
            return
        left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
        monitor = win32api.MonitorFromRect((left, top, right, bottom), win32con.MONITOR_DEFAULTTONEAREST)
        self.apply_layout(user32.GetDpiForWindow(self.hwnd) or 96)
        x, y = fit_to_work_area((left, top), win32api.GetMonitorInfo(monitor)['Work'], (self.width, self.height))
        if (x, y) != (left, top):
            win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, x, y, 0, 0,
                                 win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

    def close(self):
        self.fade_version += 1
        if self.drag_origin:
            self.end_drag()
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            win32gui.DestroyWindow(self.hwnd)
        self.hwnd = None
        self.surface.close()
        user32.UnregisterClassW(self.class_name, self.instance)
        win32gui.DestroyIcon(self.icon)
