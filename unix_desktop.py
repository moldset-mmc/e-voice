"""Compact Tk controls for the native macOS and Linux distributions."""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

from PIL import ImageTk
from desktop_entry import model_directory
from glass_theme import SURFACE, configure_settings, glass_disc
from preferences import read_settings, write_settings
from recognition_session import RecognitionSession
from speech_engine import Recorder
from unix_input import DesktopInput

DEFAULT = 'Ctrl + Alt + Space'
MIC = 'Системный микрофон'


def preference_dir():
    if os.getenv('EVOICE_DATA_DIR'):
        return Path(os.environ['EVOICE_DATA_DIR'])
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/E Voice'
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'e-voice'


def appear(window, opacity=.96):
    window._fade = getattr(window, '_fade', 0) + 1
    sequence = window._fade
    window.attributes('-alpha', .05)
    window.deiconify()
    window.lift()
    def frame(step):
        if window._fade != sequence or window.state() == 'withdrawn':
            return
        window.attributes('-alpha', opacity * (1 - (1 - step / 8) ** 2))
        if step < 8:
            window.after(16, lambda: frame(step + 1))
    frame(1)


class Desktop:
    def __init__(self, root, test_audio=None):
        self.root, self.test_audio = root, test_audio
        self.path = preference_dir() / 'settings.json'
        self.config = {} if test_audio else read_settings(self.path)
        self.events = queue.Queue()
        self.recorder = Recorder(self.events)
        self.engine = RecognitionSession(model_directory())
        self.io = DesktopInput(root, self.events)
        self.state, self.text = 'ready', ''
        self.last_target = self.target = self.idle = self.capture = None
        self.reveals = 0
        self.closing = False
        self.key = tk.StringVar(value=self.config.get('hotkey', DEFAULT))
        self.paste = tk.BooleanVar(value=self.config.get('autopaste', True))
        self.triple = tk.BooleanVar(value=self.config.get('triple_click', True))
        self.auto_hide = tk.BooleanVar(value=self.config.get('auto_hide_recording', False))
        self.visible = tk.BooleanVar(value=self.config.get('show_float', True))
        self.status = tk.StringVar(value='Готов к записи')
        root.title('Настройки · E Voice')
        root.protocol('WM_DELETE_WINDOW', self.dismiss)
        root.bind('<Escape>', lambda _e: self.dismiss())
        configure_settings(root, 1)
        body = ttk.Frame(root, style='Glass.TFrame', padding=16)
        body.pack(fill='both', expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text='E Voice', style='Title.Glass.TLabel').grid(row=0, column=0, sticky='w')
        self.action = ttk.Button(body, text='Начать запись', style='Action.Glass.TButton', command=self.toggle)
        self.action.grid(row=0, column=1, sticky='e')
        ttk.Label(body, textvariable=self.status, wraplength=340, style='State.Glass.TLabel').grid(row=1, columnspan=2, sticky='w', pady=6)
        self.level = ttk.Progressbar(body, maximum=100)
        self.level.grid(row=2, columnspan=2, sticky='ew')
        self.devices = {MIC: None}
        try:
            import sounddevice as sd
            for i, dev in enumerate(sd.query_devices()):
                if dev['max_input_channels']:
                    self.devices[f"{dev['name']} · {i}"] = i
        except (ImportError, OSError, RuntimeError):
            pass
        self.device = tk.StringVar(value=self.config.get('microphone', MIC))
        if self.device.get() not in self.devices:
            self.device.set(MIC)
        ttk.Label(body, text='Микрофон').grid(row=3, column=0, sticky='w', pady=8)
        self.devices_box = ttk.Combobox(body, textvariable=self.device, values=list(self.devices), state='readonly', width=21)
        self.devices_box.grid(row=3, column=1, sticky='ew')
        self.devices_box.bind('<<ComboboxSelected>>', lambda _e: self.save())
        ttk.Label(body, text='Горячая клавиша').grid(row=4, column=0, sticky='w')
        self.key_button = ttk.Button(body, textvariable=self.key, style='Glass.TButton', command=self.capture_key)
        self.key_button.grid(row=4, column=1, sticky='ew')
        self.key_button.bind('<KeyPress>', self.keypress)
        self.key_button.bind('<FocusOut>', lambda _e: self.end_capture())
        for row, (label, var, callback) in enumerate((
            ('Вставлять результат у курсора', self.paste, self.save),
            ('Показывать плавающую кнопку', self.visible, self.show_changed),
            ('Вызывать тройным щелчком', self.triple, self.bind_inputs),
            ('Скрывать кнопку во время записи', self.auto_hide, self.save)), 5):
            ttk.Checkbutton(body, text=label, variable=var, command=callback).grid(row=row, columnspan=2, sticky='w')
        self.preview = tk.Text(body, height=3, width=34, wrap='word', relief='flat', state='disabled')
        self.preview.grid(row=9, columnspan=2, sticky='ew', pady=7)
        self.copy = ttk.Button(body, text='Скопировать', style='Glass.TButton', state='disabled', command=self.copy_result)
        self.copy.grid(row=10, column=1, sticky='e')
        ttk.Button(body, text='Выход', command=self.close).grid(row=10, column=0, sticky='w')
        self.orb = tk.Toplevel(root)
        self.orb.withdraw()
        self.orb.title('E Voice')
        self.orb.overrideredirect(True)
        self.orb.attributes('-topmost', True)
        bg = SURFACE
        if sys.platform == 'darwin':
            self.orb.attributes('-transparent', True)
            bg = 'systemTransparent'
        self.canvas = tk.Canvas(self.orb, width=82, height=46, bg=bg, highlightthickness=0, cursor='hand2')
        self.canvas.pack()
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.menu = tk.Menu(root, tearoff=False)
        for label, action in [('Настройки', self.settings), ('Горячая клавиша', self.open_shortcut),
                              ('Скопировать текст', self.copy_result), ('Скрыть кнопку', self.hide_orb), ('Выход', self.close)]:
            self.menu.add_command(label=label, command=action)
        self.canvas.bind('<Button-3>', lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        self.origin = None
        self.shaped = False
        self.redraw()
        self.io.register_window(root)
        self.io.register_window(self.orb)
        if self.visible.get():
            self.show_orb(self.config.get('floating_position'))
        else:
            root.iconify()
        if not test_audio:
            self.bind_inputs(save=False)
        if self.io.note:
            self.status.set(self.io.note)
        root.after(50, self.poll)

    def redraw(self):
        self.images = [ImageTk.PhotoImage(glass_disc(26, 'menu')), ImageTk.PhotoImage(glass_disc(46, self.state))]
        self.canvas.delete('all')
        for x, image in zip((13, 59), self.images):
            self.canvas.create_image(x, 23, image=image)

    def show_orb(self, position=None):
        x, y = self.root.winfo_pointerxy()
        x, y = position or ((x-98 if x >= 98 else x+16), y-23)
        x = max(0, min(x, self.root.winfo_screenwidth()-82))
        y = max(0, min(y, self.root.winfo_screenheight()-46))
        self.orb.geometry(f'82x46+{x}+{y}')
        if self.orb.state() == 'withdrawn':
            appear(self.orb)
        self.orb.lift()
        self.reveals += 1
        if self.io.display and not self.shaped:
            self.orb.update_idletasks()
            from Xlib import X
            from Xlib.ext import shape
            if self.io.display.has_extension('SHAPE'):
                rects = []
                for cx, radius in ((13, 13), (59, 23)):
                    for row in range(2*radius):
                        dx = max(1, int(math.sqrt(radius**2-(row+.5-radius)**2)))
                        rects.append((cx-dx, 23-radius+row, 2*dx, 1))
                for wid in (int(self.orb.frame(), 0), self.orb.winfo_id()):
                    window = self.io.display.create_resource_object('window', wid)
                    shape.rectangles(window, shape.SO.Set, shape.SK.Bounding, X.Unsorted, 0, 0, rects)
                self.io.display.flush()
                self.shaped = True

    def press(self, event):
        self.origin = (event.x_root, event.y_root, self.orb.winfo_x(), self.orb.winfo_y(), event.x <= 29)
        self.dragged = False

    def drag(self, event):
        if self.origin:
            x, y, left, top, _ = self.origin
            if max(abs(event.x_root-x), abs(event.y_root-y)) > 4:
                self.dragged = True
                self.show_orb((left+event.x_root-x, top+event.y_root-y))

    def release(self, event):
        if self.origin:
            menu, self.origin = self.origin[4], None
            if self.dragged:
                self.config['floating_position'] = [self.orb.winfo_x(), self.orb.winfo_y()]
                self.save()
            elif menu:
                self.menu.tk_popup(event.x_root, event.y_root)
            else:
                self.toggle()

    def settings(self):
        appear(self.root)

    def open_shortcut(self):
        self.settings()
        self.capture_key()

    def dismiss(self):
        if self.orb.state() == 'withdrawn':
            self.root.iconify()
        else:
            self.root.withdraw()

    def hide_orb(self):
        self.visible.set(False)
        self.show_changed()

    def show_changed(self):
        if self.visible.get():
            self.show_orb()
        else:
            self.orb.withdraw()
            if self.root.state() == 'withdrawn':
                self.root.iconify()
        self.save()

    def capture_key(self):
        if self.io.wayland:
            self.status.set(self.io.note)
            return
        self.io.request_access()
        self.capture = self.config.get('hotkey', DEFAULT)
        self.key.set('Нажмите сочетание…')
        self.key_button.focus_set()

    def end_capture(self):
        if self.capture:
            self.key.set(self.capture)
            self.capture = None

    def keypress(self, event):
        if not self.capture:
            return
        if event.keysym == 'Escape':
            self.end_capture()
        elif event.keysym not in ('Shift_L','Shift_R','Control_L','Control_R','Alt_L','Alt_R','Meta_L','Meta_R','Super_L','Super_R','Command','Option'):
            old = self.capture
            self.capture = None
            mods = [('Ctrl', 4), ('Alt', 16 if sys.platform == 'darwin' else 8), ('Shift', 1),
                    ('Cmd' if sys.platform == 'darwin' else 'Super', 8 if sys.platform == 'darwin' else 64)]
            self.key.set(' + '.join([name for name, mask in mods if event.state & mask] + [event.keysym.capitalize()]))
            if not self.bind_inputs():
                self.key.set(old)
        return 'break'

    def bind_inputs(self, save=True):
        try:
            self.io.listen(self.key.get(), self.triple.get())
            if save:
                self.save()
            return True
        except Exception as error:
            self.status.set('Горячая клавиша недоступна. ' + str(error))
            return False

    def save(self):
        if self.test_audio:
            return
        self.config.update(microphone=self.device.get(), hotkey=self.key.get(), show_float=self.visible.get(),
                           auto_hide_recording=self.auto_hide.get(), autopaste=self.paste.get(), triple_click=self.triple.get())
        try:
            write_settings(self.path, self.config)
        except OSError:
            self.status.set('Не удалось сохранить настройки.')

    def toggle(self):
        if self.state == 'transcribing':
            return
        current, own = self.io.target()
        if current and not own:
            self.last_target = current
        self.target = self.last_target
        if self.state == 'ready':
            try:
                if not self.test_audio:
                    self.recorder.start(self.devices[self.device.get()])
                self.engine.warm()
            except Exception as exc:
                if self.recorder.stream:
                    self.recorder.stop()
                self.status.set(str(exc))
                return
            if self.idle:
                self.root.after_cancel(self.idle)
                self.idle = None
            self.state = 'recording'
            self.status.set('Идёт запись')
            self.action.configure(text='Стоп и вставить', style='Stop.Glass.TButton')
            self.devices_box.configure(state='disabled')
            if self.auto_hide.get():
                generation = self.reveals
                def hide():
                    if self.state == 'recording' and self.reveals == generation and self.auto_hide.get():
                        self.orb.withdraw()
                        if self.root.state() == 'withdrawn':
                            self.root.iconify()
                self.root.after(900, hide)
        else:
            try:
                audio = None if self.test_audio else self.recorder.stop()
            except Exception as exc:
                self.finish('', str(exc))
                return
            self.state = 'transcribing'
            self.action.configure(text='Распознаю…', state='disabled')
            self.status.set('Распознаю речь…')
            def recognize():
                try:
                    text = self.engine.transcribe(audio, self.test_audio, self.recorder.rate)
                    self.events.put(('result', text))
                except Exception as exc:
                    self.events.put(('error', str(exc)))
            threading.Thread(target=recognize, daemon=True).start()
        self.redraw()

    def finish(self, text, error=''):
        self.state = 'ready'
        self.action.configure(text='Начать запись', state='normal', style='Action.Glass.TButton')
        self.devices_box.configure(state='readonly')
        self.level['value'] = 0
        self.redraw()
        self.idle = self.root.after(30000, self.release_model)
        if error:
            self.status.set(error)
            return
        self.text = text.strip()
        self.preview.configure(state='normal')
        self.preview.delete('1.0', 'end')
        self.preview.insert('1.0', self.text)
        self.preview.configure(state='disabled')
        self.copy.configure(state='normal' if self.text else 'disabled')
        message = 'Текст готов.' if self.text else 'Речь не обнаружена.'
        if self.text and self.paste.get() and not self.test_audio:
            try:
                if self.io.paste(self.text + ' ', self.target):
                    message = 'Текст вставлен у курсора.'
                elif self.io.wayland:
                    self.io.copy(self.text + ' ')
                    message = 'Текст скопирован. Нажмите Ctrl+V для вставки.'
                else:
                    message = 'Поле ввода недоступно. Текст можно скопировать.'
            except Exception:
                message = 'Вставка недоступна. Текст можно скопировать.'
        self.status.set(message + (' Были пропуски звука.' if self.recorder.overflow else ''))

    def release_model(self):
        self.idle = None
        if self.state == 'ready':
            self.engine.close()

    def copy_result(self):
        if self.text:
            self.io.copy(self.text)
            self.status.set('Текст скопирован.')

    def poll(self):
        if self.closing:
            return
        current, own = self.io.target()
        if current and not own:
            self.last_target = current
        for _ in range(100):
            try:
                action, value = self.events.get_nowait()
            except queue.Empty:
                break
            if action == 'hotkey' and not self.capture:
                self.toggle()
            elif action == 'summon' and self.triple.get():
                self.visible.set(True)
                self.show_orb()
            elif action == 'level' and self.state == 'recording':
                self.level['value'] = min(100, value*700)
            elif action == 'record_limit' and self.state == 'recording':
                self.toggle()
            elif action == 'result':
                self.finish(value)
            elif action == 'error':
                self.finish('', value)
        self.root.after(80, self.poll)

    def close(self):
        self.closing = True
        try:
            if self.recorder.stream:
                self.recorder.stop()
        finally:
            self.engine.close()
            self.io.close()
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--interaction-test', type=Path)
    parser.add_argument('--test-audio', type=Path)
    args = parser.parse_args()
    if args.interaction_test and not args.test_audio:
        parser.error('--interaction-test requires --test-audio')
    directory = preference_dir()
    directory.mkdir(parents=True, exist_ok=True)
    request = directory / 'show.request'
    with (directory / 'instance.lock').open('a+b') as lock:
        if not args.test_audio:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                request.touch()
                return
        root = tk.Tk()
        root.withdraw()
        app = Desktop(root, args.test_audio)
        def reopen():
            if request.exists():
                request.unlink(missing_ok=True)
                app.visible.set(True)
                app.show_changed()
            root.after(500, reopen)
        if not args.test_audio:
            reopen()
        if args.interaction_test:
            from unix_ui_checks import run
            run(app, args.interaction_test)
        root.mainloop()
        if args.interaction_test and not json.loads(args.interaction_test.read_text()).get('passed'):
            raise SystemExit(1)
