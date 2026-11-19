"""Global hotkey and direct Unicode typing; auto-insertion leaves clipboard alone."""
import ctypes
from ctypes import wintypes
import threading

import win32clipboard
import win32con
import win32gui

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

DEFAULT_HOTKEY = 'Ctrl + Alt + Space'
MODIFIERS = (('Ctrl', 0x0002, (0x11,)), ('Alt', 0x0001, (0x12,)),
             ('Shift', 0x0004, (0x10,)), ('Win', 0x0008, (0x5B, 0x5C)))
KEY_NAMES = {
    0x08: 'Backspace', 0x09: 'Tab', 0x0D: 'Enter', 0x13: 'Pause', 0x14: 'Caps Lock',
    0x20: 'Space', 0x21: 'Page Up', 0x22: 'Page Down', 0x23: 'End', 0x24: 'Home',
    0x25: 'Left', 0x26: 'Up', 0x27: 'Right', 0x28: 'Down', 0x2D: 'Insert', 0x2E: 'Delete',
    0x6A: 'Num Multiply', 0x6B: 'Num Plus', 0x6D: 'Num Minus', 0x6E: 'Num Decimal',
    0x6F: 'Num Divide', 0x90: 'Num Lock', 0x91: 'Scroll Lock',
    0xBA: 'Semicolon', 0xBB: 'Plus', 0xBC: 'Comma', 0xBD: 'Minus', 0xBE: 'Period',
    0xBF: 'Slash', 0xC0: 'Grave', 0xDB: 'Left Bracket', 0xDC: 'Backslash',
    0xDD: 'Right Bracket', 0xDE: 'Quote',
    **{ord(char): char for char in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'},
    **{0x70 + i: f'F{i + 1}' for i in range(24)},
    **{0x60 + i: f'Num {i}' for i in range(10)},
}
KEY_CODES = {name: code for code, name in KEY_NAMES.items()}
MODIFIER_KEY_CODES = (0x10, 0x11, 0x12, 0x5B, 0x5C, *range(0xA0, 0xA6))


def hotkey_label(modifiers, key):
    return ' + '.join([name for name, flag, _ in MODIFIERS if modifiers & flag] + [KEY_NAMES[key]])


def parse_hotkey(name):
    parts = [part.strip() for part in name.split('+')]
    flags = {modifier: flag for modifier, flag, _ in MODIFIERS}
    if parts[-1] not in KEY_CODES or len(set(parts[:-1])) != len(parts[:-1]) or any(part not in flags for part in parts[:-1]):
        raise ValueError('Выбери обычную клавишу или сочетание с Ctrl, Alt либо Shift.')
    return sum(flags[part] for part in parts[:-1]), KEY_CODES[parts[-1]]


def pressed_modifier_flags():
    return sum(flag for _, flag, keys in MODIFIERS if any(user32.GetAsyncKeyState(key) & 0x8000 for key in keys))


def over_taskbar(point=None):
    hwnd = win32gui.WindowFromPoint(point or win32gui.GetCursorPos())
    if not hwnd:
        return False
    try:
        ancestor = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
        return win32gui.GetClassName(ancestor or hwnd) in ('Shell_TrayWnd', 'Shell_SecondaryTrayWnd', 'NotifyIconOverflowWindow')
    except win32gui.error:
        return False


def foreground():
    handle = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    return (handle, pid.value)


def choose_target(current, previous, own_pid):
    """Own controls never replace the last external caret destination."""
    if current and current[0] and current[1] and current[1] != own_pid:
        return current
    if previous and previous[0] and previous[1] and previous[1] != own_pid:
        return previous
    return None


def activate_target(target):
    if not target or not user32.IsWindow(target[0]):
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(target[0], ctypes.byref(pid))
    if pid.value != target[1]:
        return False
    if foreground() == target:
        return True
    user32.SetForegroundWindow(target[0])
    return foreground() == target


def modifiers_down():
    return bool(pressed_modifier_flags())


class GlobalHotkey:
    def __init__(self, name, events):
        self.modifiers, self.key = parse_hotkey(name)
        self.name = hotkey_label(self.modifiers, self.key)
        self.events = events
        self.thread_id = None
        self.started = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.started.wait(3):
            raise RuntimeError('Не удалось запустить горячую клавишу')
        if self.error:
            raise RuntimeError(self.error)

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, self.modifiers | 0x4000, self.key):
            self.error = 'Горячая клавиша занята другой программой. Выбери другую.'
            self.started.set()
            return
        self.started.set()
        message = wintypes.MSG()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == 0x0312:
                    self.events.put(('hotkey', self))
        finally:
            user32.UnregisterHotKey(None, 1)

    def close(self):
        if self.thread.is_alive() and self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(2)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]

class INPUTUNION(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', wintypes.DWORD), ('u', INPUTUNION)]

user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def text_key_events(text):
    """UTF-16 keyboard input works independently of the current keyboard layout."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if '\0' in text:
        raise ValueError('Текст содержит недопустимый нулевой символ')
    events = []
    for char in text:
        if char == '\n':
            events.extend(((0x0D, 0, 0), (0x0D, 0, 2)))
        else:
            raw = char.encode('utf-16-le')
            for offset in range(0, len(raw), 2):
                unit = int.from_bytes(raw[offset:offset + 2], 'little')
                events.extend(((0, unit, 4), (0, unit, 4 | 2)))
    return events


def copy_text(text):
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def insert_text(text, target):
    """Type into the current caret without reading or replacing the clipboard."""
    if not target or foreground() != target or not user32.IsWindow(target[0]):
        return False
    events = text_key_events(text)
    for offset in range(0, len(events), 256):
        if foreground() != target:
            raise RuntimeError('Окно изменилось во время ввода. Полный текст сохранён в окне программы.')
        batch = events[offset:offset + 256]
        inputs = (INPUT * len(batch))()
        for item, (key, scan, flags) in zip(inputs, batch):
            item.type = 1
            item.ki = KEYBDINPUT(key, scan, flags, 0, 0)
        if user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT)) != len(inputs):
            raise RuntimeError('Windows не разрешила ввод. Текст доступен для копирования.')
    return True
