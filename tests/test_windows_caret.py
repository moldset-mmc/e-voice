"""Exercise native Unicode entry only inside a disposable Windows CI desktop."""
import os
import sys
import time
import unittest


@unittest.skipUnless(sys.platform == 'win32' and os.getenv('GITHUB_ACTIONS') == 'true',
                     'Uses keyboard input in the disposable GitHub Windows VM')
class WindowsCaretTest(unittest.TestCase):
    def test_text_is_inserted_at_the_caret_with_the_clipboard_intact(self):
        import win32api
        import win32clipboard
        import win32con as wc
        import win32gui as gui
        from windows_io import foreground, insert_text
        instance = win32api.GetModuleHandle(None)
        root = gui.CreateWindowEx(wc.WS_EX_TOPMOST, 'STATIC', 'E Voice QA editor',
                                  wc.WS_OVERLAPPEDWINDOW | wc.WS_VISIBLE, 30, 30, 320, 100, 0, 0, instance, None)
        try:
            field = gui.CreateWindowEx(0, 'EDIT', 'До конца', wc.WS_CHILD | wc.WS_VISIBLE | wc.ES_AUTOHSCROLL,
                                       8, 8, 270, 25, root, 1, instance, None)
            win32clipboard.OpenClipboard(root)
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText('Исходный буфер', wc.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            gui.SetForegroundWindow(root)
            gui.SetFocus(field)
            gui.SendMessage(field, wc.EM_SETSEL, 3, 3)
            gui.PumpWaitingMessages()
            target = foreground()
            self.assertEqual(target, (root, os.getpid()))
            self.assertFalse(insert_text('X', (target[0], target[1]+1)))
            self.assertTrue(insert_text('фразы ', target))
            deadline = time.monotonic()+4
            while gui.GetWindowText(field) != 'До фразы конца' and time.monotonic() < deadline:
                gui.PumpWaitingMessages()
                time.sleep(.01)
            self.assertEqual(gui.GetWindowText(field), 'До фразы конца')
            win32clipboard.OpenClipboard(root)
            try:
                self.assertEqual(win32clipboard.GetClipboardData(wc.CF_UNICODETEXT), 'Исходный буфер')
            finally:
                win32clipboard.CloseClipboard()
        finally:
            gui.DestroyWindow(root)
