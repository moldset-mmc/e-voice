"""Exercise the real X11 shortcuts and clipboard inside the CI virtual display."""
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unix_input import DesktopInput


def editor(directory):
    root = tk.Tk()
    text = tk.Text(root)
    text.pack()
    root.update()
    text.focus_force()
    (directory / 'window').write_text(str(text.winfo_id()))
    def read_text():
        if (directory / 'copy').exists() and not (directory / 'copied').exists():
            root.clipboard_clear()
            root.clipboard_append('Новая копия из другого окна')
            (directory / 'copied').touch()
        content = text.get('1.0', 'end-1c')
        if content:
            (directory / 'result').write_text(content, encoding='utf-8')
        root.after(25, read_text)
    read_text()
    root.mainloop()


def check():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('This input test runs only in the isolated CI Xvfb desktop.')
    from Xlib import X, XK
    from Xlib.ext import xtest
    root = tk.Tk()
    root.withdraw()
    events = queue.Queue()
    io = DesktopInput(root, events)
    def wait_for(predicate):
        deadline = time.monotonic() + 8
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError('Timed out waiting for X11 test event')
            root.update()
            time.sleep(.015)
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        process = subprocess.Popen([sys.executable, __file__, '--editor', directory])
        try:
            wait_for(lambda: (tmp / 'window').exists())
            window = io.display.create_resource_object('window', int((tmp / 'window').read_text()))
            window.set_input_focus(X.RevertToParent, X.CurrentTime)
            io.display.sync()
            target, own = io.target()
            assert target == window.id and not own
            assert not io.paste('другое окно', target+1)
            io.copy('Исходный буфер')
            assert io.paste('Проверка у курсора. ', target)
            wait_for(lambda: (tmp / 'result').exists())
            assert (tmp / 'result').read_text(encoding='utf-8') == 'Проверка у курсора. '
            assert io._pending_clipboard is not None
            (tmp / 'copy').touch()
            wait_for(lambda: (tmp / 'copied').exists())
            wait_for(lambda: io._pending_clipboard is None)
            assert root.clipboard_get() == 'Новая копия из другого окна'
            io.listen('Ctrl + F9', True)
            io.keys.wait()
            io.mouse.wait()
            for keysym, event in [('Control_L', X.KeyPress), ('F9', X.KeyPress), ('F9', X.KeyRelease), ('Control_L', X.KeyRelease)]:
                key = io.display.keysym_to_keycode(XK.string_to_keysym(keysym))
                xtest.fake_input(io.display, event, key)
            io.display.sync()
            assert events.get(timeout=5)[0] == 'hotkey'
            for _ in range(3):
                for kind in (X.ButtonPress, X.ButtonRelease):
                    xtest.fake_input(io.display, kind, 1)
                io.display.sync()
                time.sleep(.07)
            assert events.get(timeout=5)[0] == 'summon'
        finally:
            io.close()
            process.terminate()
            process.wait(timeout=5)
            root.destroy()


if __name__ == '__main__':
    if '--editor' in sys.argv:
        editor(Path(sys.argv[-1]))
    else:
        check()
