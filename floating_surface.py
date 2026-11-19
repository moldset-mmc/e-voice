"""A top-down, premultiplied BGRA buffer for a native layered window."""
import ctypes
from ctypes import wintypes
import struct

import win32gui
from PIL import Image

from glass_theme import glass_disc


gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                  ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP


class FloatingSurface:
    def __init__(self):
        self.size = None
        self.dc = self.bitmap = self.previous = None
        self.bits = ctypes.c_void_p()
        self.last_frame = None

    def resize(self, size):
        if size == self.size:
            return
        self.close()
        width, height = size
        header = struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        self.dc = win32gui.CreateCompatibleDC(0)
        self.bitmap = gdi32.CreateDIBSection(self.dc, header, 0, ctypes.byref(self.bits), None, 0)
        if not self.bitmap:
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        self.previous = win32gui.SelectObject(self.dc, self.bitmap)
        self.size = size

    def draw(self, hwnd, metrics, state, hovered, pressed, transition, opacity):
        self.resize(metrics['size'])
        main, menu = metrics['action'], metrics['menu']
        key = (main, menu, state, hovered, pressed, transition)
        if key != self.last_frame:
            frame = Image.new('RGBA', self.size)
            active = hovered == 'action', pressed == 'action'
            disc = glass_disc(main[2], state, *active)
            previous, mix = transition
            if previous:
                disc = Image.blend(glass_disc(main[2], previous, *active), disc, mix)
            frame.paste(disc, main[:2])
            frame.paste(glass_disc(menu[2], 'menu', hovered == 'menu', pressed == 'menu'), menu[:2])
            pixels = frame.convert('RGBa').tobytes('raw', 'BGRa')
            ctypes.memmove(self.bits, pixels, len(pixels))
            self.last_frame = key
        # The position belongs to the drag / summon logic; drawing only updates pixels.
        win32gui.UpdateLayeredWindow(hwnd, 0, None, self.size, self.dc, (0, 0), 0, (0, 0, opacity, 1), 2)

    def close(self):
        if self.bitmap:
            win32gui.SelectObject(self.dc, self.previous)
            win32gui.DeleteObject(self.bitmap)
        if self.dc:
            win32gui.DeleteDC(self.dc)
        self.size = self.dc = self.bitmap = self.previous = self.last_frame = None
