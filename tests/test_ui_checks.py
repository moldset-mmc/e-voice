"""Regression tests for the CI harness; all desktop APIs are replaced by mocks."""
import importlib.util
import json
from pathlib import Path
import queue
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch


class UICheckHarnessTests(unittest.TestCase):
    def setUp(self):
        names = ('win32api', 'win32con', 'win32gui', 'win32process', 'tkinter',
                 'dpi_awareness', 'glass_theme')
        deps = {name: MagicMock() for name in names}
        spec = importlib.util.spec_from_file_location(
            '_isolated_ui_checks', Path(__file__).resolve().parents[1] / 'ui_checks.py')
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, deps):
            spec.loader.exec_module(self.module)
        self.root = Mock()
        self.timers = []

        def after(delay, callback):
            self.timers.append((delay, callback))
            return 'timer-' + str(len(self.timers))

        self.root.after.side_effect = after
        self.app = Mock()
        self.app.state = 'transcribing'
        self.app.events = queue.Queue()
        self.app.show_float.get.return_value = False
        self.app.floating.is_shown.return_value = False
        self.app.mouse_gesture = None
        self.output = Mock()

    def start(self):
        self.module.run_checks(self.root, self.app, self.output)
        return next(callback for delay, callback in self.timers if delay == 110_000)

    def result(self):
        return json.loads(self.output.write_text.call_args.args[0])

    def test_synthetic_recorder_matches_dictation_contract(self):
        recorder = self.module.synthetic_recorder(b'pcm', rate=48000)
        self.assertEqual(recorder.stop(), b'pcm')
        self.assertIsNone(recorder.start('ignored device'))
        self.assertIsNone(recorder.stream)
        self.assertFalse(recorder.overflow)
        self.assertFalse(recorder.limit_reached)
        self.assertEqual(recorder.rate, 48000)

    def test_global_watchdog_fails_even_without_a_phase_successor(self):
        watchdog = self.start()
        watchdog()
        self.assertFalse(self.result()['passed'])
        self.assertIn('global deadline', self.result()['error'])
        self.assertEqual(self.app.events.get_nowait(), ('exit', None))
        self.app.exit.assert_not_called()

    def test_callback_failure_is_recorded_and_requests_exit(self):
        self.start()
        error = RuntimeError('callback lost its successor')
        with patch.object(self.module.traceback, 'print_exception'):
            self.root.report_callback_exception(RuntimeError, error, None)
        self.assertFalse(self.result()['passed'])
        self.assertEqual(self.result()['error'], str(error))
        self.assertTrue(self.result()['exit_requested_via_event_queue'])
        self.assertEqual(self.app.events.get_nowait(), ('exit', None))

    def test_finish_is_idempotent_and_cancels_watchdog(self):
        watchdog = self.start()
        watchdog()
        watchdog()
        self.output.write_text.assert_called_once()
        self.root.after_cancel.assert_called_once_with('timer-1')
        self.assertEqual(self.app.events.qsize(), 1)

    def test_output_failure_still_requests_exit(self):
        watchdog = self.start()
        self.output.write_text.side_effect = OSError('disk full')
        with self.assertRaisesRegex(OSError, 'disk full'):
            watchdog()
        self.assertEqual(self.app.events.get_nowait(), ('exit', None))


if __name__ == '__main__':
    unittest.main()
