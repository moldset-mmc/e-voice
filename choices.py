"""Keyboard- and mouse-accessible glass lists for devices and tray actions."""
import tkinter as tk
from tkinter import ttk
import win32api
import win32con
import win32gui

from glass_theme import MENU_ALPHA, SURFACE, PopupMotion, glass_frame
from popup_glass import native_handle


class ChoicePopup:
    def __init__(self, parent, title, choices, selected, anchor, below, width_dip):
        window = self.window = tk.Toplevel(parent, takefocus=True)
        window.withdraw()
        window.title(title + ' · E Voice')
        window.overrideredirect(True)
        window.attributes('-topmost', True)
        scale = max(1.0, parent.winfo_fpixels('1i') / 96)
        p = lambda value: round(value * scale)
        self.motion = PopupMotion(window, MENU_ALPHA)
        body = glass_frame(window, scale, (p(10), p(9)))
        ttk.Label(body, text=title, style='Section.Glass.TLabel').pack(anchor='w', pady=(0, p(5)))
        view = tk.Canvas(body, background=SURFACE, borderwidth=0, highlightthickness=0,
                         width=1, height=1, takefocus=False)
        view.pack(side='left', fill='both', expand=True)
        rows = ttk.Frame(view, style='Glass.TFrame')
        slot = view.create_window(0, 0, anchor='nw', window=rows)
        buttons = []
        chosen = 0
        for index, (label, callback) in enumerate(choices):
            marked = selected == label
            if marked:
                chosen = index
            button = ttk.Button(rows, text=('✓  ' if marked else '') + label, style='Menu.Glass.TButton',
                                command=lambda action=callback: self.choose(action))
            button.pack(fill='x', pady=(0, p(3)))
            buttons.append(button)
        window.update_idletasks()
        monitor = win32api.MonitorFromPoint(anchor, win32con.MONITOR_DEFAULTTONEAREST)
        work = win32api.GetMonitorInfo(monitor)['Work']
        row_height = max(1, buttons[0].winfo_reqheight() + p(3))
        full = rows.winfo_reqheight()
        view_height = min(full, row_height * 7, max(row_height, work[3] - work[1] - p(90)))
        view.configure(height=view_height, scrollregion=(0, 0, 1, full))
        if view_height < full:
            bar = ttk.Scrollbar(body, orient='vertical', command=view.yview)
            bar.pack(side='right', fill='y')
            view.configure(yscrollcommand=bar.set)
        view.bind('<Configure>', lambda event: view.itemconfigure(slot, width=event.width))
        window.bind('<MouseWheel>', lambda event: view.yview_scroll(-int(event.delta / 120), 'units'))
        window.bind('<Escape>', lambda event: self.motion.hide())
        window.bind('<FocusOut>', lambda event: window.after_idle(self.dismiss_if_blurred))

        def focus(index):
            index %= len(buttons)
            start, end = view.yview()
            top = index * row_height
            if top < start * full:
                view.yview_moveto(top / full)
            elif top + row_height > end * full:
                view.yview_moveto(max(0, top + row_height - view_height) / full)
            buttons[index].focus_set()
            return 'break'

        for i, button in enumerate(buttons):
            button.bind('<Down>', lambda event, i=i: focus(i + 1))
            button.bind('<Up>', lambda event, i=i: focus(i - 1))
            button.bind('<Home>', lambda event: focus(0))
            button.bind('<End>', lambda event: focus(-1))
            button.bind('<Return>', lambda event, button=button: button.invoke())
        window.update_idletasks()
        width = min(p(width_dip), work[2] - work[0])
        height = min(window.winfo_reqheight(), work[3] - work[1])
        window.geometry(f'{width}x{height}')
        x = max(work[0], min(anchor[0], work[2] - width))
        y = below + p(5) if below + p(5) + height <= work[3] else anchor[1] - height - p(5)
        y = max(work[1], min(y, work[3] - height))
        def place():
            win32gui.SetWindowPos(native_handle(window), win32con.HWND_TOPMOST, x, y, width, height,
                                  win32con.SWP_NOACTIVATE)
        self.motion.show(place)
        focus(chosen)

    def choose(self, action):
        self.motion.hide()
        action()

    def dismiss_if_blurred(self):
        if self.window.winfo_exists() and self.motion.open:
            focus = self.window.focus_get()
            if focus is None or focus.winfo_toplevel() != self.window:
                self.motion.hide()
