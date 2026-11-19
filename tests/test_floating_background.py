"""Headless overlay regressions: all Win32 calls and rendering are test doubles.

Do not create real windows or touch the owner's cursor, focus, keyboard or microphone.
Only win32con (numeric constants) is imported from pywin32.
"""
import ctypes
import importlib.util
from pathlib import Path
import queue
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


if sys.platform != 'win32':
    raise unittest.SkipTest('Windows floating control (mocked native APIs)')

import win32con as wc


class NativeError(Exception):
    def __init__(self, winerror, function):
        super().__init__(winerror, function, 'native test error')
        self.winerror = winerror


class FloatingBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.gui = Mock(error=NativeError)
        self.api = Mock()
        self.user32 = Mock()
        self.controls = Mock()
        self.surface = Mock()
        dependencies = {
            'win32api': self.api,
            'win32gui': self.gui,
            'floating_surface': SimpleNamespace(FloatingSurface=Mock(return_value=self.surface)),
            'glass_theme': SimpleNamespace(CAPTIONS={'loading': 'Loading'}, GLASS_ALPHA=255),
            'windows_io': SimpleNamespace(activate_target=Mock(), foreground=Mock(), over_taskbar=Mock()),
        }
        # Load a private module, not the cached production import used by other tests.
        # Stub dependencies BEFORE executing it, including every ctypes DLL load.
        path = Path(__file__).resolve().parents[1] / 'floating_control.py'
        spec = importlib.util.spec_from_file_location('_floating_background_test', path)
        self.module = importlib.util.module_from_spec(spec)
        libraries = {'user32': self.user32, 'comctl32': self.controls}
        with patch.dict(sys.modules, dependencies), patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kw: libraries[name]):
            spec.loader.exec_module(self.module)

        self.visible = True
        self.gui.IsWindowVisible.side_effect = lambda hwnd: self.visible
        self.gui.IsIconic.return_value = False
        self.gui.IsWindow.return_value = True
        self.gui.ShowWindow.side_effect = self.show_window
        self.control = self.module.FloatingControl.__new__(self.module.FloatingControl)
        self.control.hwnd, self.control.button, self.control.menu_button = 101, 102, 103
        self.control.events = queue.Queue()
        self.control.schedule = None
        self.control.fade_version = self.control.icon_generation = 0
        self.control.hiding = False
        self.control.opacity = self.module.GLASS_ALPHA
        self.control.icon_transition = (None, 1.0)
        self.control.hovered = None
        self.control.drag_origin = None
        self.control.surface = self.surface
        self.control.class_name, self.control.instance, self.control.icon = 'test', 1, 2
        self.control.paint = Mock()
        self.control.ensure_on_screen = Mock()
        self.control.part_at = Mock(return_value='action')

    def show_window(self, hwnd, command):
        self.visible = command != wc.SW_HIDE

    def test_constructor_creates_hidden_toolwindow_without_taskbar_or_activation(self):
        self.api.GetModuleHandle.return_value = 1
        self.api.GetMonitorInfo.return_value = {'Work': (0, 0, 1920, 1080)}
        self.gui.LoadCursor.return_value = 2
        self.gui.LoadImage.return_value = 3
        self.user32.RegisterClassW.return_value = 1
        self.user32.CreateWindowExW.return_value = 101
        self.controls.SetWindowSubclass.return_value = True
        with patch.object(self.module.FloatingControl, 'apply_layout'), patch.object(self.module.FloatingControl, 'set_opacity'):
            control = self.module.FloatingControl(queue.Queue())
        arguments = self.user32.CreateWindowExW.call_args.args
        extended_style, style = arguments[0], arguments[3]
        for flag in (wc.WS_EX_TOOLWINDOW, wc.WS_EX_NOACTIVATE, wc.WS_EX_TOPMOST, wc.WS_EX_LAYERED):
            self.assertTrue(extended_style & flag)
        self.assertFalse(extended_style & wc.WS_EX_APPWINDOW)
        self.assertFalse(style & wc.WS_VISIBLE)
        self.assertEqual(control.hwnd, 101)
        self.gui.ShowWindow.assert_not_called()

    def test_second_launch_opens_settings_without_enabling_overlay(self):
        self.visible = False
        result = self.control.window_proc(101, self.module.RESTORE_MESSAGE, 0, 0)
        self.assertEqual(result, 0)
        self.assertEqual(list(self.control.events.queue), [('show', None)])
        self.gui.ShowWindow.assert_not_called()
        self.assertFalse(self.visible)

    def test_system_commands_do_not_toggle_a_taskbar_window(self):
        for command, event in ((wc.SC_MINIMIZE, 'hide_float'), (wc.SC_RESTORE, 'show')):
            with self.subTest(command=command):
                self.assertEqual(self.control.window_proc(101, wc.WM_SYSCOMMAND, command, 0), 0)
                self.assertEqual(self.control.events.get_nowait(), (event, None))
        self.gui.ShowWindow.assert_not_called()
        self.gui.GetCursorPos.assert_not_called()

    def test_activation_does_not_probe_taskbar_or_toggle_overlay(self):
        self.control.window_proc(101, wc.WM_ACTIVATE, wc.WA_ACTIVE, 0)
        self.assertTrue(self.control.events.empty())
        self.gui.GetCursorPos.assert_not_called()

    def test_mouse_activation_remains_disabled(self):
        result = self.control.window_proc(101, wc.WM_MOUSEACTIVATE, 0, 0)
        self.assertEqual(result, wc.MA_NOACTIVATE)

    def test_hide_really_hides_instead_of_minimizing(self):
        self.control.hovered = 'action'
        self.control.visible(False)
        self.gui.ShowWindow.assert_called_once_with(101, wc.SW_HIDE)
        self.assertFalse(self.control.is_shown())
        self.assertFalse(self.control.hiding)
        self.assertIsNone(self.control.hovered)
        self.assertEqual(self.control.opacity, self.module.GLASS_ALPHA)
        self.control.visible(False)
        self.assertEqual(self.gui.ShowWindow.call_count, 1)

    def test_show_hidden_overlay_does_not_activate_it(self):
        self.visible = False
        self.control.visible(True)
        self.gui.ShowWindow.assert_called_once_with(101, wc.SW_SHOWNOACTIVATE)
        self.assertTrue(self.control.is_shown())
        self.assertEqual(self.control.opacity, self.module.GLASS_ALPHA)
        flags = self.gui.SetWindowPos.call_args.args[-1]
        self.assertTrue(flags & wc.SWP_NOACTIVATE)

    def test_hover_skips_hidden_overlay_without_querying_desktop(self):
        self.visible = False
        self.control.refresh_hover()
        self.gui.GetCursorPos.assert_not_called()
        self.gui.WindowFromPoint.assert_not_called()
        self.control.paint.assert_not_called()

    def test_access_denied_hover_is_quiet_and_next_tick_recovers(self):
        for function in ('GetCursorPos', 'WindowFromPoint', 'ScreenToClient'):
            with self.subTest(function=function):
                self.control.hovered = None
                self.control.paint.reset_mock()
                self.gui.GetCursorPos.return_value = (10, 20)
                self.gui.WindowFromPoint.return_value = 101
                # part_at uses ScreenToClient; exercise that native error too.
                self.control.part_at.side_effect = lambda point: self.gui.ScreenToClient(101, point) and 'action'
                self.gui.ScreenToClient.return_value = (10, 20)
                native = getattr(self.gui, function)
                native.side_effect = NativeError(5, function)
                with patch.object(self.module.traceback, 'print_exc') as log:
                    for _ in range(3):
                        self.control.refresh_hover()
                    log.assert_not_called()
                self.control.paint.assert_not_called()
                self.assertIsNone(self.control.hovered)
                native.side_effect = None
                self.control.refresh_hover()
                self.assertEqual(self.control.hovered, 'action')
                self.control.paint.assert_called_once()

    def test_unexpected_native_errors_are_not_silenced(self):
        self.gui.GetCursorPos.side_effect = NativeError(6, 'GetCursorPos')
        with self.assertRaises(NativeError):
            self.control.refresh_hover()

    def test_programming_errors_are_not_silenced(self):
        self.gui.GetCursorPos.side_effect = RuntimeError('unexpected')
        with self.assertRaisesRegex(RuntimeError, 'unexpected'):
            self.control.refresh_hover()

    def test_unchanged_hover_does_not_repaint(self):
        self.gui.GetCursorPos.return_value = (10, 20)
        self.gui.WindowFromPoint.return_value = 101
        self.control.hovered = 'action'
        self.control.refresh_hover()
        self.control.paint.assert_not_called()
        self.gui.WindowFromPoint.return_value = 999
        self.control.refresh_hover()
        self.assertIsNone(self.control.hovered)
        self.control.paint.assert_called_once()

    def test_show_cancels_pending_hide_animation(self):
        frames = []
        self.control.schedule = lambda delay, callback: frames.append(callback)
        with patch.object(self.module.time, 'monotonic', return_value=0) as clock:
            self.control.visible(False)
            pending_hide = frames.pop()
            self.control.visible(True)
            pending_show = frames.pop()
            clock.return_value = 1
            pending_hide()
            pending_show()
        self.gui.ShowWindow.assert_not_called()
        self.assertTrue(self.control.is_shown())
        self.assertFalse(self.control.hiding)
        self.assertEqual(self.control.opacity, self.module.GLASS_ALPHA)
        self.assertEqual(frames, [])

    def test_latest_hide_wins_after_rapid_hide_show_hide(self):
        frames = []
        self.control.schedule = lambda delay, callback: frames.append(callback)
        with patch.object(self.module.time, 'monotonic', return_value=0) as clock:
            self.control.visible(False)
            self.control.visible(True)
            self.control.visible(False)
            clock.return_value = 1
            for callback in list(frames):
                callback()
        self.gui.ShowWindow.assert_called_once_with(101, wc.SW_HIDE)
        self.assertFalse(self.control.is_shown())

    def test_direct_hide_invalidates_pending_animation(self):
        frames = []
        self.control.schedule = lambda delay, callback: frames.append(callback)
        with patch.object(self.module.time, 'monotonic', return_value=0) as clock:
            self.control.fade_in()
            self.control.finish_hide()
            self.control.paint.reset_mock()
            clock.return_value = 1
            frames.pop()()
        self.control.paint.assert_not_called()
        self.assertFalse(self.control.is_shown())

    def test_reveal_near_cursor_cancels_pending_hide_without_moving(self):
        frames = []
        self.control.schedule = lambda delay, callback: frames.append(callback)
        self.control.dpi, self.control.on_left = 144, True
        self.api.MonitorFromPoint.return_value = self.api.MonitorFromWindow.return_value = 1
        work = (0, 0, 1920, 1080)
        self.api.GetMonitorInfo.return_value = {'Work': work}
        self.user32.GetDpiForWindow.return_value = 144
        position, _ = self.module.placement_near_cursor((800, 450), work, 144)
        self.gui.GetWindowRect.return_value = (*position, position[0] + 116, position[1] + 69)
        self.control.move_to = Mock()
        with patch.object(self.module.time, 'monotonic', return_value=0) as clock:
            self.control.visible(False)
            pending_hide = frames.pop()
            self.control.show_at((800, 450))
            clock.return_value = 1
            pending_hide()
            frames.pop()()
        self.control.move_to.assert_not_called()
        self.gui.ShowWindow.assert_not_called()
        self.assertFalse(self.control.hiding)
        self.assertEqual(self.control.opacity, self.module.GLASS_ALPHA)

    def test_close_invalidates_pending_hide_without_native_show(self):
        frames = []
        self.control.schedule = lambda delay, callback: frames.append(callback)
        self.control.visible(False)
        self.control.close()
        frames.pop()()
        self.gui.DestroyWindow.assert_called_once_with(101)
        self.gui.ShowWindow.assert_not_called()
        self.assertIsNone(self.control.hwnd)


if __name__ == '__main__':
    unittest.main()
