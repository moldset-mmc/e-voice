"""Exercise native Unicode entry only inside a disposable Windows CI desktop."""
import os
import sys
import time
import unittest


@unittest.skipUnless(sys.platform == 'win32' and os.getenv('GITHUB_ACTIONS') == 'true',
                     'Uses keyboard input in the disposable GitHub Windows VM')
class WindowsCaretTest(unittest.TestCase):
    def test_text_is_inserted_at_the_caret_with_the_clipboard_intact(self):
        import tkinter as tk
        from windows_io import foreground, insert_text
        root = tk.Tk()
        try:
            root.title('E Voice · редактор проверки')
            field = tk.Entry(root)
            field.pack()
            field.insert(0, 'До конца')
            field.icursor(3)
            root.clipboard_clear()
            root.clipboard_append('Исходный буфер')
            root.lift()
            field.focus_force()
            root.update()
            target = foreground()
            self.assertEqual(target[1], os.getpid())
            self.assertFalse(insert_text('X', (target[0], target[1]+1)))
            self.assertTrue(insert_text('фразы ', target))
            deadline = time.monotonic()+4
            while field.get() == 'До конца' and time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            self.assertEqual(field.get(), 'До фразы конца')
            self.assertEqual(root.clipboard_get(), 'Исходный буфер')
        finally:
            root.destroy()
