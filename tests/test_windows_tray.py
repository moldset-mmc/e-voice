"""Exercise the Windows application with all GUI, audio and input APIs mocked.

Import a private copy so no real window, tray thread, hook or microphone is used.
These tests also run on non-Windows CI workers.
"""
import importlib.util
import io
from pathlib import Path
import queue
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch


class NativeError(Exception):
    def __init__(self, code):
        super().__init__(code, 'native', 'test error')
        self.winerror = code


class Variable:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Icon:
    def __init__(self, name, image, title, menu):
        self.menu = menu
        self.run = Mock()

    def _on_notify(self, wparam, lparam):
        return 'base notification'


class WindowsTrayTests(unittest.TestCase):
    def setUp(self):
        self.gui = Mock(error=NativeError)
        con = SimpleNamespace(WM_RBUTTONUP=0x205, WM_CONTEXTMENU=0x7b)
        pystray = SimpleNamespace(Icon=Icon, Menu=lambda *items: items,
                                 MenuItem=lambda label, action, **kwargs: SimpleNamespace(label=label, action=action, **kwargs))
        deps = {name: MagicMock() for name in (
            'tkinter', 'sounddevice', 'win32api', 'speech_engine', 'windows_io',
            'floating_control', 'glass_theme', 'mouse_gesture', 'dpi_awareness',
            'choices', 'recognition_session', 'dictation', 'preferences')}
        deps.update(win32gui=self.gui, win32con=con, pystray=pystray)
        deps['tkinter'].BooleanVar = Variable
        deps['tkinter'].StringVar = Variable
        deps['sounddevice'].query_devices.return_value = []
        deps['windows_io'].DEFAULT_HOTKEY = 'Ctrl + Alt + Space'
        deps['windows_io'].parse_hotkey.return_value = (1, 2)
        deps['windows_io'].hotkey_label.return_value = 'Ctrl + Alt + Space'
        deps['preferences'].read_settings.return_value = {}
        deps['recognition_session'].RecognitionSession.return_value.process = None
        path = Path(__file__).resolve().parents[1] / 'app.py'
        spec = importlib.util.spec_from_file_location('_windows_tray_test', path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, deps):
            spec.loader.exec_module(self.module)
        self.thread = patch.object(self.module.threading, 'Thread').start()
        self.addCleanup(patch.stopall)
        self.root = MagicMock()
        self.root.winfo_fpixels.return_value = 96
        self.root.winfo_reqheight.return_value = 600

    def application(self, config=None):
        self.module.read_settings.return_value = config or {}
        return self.module.Application(self.root)

    def polling_app(self):
        app = self.module.Application.__new__(self.module.Application)
        app.root = self.root
        app.closing = False
        app.last_target = None
        app.events = queue.Queue()
        app.floating = Mock()
        app.settings_motion = SimpleNamespace(open=False)
        app.menu_motion = app.choices = None
        app.last_float_raise = 0
        app.show_settings = Mock()
        app.summon_enabled = Variable(False)
        app.reveal_floating = Mock()
        app.state = 'ready'
        return app

    def test_fresh_start_is_tray_only_without_model_audio_or_mouse_hook(self):
        app = self.application()
        self.assertFalse(app.show_float.get())
        self.assertFalse(app.summon_enabled.get())
        self.assertIsNone(app.mouse_gesture)
        self.module.MouseGesture.assert_not_called()
        app.floating.finish_hide.assert_called_once()
        app.floating.show_at.assert_not_called()
        app.floating.visible.assert_not_called()
        app.settings_motion.show.assert_not_called()
        self.root.deiconify.assert_not_called()
        app.session.warm.assert_not_called()
        app.recorder.start.assert_not_called()
        self.assertIsNotNone(app.tray)
        self.thread.return_value.start.assert_called_once()

    def test_existing_overlay_and_gesture_preferences_are_preserved(self):
        app = self.application({'show_float': True, 'triple_click': True})
        self.assertTrue(app.show_float.get())
        app.floating.show_at.assert_called_once()
        self.module.MouseGesture.assert_called_once_with(app.events)
        app.session.warm.assert_not_called()

    def test_saved_overlay_survives_inaccessible_cursor_at_startup(self):
        control = self.module.FloatingControl.return_value
        control.show_at.side_effect = NativeError(5)
        app = self.application({'show_float': True})
        self.assertTrue(app.show_float.get())
        control.visible.assert_called_once_with(True)
        self.root.after.assert_any_call(50, app.poll)
        self.assertIsNotNone(app.tray)
        app.session.warm.assert_not_called()

    def test_tray_default_action_queues_settings_not_recording_or_overlay(self):
        app = self.application()
        item = app.tray.menu[0]
        self.assertTrue(item.default)
        item.action()
        self.assertEqual(app.events.get_nowait(), ('show', None))
        app.session.warm.assert_not_called()
        app.floating.show_at.assert_not_called()

    def test_tray_right_click_queues_menu(self):
        app = self.application()
        self.gui.GetCursorPos.return_value = (12, 34)
        self.assertEqual(app.tray._on_notify(0, self.module.win32con.WM_RBUTTONUP), 0)
        self.assertEqual(app.events.get_nowait(), ('tray_menu', (12, 34)))

    def test_tray_access_denied_is_ignored_but_unexpected_errors_surface(self):
        app = self.application()
        self.gui.GetCursorPos.side_effect = NativeError(5)
        app.tray._on_notify(0, self.module.win32con.WM_RBUTTONUP)
        self.assertTrue(app.events.empty())
        self.gui.GetCursorPos.side_effect = NativeError(6)
        with self.assertRaises(NativeError):
            app.tray._on_notify(0, self.module.win32con.WM_RBUTTONUP)

    def test_gesture_is_installed_once_and_released_when_disabled(self):
        app = self.application()
        app.summon_enabled.set(True)
        app.toggle_mouse_gesture()
        app.configure_mouse_gesture()
        self.module.MouseGesture.assert_called_once_with(app.events)
        hook = app.mouse_gesture
        app.summon_enabled.set(False)
        app.toggle_mouse_gesture()
        hook.close.assert_called_once()
        self.assertIsNone(app.mouse_gesture)

    def test_failed_hook_is_not_shown_as_enabled(self):
        self.module.MouseGesture.side_effect = RuntimeError('hook unavailable')
        app = self.application({'triple_click': True})
        self.assertFalse(app.summon_enabled.get())
        self.assertIsNone(app.mouse_gesture)
        self.assertEqual(app.status.get(), 'hook unavailable')

    def test_show_event_opens_settings_without_overlay(self):
        app = self.polling_app()
        app.events.put(('show', None))
        app.poll()
        app.show_settings.assert_called_once_with()
        app.reveal_floating.assert_not_called()
        self.root.after.assert_called_once_with(50, app.poll)

    def test_poll_reschedules_after_hover_exception_and_next_tick_handles_exit(self):
        app = self.polling_app()
        app.floating.refresh_hover.side_effect = NativeError(5)
        with self.assertRaises(NativeError):
            app.poll()
        self.root.after.assert_called_once_with(50, app.poll)
        app.exit = Mock(side_effect=lambda: setattr(app, 'closing', True))
        app.events.put(('exit', None))
        self.root.after.reset_mock()
        app.poll()
        app.exit.assert_called_once()
        self.root.after.assert_not_called()

    def test_foreground_access_denied_does_not_starve_commands(self):
        app = self.polling_app()
        app.last_target = (1, 2)
        self.module.foreground.side_effect = NativeError(5)
        app.events.put(('show', None))
        app.poll()
        self.assertIsNone(app.last_target)
        app.show_settings.assert_called_once()
        self.root.after.assert_called_once_with(50, app.poll)

    def test_handler_failure_leaves_following_events_for_next_tick(self):
        app = self.polling_app()
        app.show_settings.side_effect = RuntimeError('window failed')
        app.events.put(('show', None))
        app.events.put(('show', None))
        with self.assertRaisesRegex(RuntimeError, 'window failed'):
            app.poll()
        self.root.after.assert_called_once_with(50, app.poll)
        app.show_settings.side_effect = None
        app.poll()
        self.assertTrue(app.events.empty())
        self.assertEqual(app.show_settings.call_count, 2)
        self.assertEqual(self.root.after.call_count, 2)

    def test_disabled_gesture_cannot_reveal_overlay_from_stale_event(self):
        app = self.polling_app()
        app.events.put(('summon_float', (1, 2)))
        app.poll()
        app.reveal_floating.assert_not_called()

    def test_closing_app_never_schedules_another_tick(self):
        app = self.polling_app()
        app.closing = True
        app.poll()
        self.root.after.assert_not_called()
        self.module.foreground.assert_not_called()

    def shutdown_app(self):
        app = self.polling_app()
        app.root = Mock()
        app.recorder = Mock(stream=object())
        app.session, app.hotkey, app.mouse_gesture = Mock(), Mock(), Mock()
        app.tray, app.status = Mock(), Mock()
        app.worker = None
        return app

    def test_exit_attempts_all_cleanup_after_each_resource_failure(self):
        stages = [('recorder', 'stop'), ('session', 'close'), ('hotkey', 'close'),
                  ('mouse_gesture', 'close'), ('tray', 'stop'), ('floating', 'close'), ('status', 'set')]
        for failed in stages:
            with self.subTest(failed=failed):
                app, order, output = self.shutdown_app(), [], io.StringIO()
                for name, method in stages:
                    def cleanup(*args, stage=(name, method)):
                        order.append(stage)
                        if stage == failed:
                            raise OSError('simulated cleanup failure')
                    getattr(getattr(app, name), method).side_effect = cleanup
                with patch.object(self.module, '_diagnostic_file', output):
                    app.exit()
                    app.exit()  # Neither a failure nor a retry duplicates native cleanup.
                self.assertTrue(app.closing)
                self.assertEqual(order, stages, 'microphone must stop first and failures must not skip later resources')
                app.root.destroy.assert_called_once_with()
                app.root.after.assert_not_called()
                self.assertIn('simulated cleanup failure', output.getvalue())

    def test_exit_without_optional_controls_or_active_microphone(self):
        app = self.shutdown_app()
        app.recorder.stream = None
        app.hotkey = app.mouse_gesture = app.tray = app.floating = None
        app.exit()
        app.recorder.stop.assert_not_called()
        app.session.close.assert_called_once_with()
        app.root.destroy.assert_called_once_with()

    def test_real_exit_after_poll_failure_still_tears_down_and_stops_polling(self):
        app = self.shutdown_app()
        app.floating.refresh_hover.side_effect = NativeError(5)
        with self.assertRaises(NativeError):
            app.poll()
        app.events.put(('exit', None))
        app.root.after.reset_mock()
        app.session.close.side_effect = OSError('recognizer cleanup failed')
        with patch.object(self.module, '_diagnostic_file', io.StringIO()):
            app.poll()
        app.recorder.stop.assert_called_once_with()
        app.floating.close.assert_called_once_with()
        app.root.destroy.assert_called_once_with()
        app.root.after.assert_not_called()

    def test_exit_waits_for_worker_completion_without_blocking_tk(self):
        app, pending = self.shutdown_app(), []
        app.worker = Mock()
        app.worker.is_alive.side_effect = [True, False]
        app.root.after.side_effect = lambda delay, callback: pending.append(callback)
        with patch.object(self.module.time, 'monotonic', return_value=100):
            app.exit()
            app.exit()
            app.root.destroy.assert_not_called()
            self.assertEqual(len(pending), 1)
            pending.pop()()
        app.root.destroy.assert_called_once_with()
        app.worker.join.assert_not_called()
        app.session.close.assert_called_once_with()

    def test_exit_destroys_root_when_daemon_worker_exceeds_deadline(self):
        app, pending, output = self.shutdown_app(), [], io.StringIO()
        app.worker = Mock()
        app.worker.is_alive.return_value = True
        app.root.after.side_effect = lambda delay, callback: pending.append(callback)
        with patch.object(self.module.time, 'monotonic', return_value=100) as clock, \
                patch.object(self.module, '_diagnostic_file', output):
            app.exit()
            pending.pop()()
            app.root.destroy.assert_not_called()
            clock.return_value = 100 + self.module.WORKER_EXIT_TIMEOUT
            pending.pop()()
        self.assertFalse(pending)
        app.root.destroy.assert_called_once_with()
        app.worker.join.assert_not_called()
        self.assertIn('shutdown deadline', output.getvalue())

    def test_exit_handles_worker_probe_and_tk_timer_failures(self):
        for stage in ('worker', 'timer'):
            with self.subTest(stage=stage):
                app = self.shutdown_app()
                app.worker = Mock()
                app.worker.is_alive.return_value = True
                method = app.worker.is_alive if stage == 'worker' else app.root.after
                method.side_effect = OSError('shutdown wait failed')
                with patch.object(self.module, '_diagnostic_file', io.StringIO()):
                    app.exit()
                app.root.destroy.assert_called_once_with()

    def test_exit_quits_event_loop_if_destroy_fails(self):
        app = self.shutdown_app()
        app.root.destroy.side_effect = OSError('destroy failed')
        with patch.object(self.module, '_diagnostic_file', io.StringIO()):
            app.exit()
        app.root.quit.assert_called_once_with()

    def test_broken_diagnostic_stream_cannot_prevent_exit(self):
        app, output = self.shutdown_app(), Mock()
        output.write.side_effect = OSError('log unavailable')
        app.session.close.side_effect = OSError('model unavailable')
        with patch.object(self.module, '_diagnostic_file', output):
            app.exit()
        app.root.destroy.assert_called_once_with()
        app.floating.close.assert_called_once_with()

    def test_toggle_cannot_start_recording_during_shutdown(self):
        app = self.shutdown_app()
        app.closing = True
        app.toggle()
        app.recorder.start.assert_not_called()
        app.session.warm.assert_not_called()
        self.module.foreground.assert_not_called()

    def test_second_test_instance_restores_only_matching_process_window(self):
        self.gui.GetClassName.side_effect = lambda hwnd: self.module.CLASS_NAME if hwnd != 30 else 'other'
        self.gui.EnumWindows.side_effect = lambda callback, extra: [callback(hwnd, extra) for hwnd in (10, 20, 30)]
        native = SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (1, {10: 99, 20: 42, 30: 42}[hwnd]))
        with patch.dict(sys.modules, win32process=native):
            self.assertEqual(self.module.find_instance_window(42), 20)
            self.assertEqual(self.module.find_instance_window(123), 0)
        self.gui.FindWindow.assert_not_called()

    def test_regular_second_instance_keeps_existing_class_lookup(self):
        self.gui.FindWindow.return_value = 123
        self.assertEqual(self.module.find_instance_window(), 123)
        self.gui.FindWindow.assert_called_once_with(self.module.CLASS_NAME, None)
        self.gui.EnumWindows.assert_not_called()

    def test_invalid_test_instance_id_fails_before_creating_any_mutex(self):
        parser = Mock()
        parser.parse_args.return_value = SimpleNamespace(
            interaction_test='result.json', test_audio='fixture.wav', ui_demo=False,
            transcribe=None, smoke_test=None)
        parser.error.side_effect = SystemExit(2)
        for invalid in ('not-a-pid', '0', '-1'):
            with self.subTest(invalid=invalid), \
                    patch.object(self.module.argparse, 'ArgumentParser', return_value=parser), \
                    patch.object(self.module, 'enable_diagnostics'), \
                    patch.dict(self.module.os.environ, EVOICE_TEST_INSTANCE_ID=invalid):
                with self.assertRaises(SystemExit):
                    self.module.main()
        self.module.kernel32.CreateMutexW.assert_not_called()
        self.gui.FindWindow.assert_not_called()
        self.module.tk.Tk.assert_not_called()


if __name__ == '__main__':
    unittest.main()
