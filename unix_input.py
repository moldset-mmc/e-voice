"""Accessibility integrations for the user's active desktop session."""
import os
import sys
import time
from tkinter import TclError


def shortcut_expression(label):
    names = {'ctrl': 'ctrl', 'alt': 'alt', 'shift': 'shift', 'cmd': 'cmd',
             'super': 'cmd', 'space': 'space', 'enter': 'enter', 'tab': 'tab',
             'backspace': 'backspace', 'home': 'home', 'end': 'end', 'delete': 'delete',
             'left': 'left', 'right': 'right', 'up': 'up', 'down': 'down'}
    parts = [word.strip().lower() for word in label.split('+')]
    if not parts[-1] or any(x not in ('ctrl', 'alt', 'shift', 'cmd', 'super') for x in parts[:-1]):
        raise ValueError('Нужна клавиша или сочетание.')
    if parts[-1] in ('ctrl', 'alt', 'shift', 'cmd', 'super') or len(set(parts)) != len(parts):
        raise ValueError('Добавьте основную клавишу.')
    def token(name):
        if len(name) == 1:
            return name
        if name in names:
            return '<' + names[name] + '>'
        if name.startswith('f') and name[1:].isdigit() and 1 <= int(name[1:]) <= 20:
            return '<' + name + '>'
        raise ValueError('Эта клавиша не поддерживается.')
    return '+'.join(token(p) for p in parts)


class DesktopInput:
    def __init__(self, root, events):
        self.root, self.events = root, events
        self.wayland = sys.platform == 'linux' and bool(os.getenv('WAYLAND_DISPLAY') or os.getenv('XDG_SESSION_TYPE') == 'wayland')
        self.keys = self.mouse = self.display = None
        self._pending_clipboard = None
        self.own_windows = set()
        self.note = ''
        if self.wayland:
            self.note = 'В Wayland управляйте кнопкой. Результат копируется для вставки через Ctrl+V.'
        elif sys.platform == 'linux':
            from Xlib.display import Display
            self.display = Display()

    def register_window(self, window):
        if self.display:
            window.update_idletasks()
            self.own_windows.update((window.winfo_id(), int(window.frame(), 0)))

    def target(self):
        """Return (focused native destination, belongs to E Voice)."""
        if self.wayland:
            return None, False
        if sys.platform == 'darwin':
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return (app.processIdentifier(), app.processIdentifier() == os.getpid()) if app else (None, False)
        try:
            window = self.display.get_input_focus().focus
            if not hasattr(window, 'id'):
                return None, False
            original = window.id
            while hasattr(window, 'id'):
                if window.id in self.own_windows:
                    return original, True
                window = window.query_tree().parent
            return original, False
        except Exception:
            return None, False

    def restore_own_focus(self, target):
        if not target or not self.target()[1]:
            return
        if sys.platform == 'darwin':
            from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(target)
            if app:
                app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        elif self.display:
            from Xlib import X
            window = self.display.create_resource_object('window', target)
            window.set_input_focus(X.RevertToParent, X.CurrentTime)
            self.display.sync()

    def paste(self, text, target):
        if self.wayland or not target or self.target()[0] != target:
            return False
        if sys.platform == 'darwin':
            from ApplicationServices import AXIsProcessTrusted
            import Quartz as q
            if not AXIsProcessTrusted():
                return False
        self._publish_for_paste(text)
        if sys.platform == 'darwin':
            # Post one paste chord after publishing the UTF-8 result to the macOS pasteboard.
            for down in (True, False):
                event = q.CGEventCreateKeyboardEvent(None, 9, down)
                q.CGEventSetFlags(event, q.kCGEventFlagMaskCommand)
                q.CGEventPostToPid(target, event)
        else:
            from Xlib import X, XK
            from Xlib.ext import xtest
            ctrl, v = [self.display.keysym_to_keycode(XK.string_to_keysym(k)) for k in ('Control_L', 'v')]
            for kind, key in ((X.KeyPress, ctrl), (X.KeyPress, v), (X.KeyRelease, v), (X.KeyRelease, ctrl)):
                xtest.fake_input(self.display, kind, key)
            self.display.sync()
        return True

    def _read_clipboard(self):
        try:
            return self.root.clipboard_get()
        except Exception:
            return None

    def _clipboard_stamp(self):
        try:
            if sys.platform == 'darwin':
                from AppKit import NSPasteboard
                return NSPasteboard.generalPasteboard().changeCount()
            owner = self.display.get_selection_owner(self.display.intern_atom('CLIPBOARD'))
            return getattr(owner, 'id', None)
        except Exception:
            return None

    def _publish_for_paste(self, text):
        saved = self._read_clipboard()
        pending = self._pending_clipboard
        if pending and pending[0] == self._clipboard_stamp() and saved == pending[1]:
            saved = pending[2]  # Two quick dictations share the original clipboard.
        self.copy(text)
        stamp = self._clipboard_stamp()
        if saved is None or stamp is None:
            return
        pending = self._pending_clipboard = (stamp, text, saved)

        def restore():
            if self._pending_clipboard is not pending:
                return
            self._pending_clipboard = None
            if self._clipboard_stamp() == stamp and self._read_clipboard() == text:
                try:
                    self.copy(saved)
                except TclError:
                    pass  # The desktop or Tk window may be closing.

        self.root.after(600, restore)

    def copy(self, text):
        self._pending_clipboard = None
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()

    def request_access(self):
        if sys.platform == 'darwin':
            import Quartz as q
            from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
            if hasattr(q, 'CGRequestListenEventAccess'):
                q.CGRequestListenEventAccess()

    def listen(self, label, triple=True):
        expression = shortcut_expression(label)
        if self.wayland:
            return
        if sys.platform == 'darwin':
            import Quartz as q
            from ApplicationServices import AXIsProcessTrusted
            if not AXIsProcessTrusted() or (hasattr(q, 'CGPreflightListenEventAccess') and not q.CGPreflightListenEventAccess()):
                self.note = 'Для сочетания и автовставки разрешите универсальный доступ и мониторинг ввода в macOS.'
                return
            self.note = ''
        from pynput import keyboard, mouse
        replacement = keyboard.GlobalHotKeys({expression: lambda: self.events.put(('hotkey', None))})
        replacement.start()
        if self.keys:
            self.keys.stop()
        self.keys = replacement
        if self.mouse:
            self.mouse.stop()
            self.mouse = None
        if triple:
            history = []
            def click(x, y, button, pressed, *extra):
                if button != mouse.Button.left:
                    history.clear()
                elif not pressed:
                    now = time.monotonic()
                    if history and (now-history[-1][0] > .55 or max(abs(x-history[0][1]), abs(y-history[0][2])) > 9):
                        history.clear()
                    history.append((now, x, y))
                    if len(history) == 3:
                        self.events.put(('summon', (x, y)))
                        history.clear()
            self.mouse = mouse.Listener(on_click=click)
            self.mouse.start()

    def close(self):
        self._pending_clipboard = None
        for listener in (self.keys, self.mouse):
            if listener:
                listener.stop()
        if self.display:
            self.display.close()
