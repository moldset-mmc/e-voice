"""Pearl glass surfaces and charcoal controls for the Windows interface."""

from functools import lru_cache
import time
import sys
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageOps, ImageTk
if sys.platform == 'win32':
    from popup_glass import CORNER_DIP, GlassBackdrop


SURFACE = '#EFF4F6'
PANEL = '#E0E8ED'
FIELD = '#FAFCFD'
BORDER = '#8295A1'
TEXT = '#17262F'
MUTED = '#405460'
ACTION = '#20303B'
SECONDARY = '#344550'
HOVER = '#405563'
PRESSED = '#14232C'
ON_DARK = '#F8FBFC'
DISABLED = '#4D5C66'
ON_DISABLED = '#DEE6EB'
ACCENT = '#245A6E'
FOCUS_LIGHT = '#C7EBF4'
RECORD = '#5C303B'
RECORD_HOVER = '#70424B'
ON_RECORD = '#FFE2D8'
# The fade scales an RGBA surface. The glass, bevel and symbols have their own alpha.
GLASS_ALPHA = 255
SETTINGS_ALPHA = 1.0
MENU_ALPHA = 1.0
POPUP_OPEN_MS = 175
POPUP_CLOSE_MS = 120


def _glyph(draw, name, xy, size, ink):
    x, y = xy
    def at(points):
        return tuple((round(x + a * size / 20), round(y + b * size / 20)) for a, b in points)
    def box(a, b, c, d):
        return (*at(((a, b),))[0], *at(((c, d),))[0])
    stroke = max(1, round(size / 11))
    if name == 'mic':
        draw.rounded_rectangle(box(7, 2, 13, 12), radius=round(size * .15), outline=ink, width=stroke)
        draw.arc(box(4, 5, 16, 16), 0, 180, fill=ink, width=stroke)
        draw.line(at(((10, 16), (10, 19))), fill=ink, width=stroke)
        draw.line(at(((7, 19), (13, 19))), fill=ink, width=stroke)
    elif name == 'stop':
        draw.rounded_rectangle(box(5, 5, 15, 15), radius=round(size * .11), fill=ink)
    elif name == 'more':
        for center in (4, 10, 16):
            draw.ellipse(box(center - 1.65, 8.35, center + 1.65, 11.65), fill=ink)
    else:
        for index, height in enumerate((4, 8, 12, 8, 4)):
            col = 3 + index * 3.5
            draw.line(at(((col, 10 - height / 2), (col, 10 + height / 2))), fill=ink, width=stroke)


def icon_image(recording=False):
    image = Image.new('RGBA', (64, 64))
    draw = ImageDraw.Draw(image)
    ink = ON_RECORD if recording else ON_DARK
    draw.ellipse((2, 2, 61, 61), fill=SURFACE, outline=BORDER, width=2)
    draw.ellipse((8, 8, 55, 55), fill=RECORD if recording else ACTION)
    _glyph(draw, 'stop' if recording else 'mic', (14, 13), 36, ink)
    return image


def _tint(width, height, top, bottom):
    gradient = Image.linear_gradient('L').resize((width, height))
    return ImageOps.colorize(gradient, black=top, white=bottom)


CAPTIONS = {'loading': 'Подготовка…', 'ready': 'Начать запись',
            'recording': 'Стоп и вставить', 'transcribing': 'Распознаю…'}


@lru_cache(maxsize=64)
def glass_disc(diameter, state, hot=False, pressed=False):
    """Actual per-pixel material. Background content stays visible through both faces."""
    more = state == 'menu'
    native_size = 26 if more else 46
    side = diameter * 3
    image = Image.new('RGBA', (side, side))
    draw = ImageDraw.Draw(image)
    unit = max(1, round(side / native_size))
    bounds = (0, 0, side - 1, side - 1)
    draw.ellipse(bounds, fill=(224, 238, 246, 48), outline=(91, 116, 135, 156), width=unit)
    draw.arc(bounds, 195, 296, fill=(251, 255, 255, 224), width=unit)
    draw.arc(bounds, 8, 65, fill=(215, 207, 240, 165), width=unit)
    inset = (2 if more else 4) * unit
    inner = (inset, inset, side - inset - 1, side - inset - 1)
    fill = RECORD if state == 'recording' else ACTION
    face_alpha = 134 if pressed else 108 if hot else 79
    draw.ellipse(inner, fill=(*ImageColor.getrgb(fill), face_alpha), outline=(245, 252, 255, 112), width=unit)

    symbols = {'menu': 'more', 'ready': 'mic', 'recording': 'stop'}
    mark = Image.new('L', image.size)
    extent = round(side * (.60 if more else .48))
    offset = (side - extent) // 2
    _glyph(ImageDraw.Draw(mark), symbols.get(state, 'signal'), (offset, offset), extent, 255)
    # A dark silhouette beneath the light mark keeps it legible over white or busy windows.
    outline = mark.filter(ImageFilter.MaxFilter(2 * max(1, round(.85 * unit)) + 1))
    image.paste((20, 33, 43, 232), (0, 0, side, side), outline)
    ink = ON_RECORD if state == 'recording' else ON_DARK
    image.paste((*ImageColor.getrgb(ink), 252), (0, 0, side, side), mark)
    return image.resize((diameter, diameter), Image.Resampling.LANCZOS)


def menu_rim(size, scale):
    width, height = size
    image = Image.new('RGB', size, SURFACE)
    unit = max(1, round(scale))
    radius = round(CORNER_DIP * scale)
    mask = Image.new('L', size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width-1, height-1), radius=radius,
                                           outline=255, width=max(2, round(2.3 * scale)))
    image.paste(_tint(width, height, '#F6FCFF', '#9FAFC6'), (0, 0), mask)
    draw = ImageDraw.Draw(image)
    draw.arc((0, 0, 2*radius, 2*radius), 180, 270, fill='#FFFFFF', width=unit)
    draw.line((radius, 0, round(width*.65), 0), fill='#FFFFFF', width=unit)
    draw.arc((width-2*radius-1, height-2*radius-1, width-1, height-1),
             0, 85, fill='#E3DAEF', width=unit)
    return ImageTk.PhotoImage(image)


def glass_frame(window, scale, padding):
    window.configure(bg=SURFACE)
    window.attributes('-transparentcolor', SURFACE)
    edge = tk.Label(window, background=SURFACE, borderwidth=0, highlightthickness=0)
    edge.place(relwidth=1, relheight=1)
    inset = round(8 * scale)
    interior = tuple(max(0, side - inset + round(2.5 * scale)) for side in padding)
    body = ttk.Frame(window, padding=interior, style='Glass.TFrame')
    body.pack(fill='both', expand=True, padx=inset, pady=inset)
    last_size = None

    def resize(event):
        nonlocal last_size
        if event.widget is window and (event.width, event.height) != last_size and min(event.width, event.height) > 8:
            last_size = (event.width, event.height)
            edge.image = menu_rim(last_size, scale)
            edge.configure(image=edge.image)

    window.bind('<Configure>', resize, add='+')
    return body


def _switch_image(scale, on, focused=False):
    width, height = round(34 * scale), round(18 * scale)
    unit = max(1, round(scale))
    image = Image.new('RGBA', (width, height))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height // 2,
                           fill=ACTION if on else FIELD, outline=TEXT if focused or on else MUTED, width=unit)
    if focused:
        draw.rounded_rectangle((2 * unit, 2 * unit, width - 2 * unit - 1, height - 2 * unit - 1),
                               radius=height // 2, outline=FOCUS_LIGHT if on else ACCENT, width=unit)
    inset = round(4 * scale)
    knob = height - 2 * inset
    x = width - knob - inset if on else inset
    draw.ellipse((x, inset, x + knob - 1, inset + knob - 1), fill=ON_DARK if on else MUTED)
    return ImageTk.PhotoImage(image)


def _button_face(scale, color, focused=False):
    # Tk stretches the flat centre of each image and preserves the rounded corners.
    radius = max(3, round(10 * scale))
    side = 2 * radius + 5
    image = Image.new('RGB', (side * 3, side * 3), SURFACE)
    draw = ImageDraw.Draw(image)
    end = side * 3 - 1
    draw.rounded_rectangle((0, 0, end, end), radius=radius * 3, fill=color)
    if focused:
        inset = max(3, round(2 * scale * 3))
        draw.rounded_rectangle((inset, inset, end - inset, end - inset), radius=max(1, radius * 3 - inset),
                               outline=FOCUS_LIGHT, width=max(3, round(1.5 * scale * 3)))
    return ImageTk.PhotoImage(image.resize((side, side), Image.Resampling.LANCZOS))


def _configure_buttons(root, style, scale):
    p = lambda value: round(value * scale)
    root._glass_button_faces = []
    for role, resting, hover, pressed, names in (
        ('Secondary', SECONDARY, HOVER, PRESSED, ('Glass.TButton', 'Menu.Glass.TButton')),
        ('Action', ACTION, SECONDARY, PRESSED, ('Action.Glass.TButton',)),
        ('Stop', RECORD, RECORD_HOVER, '#46242D', ('Stop.Glass.TButton',)),
    ):
        element = f'Glass.{role}.face'
        colors = (resting, DISABLED, pressed, hover, resting, pressed, hover)
        images = [_button_face(scale, color, focused=index >= 4) for index, color in enumerate(colors)]
        root._glass_button_faces.extend(images)
        style.element_create(element, 'image', images[0], ('disabled', images[1]),
                             ('pressed', 'focus', images[5]), ('active', 'focus', images[6]),
                             ('pressed', images[2]), ('active', images[3]), ('focus', images[4]),
                             border=max(3, p(10)) + 1, padding=0, sticky='nswe')
        for name in names:
            menu = name == 'Menu.Glass.TButton'
            style.layout(name, [(element, {'sticky': 'nswe', 'children': [
                ('Button.padding', {'sticky': 'nswe', 'children': [('Button.label', {'sticky': 'nswe'})]})
            ]})])
            style.configure(name, background=resting, foreground=ON_RECORD if role == 'Stop' else ON_DARK,
                            padding=(p(10 if menu else 11), p(6)), anchor='w' if menu else 'center')
            style.map(name, foreground=[('disabled', ON_DISABLED)])


def configure_settings(root, scale):
    p = lambda value: round(value * scale)
    root.configure(bg=SURFACE)
    root.attributes('-alpha', SETTINGS_ALPHA)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=('Segoe UI', 10), background=SURFACE, foreground=TEXT)
    style.configure('Glass.TFrame', background=SURFACE)
    style.configure('Glass.TLabel', background=SURFACE, foreground=TEXT)
    style.configure('Title.Glass.TLabel', font=('Segoe UI Semibold', 19))
    style.configure('Section.Glass.TLabel', font=('Segoe UI Semibold', 11))
    style.configure('Hint.Glass.TLabel', foreground=MUTED, font=('Segoe UI', 9))
    style.configure('State.Glass.TLabel', foreground=ACCENT)
    style.configure('Record.Glass.TLabel', foreground=RECORD)

    _configure_buttons(root, style, scale)

    style.configure('Glass.Horizontal.TProgressbar', background=ACCENT, troughcolor=SURFACE,
                    bordercolor=SURFACE, lightcolor=ACCENT, darkcolor=ACCENT, thickness=p(3))
    style.configure('Glass.TSeparator', background=BORDER)

    switches = tuple(_switch_image(scale, on, focus) for on, focus in ((False, False), (True, False), (False, True), (True, True)))
    root._glass_switches = switches
    style.element_create('Glass.Switch', 'image', switches[0], ('selected', 'focus', switches[3]),
                         ('focus', switches[2]), ('selected', switches[1]))
    style.layout('Switch.Glass.TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
        ('Checkbutton.label', {'side': 'left', 'sticky': 'w'}),
        ('Glass.Switch', {'side': 'right', 'sticky': 'e'}),
    ]})])
    style.configure('Switch.Glass.TCheckbutton', background=SURFACE, foreground=TEXT, padding=(p(2), p(5)))
    style.map('Switch.Glass.TCheckbutton', background=[('active', PANEL)], foreground=[('disabled', MUTED)])


class PopupMotion:
    """Fade an already-positioned window, reversing cleanly on a second action."""

    def __init__(self, window, resting_alpha):
        self.window = window
        self.resting_alpha = resting_alpha
        self.open = False
        self.generation = 0
        self.glass = GlassBackdrop(window)

    def show(self, prepare=None):
        if not self.open:
            self.open = True
            if self.window.state() == 'withdrawn':
                self.window.attributes('-alpha', 0.0)
                self.window.deiconify()
            if prepare:
                prepare()
            self.window.update_idletasks()
            self.glass.show()
            self._fade(self.resting_alpha, POPUP_OPEN_MS, first_visible=0.12)
        self.window.lift()
        self.window.focus_force()

    def hide(self):
        if self.open:
            self.open = False
            self._fade(0.0, POPUP_CLOSE_MS, after=self.window.withdraw)

    def _fade(self, target, milliseconds, first_visible=0.0, after=None):
        self.generation += 1
        generation = self.generation
        initial = max(first_visible, float(self.window.attributes('-alpha')))
        self.window.attributes('-alpha', initial)
        self.glass.sync(initial / self.resting_alpha)
        started = time.monotonic()

        def paint_frame():
            if generation != self.generation or not self.window.winfo_exists():
                return
            progress = min(1.0, 1000 * (time.monotonic() - started) / milliseconds)
            smooth = progress * progress * (3 - 2 * progress)
            alpha = initial + (target - initial) * smooth
            self.window.attributes('-alpha', alpha)
            self.glass.sync(alpha / self.resting_alpha)
            if progress < 1.0:
                self.window.after(16, paint_frame)
            elif after:
                after()

        self.window.after(16, paint_frame)
