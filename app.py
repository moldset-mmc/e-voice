"""E Voice: explicit microphone toggle, offline Russian ASR, global paste."""
if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    from desktop_entry import dispatch
    if dispatch():
        raise SystemExit(0)

import argparse
import ctypes
import faulthandler
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import ttk

import pystray
import sounddevice as sd
import win32api
import win32con
import win32gui

from speech_engine import Recorder, SpeechEngine
from windows_io import (DEFAULT_HOTKEY, KEY_NAMES, MODIFIER_KEY_CODES, GlobalHotkey, activate_target,
                        choose_target, copy_text, foreground, hotkey_label, insert_text, kernel32,
                        modifiers_down, over_taskbar, parse_hotkey, pressed_modifier_flags)
from floating_control import CLASS_NAME, ICON_PATH, RESTORE_MESSAGE, FloatingControl, fit_to_work_area
from glass_theme import (ACCENT, BORDER, MENU_ALPHA, ON_DARK, SECONDARY, SETTINGS_ALPHA, SURFACE, TEXT,
                         PopupMotion, configure_settings, glass_frame, icon_image)
from mouse_gesture import MouseGesture
from dpi_awareness import enable_dpi_awareness
from choices import ChoicePopup
from recognition_session import RecognitionSession
from preferences import read_settings, write_settings

BASE = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('EVOICE_DATA_DIR') or (Path(os.environ.get('LOCALAPPDATA', BASE)) / 'E Voice'))
_diagnostic_file = None
HOTKEY_HINT = 'Нажми горячую клавишу ещё раз, чтобы остановить запись.'


class VoiceTray(pystray.Icon):
    def _on_notify(self, wparam, lparam):
        if lparam in (win32con.WM_RBUTTONUP, win32con.WM_CONTEXTMENU):
            self.open_glass_menu(win32gui.GetCursorPos())
            return 0
        return super()._on_notify(wparam, lparam)


def enable_diagnostics():
    global _diagnostic_file
    try:
        path = DATA_DIR / 'errors.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 1_000_000:
            path.replace(path.with_suffix('.previous.log'))
        _diagnostic_file = path.open('a', encoding='utf-8', buffering=1)
        faulthandler.enable(file=_diagnostic_file, all_threads=True)
        if sys.stderr is None:
            sys.stderr = _diagnostic_file
    except OSError:
        pass


class Application:
    def __init__(self, root, test_audio=None, test_mode=False):
        self.root = root
        self.test_mode = test_mode
        self.events = queue.Queue()
        self.recorder = Recorder(self.events)
        self.session = RecognitionSession(BASE / 'models' / 'gigaam-v3')
        self.idle_release = None
        self.model_idle_ms = 30_000
        self.state = 'loading'
        self.target = None
        self.take = 0
        self.last_target = None
        self.floating = None
        self.last_text = ''
        self.hotkey = None
        self.capturing_hotkey = False
        self.menu_motion = None
        self.mouse_gesture = None
        self.float_reveal = 0
        self.last_float_raise = 0
        self.ignore_icon_until = 0
        self.tray = None
        self.closing = False
        self.worker = None
        self.test_audio = test_audio
        self.config_path = DATA_DIR / 'settings.json'
        self.config = {} if test_mode else read_settings(self.config_path)
        self.make_window()
        self.install_hotkey(self.key_var.get())
        self.make_tray()
        position = self.config.get('floating_position')
        if not isinstance(position, list) or len(position) != 2 or not all(isinstance(x, int) for x in position):
            position = None
        self.floating = FloatingControl(self.events, position, schedule=root.after)
        if not self.show_float.get():
            self.floating.finish_minimize()
        elif position:
            self.floating.visible(True)
        else:
            self.floating.show_at()
        try:
            self.mouse_gesture = MouseGesture(self.events)
        except Exception as exc:
            self.status.set(str(exc))
        root.after(50, self.poll)
        self.ready('Готов к записи')

    def make_window(self):
        root = self.root
        self.choices = None
        root.title('Настройки · E Voice')
        ui_scale = max(1.0, root.winfo_fpixels('1i') / 96)
        p = lambda value: round(value * ui_scale)
        root.overrideredirect(True)
        root.attributes('-topmost', True)
        root.iconbitmap(str(ICON_PATH))
        root.protocol('WM_DELETE_WINDOW', self.hide)
        root.bind('<Control-q>', lambda event: self.exit())
        root.bind('<Escape>', lambda event: self.hide())
        configure_settings(root, ui_scale)
        self.settings_motion = PopupMotion(root, SETTINGS_ALPHA)

        frame = glass_frame(root, ui_scale, (p(20), p(14)))
        title = ttk.Frame(frame, style='Glass.TFrame')
        title.pack(fill='x')
        title.bind('<ButtonPress-1>', self.drag_settings)
        name = ttk.Label(title, text='E Voice', style='Title.Glass.TLabel', cursor='fleur')
        name.pack(side='left')
        name.bind('<ButtonPress-1>', self.drag_settings)
        ttk.Button(title, text='Закрыть', width=-1, style='Glass.TButton', command=self.hide).pack(side='right')
        heading = ttk.Frame(frame, style='Glass.TFrame')
        heading.pack(fill='x', pady=(p(6), p(8)))
        self.status = tk.StringVar(value='Подготовка модели…')
        self.status_label = ttk.Label(heading, textvariable=self.status, style='State.Glass.TLabel', wraplength=p(231))
        self.status_label.pack(side='left', fill='x', expand=True, anchor='w')
        self.button = ttk.Button(heading, text='Начать запись', command=self.toggle, style='Action.Glass.TButton', state='disabled')
        self.button.pack(side='right', padx=(p(12), 0))
        self.level = ttk.Progressbar(frame, maximum=100, style='Glass.Horizontal.TProgressbar')
        self.level.pack(fill='x')

        ttk.Label(frame, text='Диктовка', style='Section.Glass.TLabel').pack(anchor='w', pady=(p(16), p(6)))
        capture = ttk.Frame(frame, style='Glass.TFrame')
        capture.pack(fill='x')
        capture.columnconfigure(1, weight=1)
        ttk.Label(capture, text='Микрофон', style='Glass.TLabel').grid(row=0, column=0, sticky='w', padx=(0, p(10)))
        self.devices = {'Системный микрофон': None}
        try:
            apis = sd.query_hostapis()
            for i, device in enumerate(sd.query_devices()):
                if device['max_input_channels'] > 0 and apis[device['hostapi']]['name'] in ('MME', 'Windows WASAPI'):
                    self.devices[device['name'] + ' · ' + apis[device['hostapi']]['name']] = i
        except Exception as exc:
            self.status.set('Не удалось получить список микрофонов: ' + str(exc))
        selected = self.config.get('microphone', 'Системный микрофон')
        self.device_var = tk.StringVar(value=selected if selected in self.devices else 'Системный микрофон')
        self.device_box = ttk.Button(capture, textvariable=self.device_var, command=self.pick_device,
                                     width=15, style='Glass.TButton')
        self.device_box.grid(row=0, column=1, columnspan=2, sticky='ew')
        self.device_box.bind('<Alt-Down>', lambda event: self.pick_device())

        try:
            saved_key = hotkey_label(*parse_hotkey(self.config.get('hotkey', DEFAULT_HOTKEY)))
        except (ValueError, AttributeError):
            saved_key = DEFAULT_HOTKEY
        self.key_var = tk.StringVar(value=saved_key)
        self.hotkey_hint = tk.StringVar(value=HOTKEY_HINT)
        ttk.Label(capture, text='Горячая клавиша', style='Glass.TLabel').grid(row=1, column=0, sticky='w', padx=(0, p(10)))
        self.hotkey_button = ttk.Button(capture, text=saved_key, command=self.begin_hotkey_capture, style='Glass.TButton')
        self.hotkey_button.grid(row=1, column=1, sticky='ew', pady=(p(6), 0))
        self.hotkey_button.bind('<KeyPress>', self.capture_hotkey)
        self.hotkey_button.bind('<FocusOut>', self.cancel_hotkey_capture)
        ttk.Button(capture, text='Сбросить', command=self.reset_hotkey, style='Glass.TButton').grid(
            row=1, column=2, sticky='ew', padx=(p(6), 0), pady=(p(6), 0))
        ttk.Label(frame, textvariable=self.hotkey_hint, style='Hint.Glass.TLabel', wraplength=p(400)).pack(anchor='w', pady=(p(5), 0))

        ttk.Label(frame, text='Поведение', style='Section.Glass.TLabel').pack(anchor='w', pady=(p(15), p(3)))
        self.autopaste = tk.BooleanVar(value=self.config.get('autopaste', True))
        self.show_float = tk.BooleanVar(value=self.config.get('show_float', True))
        self.summon_enabled = tk.BooleanVar(value=self.config.get('triple_click', True))
        self.auto_hide_recording = tk.BooleanVar(value=self.config.get('auto_hide_recording', False))
        for label, variable, action in (
            ('Вставлять текст у курсора', self.autopaste, self.save_settings),
            ('Плавающая кнопка', self.show_float, self.toggle_floating),
            ('Вызывать тройным щелчком', self.summon_enabled, self.save_settings),
            ('Скрывать кнопку после начала записи', self.auto_hide_recording, self.save_settings),
        ):
            switch = ttk.Checkbutton(frame, text=label, variable=variable, command=action,
                                     style='Switch.Glass.TCheckbutton')
            switch.pack(fill='x')
            self.settings_motion.glass.switches[switch.winfo_id()] = switch

        result = ttk.Frame(frame, style='Glass.TFrame')
        result.pack(fill='x', pady=(p(10), p(5)))
        ttk.Label(result, text='Последний текст', style='Section.Glass.TLabel').pack(side='left')
        self.copy_button = ttk.Button(result, text='Скопировать', command=self.copy_result, state='disabled', style='Glass.TButton')
        self.copy_button.pack(side='right')
        self.preview = tk.Text(frame, height=2, width=34, wrap='word', font=('Segoe UI', 10),
                               bg=SURFACE, fg=TEXT, selectbackground=SECONDARY, selectforeground=ON_DARK,
                               insertbackground=ACCENT, relief='flat', padx=p(10), pady=p(7),
                               highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                               state='disabled', takefocus=True)
        self.preview.pack(fill='both', expand=True)
        root.update_idletasks()
        height = root.winfo_reqheight()
        root.geometry(f'{p(462)}x{height}')
        root.minsize(p(413), height)
        if self.test_audio:
            self.status.set('Тестовый режим: вместо микрофона используется проверочный WAV.')

    def show_settings(self, destination=None):
        if self.menu_motion:
            self.menu_motion.hide()
        self.settings_motion.show()
        if destination == 'hotkey':
            self.begin_hotkey_capture()
        elif destination == 'result':
            self.preview.focus_set()

    def open_choices(self, title, choices, selected, anchor, below, width):
        if self.choices is not None:
            self.choices.window.destroy()
        self.choices = ChoicePopup(self.root, title, choices, selected, anchor, below, width)

    def pick_device(self):
        def select(name):
            self.device_var.set(name)
            self.save_settings()
            self.device_box.focus_set()
        box = self.device_box
        anchor = box.winfo_rootx(), box.winfo_rooty()
        choices = [(name, lambda name=name: select(name)) for name in self.devices]
        self.open_choices('Микрофон', choices, self.device_var.get(), anchor, anchor[1] + box.winfo_height(), 320)

    def show_tray_menu(self, point):
        choices = [('Показать кнопку', self.reveal_floating), ('Настройки', self.show_settings),
                   ('Остановить запись' if self.state == 'recording' else 'Начать запись', self.toggle),
                   ('Свернуть кнопку', self.minimize_floating), ('Выход', self.exit)]
        self.open_choices('E Voice', choices, None, point, point[1], 200)

    def toggle_quick_menu(self):
        if self.menu_motion and self.menu_motion.open:
            self.menu_motion.hide()
            return
        if not self.floating.is_shown():
            return
        if not self.menu_motion:
            self.make_quick_menu()
        self.menu_motion.show(self.place_quick_menu)
        self.menu_first.focus_set()

    def make_quick_menu(self):
        scale = self.floating.dpi / 96
        p = lambda value: round(value * scale)
        menu = tk.Toplevel(self.root, takefocus=True)
        menu.withdraw()
        menu.title('Меню · E Voice')
        menu.overrideredirect(True)
        menu.attributes('-topmost', True)
        self.menu_motion = PopupMotion(menu, MENU_ALPHA)
        surface = glass_frame(menu, scale, (p(10), p(10)))
        ttk.Label(surface, text='E Voice', style='Section.Glass.TLabel').pack(anchor='w', padx=p(8))
        ttk.Frame(surface, height=p(5), style='Glass.TFrame').pack(fill='x')
        for label, destination in (('Настройки', None), ('Горячая клавиша', 'hotkey'), ('Последний текст', 'result')):
            item = ttk.Button(surface, text=label, style='Menu.Glass.TButton',
                              command=lambda dest=destination: self.show_settings(dest))
            item.pack(fill='x', pady=(0, p(3)))
            if destination is None:
                self.menu_first = item
        ttk.Separator(surface, style='Glass.TSeparator').pack(fill='x', padx=p(8), pady=(p(1), p(4)))
        ttk.Button(surface, text='Свернуть кнопку', style='Menu.Glass.TButton',
                   command=self.minimize_floating).pack(fill='x')
        ttk.Label(surface, text='Микрофон можно перетащить.', style='Hint.Glass.TLabel',
                  wraplength=p(162)).pack(anchor='w', padx=p(4), pady=(p(6), 0))
        menu.bind('<Escape>', lambda event: self.menu_motion.hide())
        menu.bind('<FocusOut>', lambda event: menu.after_idle(self.dismiss_menu_if_blurred))
        menu.update_idletasks()
        menu.geometry(f'{max(p(187), menu.winfo_reqwidth())}x{menu.winfo_reqheight()}')

    def place_quick_menu(self):
        menu = self.menu_motion.window
        menu.update_idletasks()
        hwnd = win32gui.GetAncestor(menu.winfo_id(), win32con.GA_ROOT)
        _, top, _, bottom = win32gui.GetWindowRect(self.floating.hwnd)
        left, _, right, _ = win32gui.GetWindowRect(self.floating.menu_button)
        monitor = win32api.MonitorFromWindow(self.floating.hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        work = win32api.GetMonitorInfo(monitor)['Work']
        scale = self.floating.dpi / 96
        width, height = menu.winfo_width(), menu.winfo_height()
        offset = round(8 * scale)
        y = bottom + offset if bottom + offset + height <= work[3] else top - offset - height
        position = fit_to_work_area(((left + right - width) // 2, y), work, (width, height))
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, *position, 0, 0,
                             win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

    def drag_settings(self, event):
        hwnd = win32gui.GetAncestor(self.root.winfo_id(), win32con.GA_ROOT)
        win32gui.ReleaseCapture()
        win32gui.PostMessage(hwnd, win32con.WM_NCLBUTTONDOWN, win32con.HTCAPTION,
                             win32api.MAKELONG(event.x_root & 0xffff, event.y_root & 0xffff))

    def dismiss_menu_if_blurred(self):
        menu = self.menu_motion.window
        focused = menu.focus_get()
        if focused is None or focused.winfo_toplevel() != menu:
            self.menu_motion.hide()

    def minimize_floating(self):
        if self.menu_motion:
            self.menu_motion.hide()
        self.show_float.set(False)
        self.toggle_floating()

    def save_settings(self):
        if self.test_mode:
            return
        self.config.update(hotkey=self.key_var.get(), microphone=self.device_var.get(), autopaste=self.autopaste.get(),
                           show_float=self.show_float.get(), triple_click=self.summon_enabled.get(),
                           auto_hide_recording=self.auto_hide_recording.get())
        try:
            write_settings(self.config_path, self.config)
        except OSError:
            self.status.set('Настройки работают, но сохранить их на диск не удалось.')

    def save_floating_position(self, position):
        self.config['floating_position'] = list(position)
        self.save_settings()

    def toggle_floating(self):
        if self.floating:
            if self.show_float.get():
                self.float_reveal += 1
            self.floating.visible(self.show_float.get())
        self.save_settings()

    def reveal_floating(self, point=None):
        self.show_float.set(True)
        self.float_reveal += 1
        # Query the cursor now, in the same DPI context as the native window.
        # The hook's queued point can be stale by the time the UI processes it.
        self.floating.show_at()
        self.save_settings()

    def hide_recording_button(self, generation):
        if not self.closing and self.state == 'recording' and self.auto_hide_recording.get() and self.float_reveal == generation:
            self.floating.visible(False)

    def install_hotkey(self, name):
        if self.hotkey and self.hotkey.name == name:
            return True
        try:
            new_key = GlobalHotkey(name, self.events)
        except Exception as exc:
            self.hotkey_hint.set(str(exc))
            return False
        previous, self.hotkey = self.hotkey, new_key
        if previous:
            previous.close()
        self.key_var.set(new_key.name)
        self.hotkey_button.configure(text=new_key.name)
        self.hotkey_hint.set(HOTKEY_HINT)
        return True

    def begin_hotkey_capture(self):
        self.capturing_hotkey = True
        self.hotkey_button.focus_set()
        self.hotkey_button.configure(text='Нажми клавиши…')
        self.hotkey_hint.set('Одна клавиша или сочетание. Esc отменяет.')

    def cancel_hotkey_capture(self, event=None):
        if self.capturing_hotkey:
            self.capturing_hotkey = False
            self.hotkey_button.configure(text=self.key_var.get())
            self.hotkey_hint.set(HOTKEY_HINT)

    def capture_hotkey(self, event):
        if not self.capturing_hotkey:
            return None
        if event.keysym == 'Escape':
            self.cancel_hotkey_capture()
        elif event.keycode in KEY_NAMES:
            name = hotkey_label(pressed_modifier_flags(), event.keycode)
            self.cancel_hotkey_capture()
            if self.install_hotkey(name):
                self.save_settings()
                self.hotkey_hint.set('Горячая клавиша сохранена. Она действует во всех окнах.')
        elif event.keycode not in MODIFIER_KEY_CODES:
            self.hotkey_hint.set('Эту клавишу Windows не поддерживает. Попробуй другую или нажми Esc.')
        return 'break'

    def reset_hotkey(self):
        self.cancel_hotkey_capture()
        if self.install_hotkey(DEFAULT_HOTKEY):
            self.save_settings()

    def make_tray(self):
        def action(name):
            return lambda *args: self.events.put((name, None))
        self.tray = VoiceTray('EVoice', icon_image(), 'E Voice — подготовка', menu=pystray.Menu(
            pystray.MenuItem('Показать или свернуть', action('icon_float'), default=True, visible=False)))
        self.tray.open_glass_menu = lambda point: self.events.put(('tray_menu', point))
        threading.Thread(target=self.tray.run, daemon=True).start()

    def release_idle_model(self):
        self.idle_release = None
        if self.state == 'ready':
            self.session.close()

    def poll(self):
        if self.closing:
            return
        self.last_target = choose_target(foreground(), self.last_target, os.getpid())
        for _ in range(100):
            try:
                name, value = self.events.get_nowait()
            except queue.Empty:
                break
            if name == 'toggle':
                self.toggle()
            elif name == 'hotkey' and value is self.hotkey:
                if self.capturing_hotkey:
                    self.cancel_hotkey_capture()
                else:
                    self.toggle()
            elif name == 'level' and self.state == 'recording':
                self.level['value'] = min(100, value * 700)
            elif name == 'record_limit' and self.state == 'recording':
                self.toggle()
            elif name == 'text':
                self.finish(value)
            elif name == 'error':
                self.ready(value)
            elif name == 'show':
                self.show_settings()
            elif name == 'quick_menu':
                self.toggle_quick_menu()
            elif name == 'tray_menu':
                self.show_tray_menu(value)
            elif name == 'show_float':
                self.reveal_floating()
            elif name == 'summon_float' and self.summon_enabled.get():
                if over_taskbar(value):
                    self.ignore_icon_until = time.monotonic() + .3
                self.reveal_floating(value)
            elif name == 'icon_float' and time.monotonic() >= self.ignore_icon_until:
                show = not self.floating.is_shown() if value is None else value
                if show:
                    self.reveal_floating()
                elif self.floating.is_shown():
                    self.minimize_floating()
            elif name == 'hide_float':
                self.minimize_floating()
            elif name == 'fit_float':
                self.floating.ensure_on_screen()
            elif name == 'float_position':
                self.save_floating_position(value)
            elif name == 'exit':
                self.exit()
                return
        self.floating.refresh_hover()
        popup_open = self.settings_motion.open or (self.menu_motion and self.menu_motion.open) or (self.choices and self.choices.motion.open)
        if not popup_open and time.monotonic() - self.last_float_raise >= 0.25:
            self.floating.keep_on_top()
            self.last_float_raise = time.monotonic()
        self.root.after(50, self.poll)

    def ready(self, message):
        self.state = 'ready'
        if self.idle_release is not None:
            self.root.after_cancel(self.idle_release)
        self.idle_release = self.root.after(self.model_idle_ms, self.release_idle_model) if self.session.process else None
        self.level['value'] = 0
        self.button.configure(text='Начать запись', state='normal', style='Action.Glass.TButton')
        self.device_box.configure(state='normal')
        self.status_label.configure(style='State.Glass.TLabel')
        self.status.set(message)
        if self.floating:
            self.floating.update('ready')
        if self.tray:
            self.tray.icon = icon_image()
            self.tray.title = 'E Voice — готово'

    def toggle(self):
        if self.state == 'ready':
            self.take += 1
            current = foreground()
            self.target = choose_target(current, self.last_target, os.getpid())
            try:
                if not self.test_audio:
                    self.recorder.start(self.devices[self.device_var.get()])
            except Exception as exc:
                self.status.set('Микрофон недоступен: ' + str(exc))
                return
            if self.idle_release is not None:
                self.root.after_cancel(self.idle_release)
                self.idle_release = None
            try:
                self.session.warm()
            except Exception as exc:
                if self.recorder.stream is not None:
                    self.recorder.stop()
                self.ready('Не удалось запустить распознавание: ' + str(exc))
                return
            self.state = 'recording'
            self.button.configure(text='Стоп и вставить', style='Stop.Glass.TButton')
            self.device_box.configure(state='disabled')
            self.status_label.configure(style='Record.Glass.TLabel')
            self.status.set('Идёт запись')
            self.tray.icon = icon_image(True)
            self.tray.title = 'E Voice — идёт запись'
            self.floating.update('recording')
            if self.auto_hide_recording.get():
                generation = self.float_reveal
                self.root.after(900, lambda: self.hide_recording_button(generation))
            if not self.test_mode and current[1] == os.getpid() and self.target:
                activate_target(self.target)
        elif self.state == 'recording':
            # Capture the destination again: the user may have moved the caret.
            self.target = choose_target(foreground(), self.last_target, os.getpid())
            if self.target and not self.test_mode:
                activate_target(self.target)
            try:
                audio = self.recorder.stop() if not self.test_audio else None
            except Exception as exc:
                self.ready('Не удалось завершить запись: ' + str(exc))
                return
            self.state = 'transcribing'
            self.button.configure(text='Распознаю…', state='disabled', style='Action.Glass.TButton')
            self.status_label.configure(style='State.Glass.TLabel')
            self.status.set('Распознаю речь…')
            self.tray.icon = icon_image()
            self.tray.title = 'E Voice — распознавание'
            self.floating.update('transcribing')
            def work():
                try:
                    text = self.session.transcribe(audio, self.test_audio, self.recorder.rate)
                    self.events.put(('text', text))
                except Exception as exc:
                    self.events.put(('error', 'Не удалось распознать речь: ' + str(exc)))
            self.worker = threading.Thread(target=work, daemon=True)
            self.worker.start()

    def finish(self, text):
        text = text.rstrip()
        self.last_text = text
        self.preview.configure(state='normal')
        self.preview.delete('1.0', 'end')
        self.preview.insert('1.0', text)
        self.preview.configure(state='disabled')
        self.copy_button.configure(state='normal' if text else 'disabled')
        warning = ' Были пропуски звука.' if self.recorder.overflow else ''
        self.ready(('Речь не обнаружена.' if not text else 'Текст готов.') + warning)
        if text and self.autopaste.get():
            deadline = time.monotonic() + 5
            target, take = self.target, self.take
            def insert():
                if self.closing or take != self.take:
                    return
                if modifiers_down():
                    if time.monotonic() < deadline:
                        self.root.after(50, insert)
                    else:
                        self.status.set('Текст готов. Отпусти клавиши и нажми «Скопировать текст».')
                    return
                try:
                    inserted = bool(target and foreground() == target and insert_text(text + ' ', target))
                    self.status.set(('Текст вставлен под курсор.' if inserted else 'Текст готов. Поле ввода изменилось или недоступно — можно скопировать результат.') + warning)
                except Exception as exc:
                    self.status.set('Текст готов, но вставка не удалась: ' + str(exc))
            self.root.after(80, insert)

    def copy_result(self):
        if self.last_text:
            try:
                copy_text(self.last_text)
                self.status.set('Текст скопирован.')
            except Exception as exc:
                self.status.set('Не удалось скопировать: ' + str(exc))

    def hide(self):
        self.cancel_hotkey_capture()
        if self.choices:
            self.choices.motion.hide()
        self.settings_motion.hide()

    def exit(self):
        if self.closing:
            return
        self.closing = True
        self.session.close()
        if self.recorder.stream is not None:
            try:
                self.recorder.stop()
            except Exception:
                pass
        if self.hotkey:
            self.hotkey.close()
        if self.mouse_gesture:
            self.mouse_gesture.close()
        if self.tray:
            self.tray.stop()
        if self.floating:
            self.floating.close()
        self.status.set('Завершаю работу…')
        def finish_exit():
            if self.worker is not None and self.worker.is_alive():
                self.root.after(50, finish_exit)
            else:
                self.root.destroy()
        finish_exit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--transcribe', type=Path, help='Offline WAV test; no microphone and no paste')
    parser.add_argument('--test-audio', type=Path, help='Integration test: hotkey uses a WAV instead of microphone')
    parser.add_argument('--smoke-test', type=Path, help='Verify packaged GUI and model, write a JSON result, then exit')
    parser.add_argument('--interaction-test', type=Path, help='Test GUI event loop, dragging and background dictation using --test-audio')
    parser.add_argument('--ui-demo', action='store_true', help='Manual UI test with --test-audio, no insertion or preference writes')
    args = parser.parse_args()
    enable_dpi_awareness()
    if (args.interaction_test or args.ui_demo) and not args.test_audio:
        parser.error('GUI testing requires --test-audio; the microphone is never used in this test')
    if not args.transcribe:
        enable_diagnostics()
    if args.smoke_test:
        root = tk.Tk()
        root.withdraw()
        control = None
        try:
            import win32con
            import win32gui
            before = foreground()
            events = queue.Queue()
            control = FloatingControl(events)
            control.visible(True)
            style = win32gui.GetWindowLong(control.hwnd, win32con.GWL_EXSTYLE)
            no_activate = bool(win32gui.GetWindowLong(control.hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_NOACTIVATE)
            no_mouse_activation = win32gui.SendMessage(control.hwnd, win32con.WM_MOUSEACTIVATE, 0, 0) == win32con.MA_NOACTIVATE
            focus_preserved = foreground() == before
            taskbar_enabled = bool(style & win32con.WS_EX_APPWINDOW) and not bool(style & win32con.WS_EX_TOOLWINDOW)
            control.visible(False)
            minimized_with_taskbar = bool(win32gui.IsIconic(control.hwnd) and win32gui.IsWindowVisible(control.hwnd))
            win32gui.SendMessage(control.hwnd, win32con.WM_SYSCOMMAND, win32con.SC_RESTORE, 0)
            taskbar_restore_requested = ('icon_float', True) in list(events.queue)
            control.visible(True)
            restored = bool(win32gui.IsWindowVisible(control.hwnd) and not win32gui.IsIconic(control.hwnd))
            restore_preserved_focus = foreground() == before
            win32gui.SendMessage(control.hwnd, win32con.WM_CLOSE, 0, 0)
            close_requests_minimize = ('hide_float', None) in list(events.queue) and bool(win32gui.IsWindow(control.hwnd))
            win32gui.SendMessage(control.hwnd, RESTORE_MESSAGE, 0, 0)
            relaunch_restore_requested = ('show_float', None) in list(events.queue)
            control.visible(False)
            engine = SpeechEngine(BASE / 'models' / 'gigaam-v3')
            result = engine.transcribe_file(args.test_audio) if args.test_audio else ''
            checks = {'gui_ready': True, 'settings_hidden': root.state() == 'withdrawn', 'floating_ready': True,
                      'no_activate': no_activate, 'no_mouse_activation': no_mouse_activation,
                      'focus_preserved_on_show': focus_preserved, 'taskbar_enabled': taskbar_enabled,
                      'minimized_with_taskbar': minimized_with_taskbar, 'taskbar_restore_requested': taskbar_restore_requested,
                      'restored': restored, 'restore_preserved_focus': restore_preserved_focus,
                      'close_requests_minimize': close_requests_minimize, 'relaunch_restore_requested': relaunch_restore_requested,
                      'model_ready': True}
            args.smoke_test.write_text(json.dumps({**checks, 'frozen': bool(getattr(sys, 'frozen', False)), 'text': result}, ensure_ascii=False, indent=2), encoding='utf-8')
            if not all(checks.values()):
                raise RuntimeError('Smoke-test failed: ' + ', '.join(key for key, value in checks.items() if not value))
        finally:
            if control:
                control.close()
            root.destroy()
        return
    if args.transcribe:
        result = SpeechEngine(BASE / 'models' / 'gigaam-v3').transcribe_file(args.transcribe)
        print(json.dumps({'text': result}, ensure_ascii=False))
        return
    # UI checks use test audio. They can run beside an existing dictation session.
    mutex_name = f'Local\\EVoiceTest-{os.getpid()}' if args.interaction_test else 'Local\\LocalVoiceRussianOffline'
    mutex = kernel32.CreateMutexW(None, False, mutex_name)
    if not mutex:
        raise RuntimeError('Не удалось создать блокировку запуска')
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(mutex)
        import win32gui
        for _ in range(40):
            existing = win32gui.FindWindow(CLASS_NAME, None)
            if existing:
                win32gui.PostMessage(existing, RESTORE_MESSAGE, 0, 0)
                return
            time.sleep(0.05)
        # The first instance owns startup and shows the orb when its window is ready.
        return
    root = tk.Tk()
    root.withdraw()
    try:
        application = Application(root, args.test_audio, bool(args.ui_demo or args.interaction_test))
        if args.ui_demo:
            application.autopaste.set(False)
            application.save_settings = lambda: None
        root.report_callback_exception = lambda typ, value, tb: traceback.print_exception(typ, value, tb, file=_diagnostic_file or sys.stderr)
        if args.interaction_test:
            from ui_checks import run_checks
            run_checks(root, application, args.interaction_test)
        root.mainloop()
        if args.interaction_test and not json.loads(args.interaction_test.read_text(encoding='utf-8')).get('passed'):
            raise SystemExit(1)
    finally:
        kernel32.CloseHandle(mutex)


if __name__ == '__main__':
    main()
