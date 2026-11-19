"""Mouse-hook lifecycle with real Python threads but entirely mocked Win32 APIs.

No hooks, input, GUI, or microphone are opened. Every wait is bounded; cleanup
releases test gates even if an assertion fails.
"""
import importlib.util
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


if sys.platform != 'win32':
    raise unittest.SkipTest('Windows hook callback types (native calls are mocked)')


class MouseHookLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.quit = threading.Event()
        self.user32, self.kernel32 = Mock(), Mock()
        self.user32.GetDoubleClickTime.return_value = 500
        self.user32.GetDpiForSystem.return_value = 96
        self.user32.SetWindowsHookExW.return_value = 123
        self.user32.CallNextHookEx.return_value = 0
        self.user32.GetMessageW.side_effect = lambda *args: self.quit.wait(3) and 0
        self.user32.PostThreadMessageW.side_effect = lambda *args: self.quit.set() or True
        self.kernel32.GetCurrentThreadId.return_value = 77
        path = Path(__file__).resolve().parents[1] / 'mouse_gesture.py'
        spec = importlib.util.spec_from_file_location('_mouse_hook_lifecycle_test', path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'windows_io': SimpleNamespace(user32=self.user32, kernel32=self.kernel32)}):
            spec.loader.exec_module(self.module)
        self.gesture = self.module.MouseGesture.__new__(self.module.MouseGesture)

    def tearDown(self):
        self.quit.set()
        if hasattr(self.gesture, 'thread'):
            self.gesture.close()

    def initialize(self):
        self.gesture.__init__(queue.Queue())
        return self.gesture

    def assert_stopped(self):
        self.assertFalse(self.gesture.thread.is_alive(), 'hook thread leaked')
        self.assertFalse(self.gesture.worker.is_alive(), 'detection worker leaked')
        self.assertIsNone(self.gesture.hook)
        self.assertTrue(self.gesture.stopping.is_set())

    def test_failed_hook_installation_stops_both_threads(self):
        self.user32.SetWindowsHookExW.return_value = 0
        with self.assertRaisesRegex(RuntimeError, 'Windows не разрешила'):
            self.initialize()
        self.assert_stopped()
        self.user32.UnhookWindowsHookEx.assert_not_called()
        self.user32.GetMessageW.assert_not_called()

    def test_exception_during_installation_stops_both_threads(self):
        self.user32.SetWindowsHookExW.side_effect = OSError('installation failed')
        with self.assertRaisesRegex(RuntimeError, 'installation failed'):
            self.initialize()
        self.assert_stopped()
        self.user32.UnhookWindowsHookEx.assert_not_called()

    def test_exception_before_message_queue_stops_both_threads(self):
        self.user32.PeekMessageW.side_effect = OSError('queue failed')
        with self.assertRaisesRegex(RuntimeError, 'queue failed'):
            self.initialize()
        self.assert_stopped()
        self.user32.SetWindowsHookExW.assert_not_called()

    def test_failed_hook_thread_start_stops_already_started_worker(self):
        real_thread = threading.Thread
        made = []

        def make_thread(**kwargs):
            if not made:
                result = real_thread(**kwargs)
            else:
                result = Mock()
                result.start.side_effect = RuntimeError('thread creation failed')
                result.is_alive.return_value = False
            made.append(result)
            return result

        with patch.object(self.module, 'threading', SimpleNamespace(Event=threading.Event, Thread=make_thread)):
            with self.assertRaisesRegex(RuntimeError, 'thread creation failed'):
                self.initialize()
        self.assert_stopped()
        self.user32.SetWindowsHookExW.assert_not_called()

    def test_failed_worker_start_does_not_start_hook_thread(self):
        worker, hook = Mock(), Mock()
        worker.start.side_effect = RuntimeError('worker creation failed')
        worker.is_alive.return_value = hook.is_alive.return_value = False
        factory = Mock(side_effect=[worker, hook])
        with patch.object(self.module, 'threading', SimpleNamespace(Event=threading.Event, Thread=factory)):
            with self.assertRaisesRegex(RuntimeError, 'worker creation failed'):
                self.initialize()
        hook.start.assert_not_called()
        worker.join.assert_not_called()
        hook.join.assert_not_called()

    def test_running_worker_still_detects_triple_click(self):
        self.initialize()
        for start in (1.0, 1.1, 1.2):
            self.gesture.raw_events.put((0x0201, (10, 20), start))
            self.gesture.raw_events.put((0x0202, (10, 20), start + 0.03))
        self.assertEqual(self.gesture.events.get(timeout=2), ('summon_float', (10, 20)))
        self.gesture.close()
        self.assert_stopped()

    def test_close_unhooks_once_and_is_idempotent(self):
        self.initialize()
        self.gesture.close()
        self.gesture.close()
        self.assert_stopped()
        self.user32.UnhookWindowsHookEx.assert_called_once_with(123)

    def test_message_loop_failure_also_releases_worker(self):
        fail = threading.Event()

        def message(*args):
            fail.wait(3)
            raise OSError('message loop failed')

        self.user32.GetMessageW.side_effect = message
        try:
            self.initialize()
        finally:
            fail.set()
        self.gesture.thread.join(2)
        self.gesture.worker.join(2)
        self.assert_stopped()
        self.assertEqual(self.gesture.error, 'message loop failed')
        self.user32.UnhookWindowsHookEx.assert_called_once_with(123)

    def test_callback_after_close_forwards_without_queueing_input(self):
        self.initialize()
        self.gesture.close()
        self.gesture.raw_events = Mock()
        self.assertEqual(self.gesture.on_mouse(0, 0x0201, 0), 0)
        self.gesture.raw_events.put.assert_not_called()
        self.user32.CallNextHookEx.assert_called_once_with(None, 0, 0x0201, 0)

    def exercise_timeout(self, native, result, return_before_completion=False):
        entered, release, stopping = threading.Event(), threading.Event(), threading.Event()
        errors = []

        def delayed(*args):
            entered.set()
            if not release.wait(6):
                raise RuntimeError('test gate was not released')
            return result

        native.side_effect = delayed

        class TimeoutStartup:
            def set(self):
                pass

            def wait(self, timeout):
                if not entered.wait(2):
                    raise RuntimeError('delayed native operation never started')
                return False

        def initialize():
            try:
                self.initialize()
            except BaseException as error:
                errors.append(error)

        events = Mock(side_effect=[TimeoutStartup(), stopping])
        replacement = SimpleNamespace(Event=events, Thread=threading.Thread)
        constructor = threading.Thread(target=initialize, daemon=True)
        with patch.object(self.module, 'threading', replacement):
            try:
                constructor.start()
                self.assertTrue(entered.wait(2))
                self.assertTrue(stopping.wait(2), 'timeout did not initiate cleanup')
                if return_before_completion:
                    constructor.join(3)
                    self.assertFalse(constructor.is_alive(), 'constructor exceeded bounded cleanup')
                    self.assertFalse(self.gesture.worker.is_alive())
                    self.assertTrue(self.gesture.thread.is_alive(), 'native test gate should still be pending')
            finally:
                release.set()
                constructor.join(3)
                if hasattr(self.gesture, 'thread'):
                    self.gesture.thread.join(2)
        self.assertFalse(constructor.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)
        self.assertIn('Не удалось запустить', str(errors[0]))
        self.assert_stopped()
        self.user32.GetMessageW.assert_not_called()

    def test_timeout_before_thread_id_never_installs_a_late_hook(self):
        self.exercise_timeout(self.kernel32.GetCurrentThreadId, 77)
        self.user32.SetWindowsHookExW.assert_not_called()
        self.user32.UnhookWindowsHookEx.assert_not_called()

    def test_timeout_during_installation_unhooks_late_success(self):
        self.user32.PostThreadMessageW.side_effect = None
        self.user32.PostThreadMessageW.return_value = False  # A lost WM_QUIT must not strand a late hook.
        self.exercise_timeout(self.user32.SetWindowsHookExW, 123)
        self.user32.UnhookWindowsHookEx.assert_called_once_with(123)

    def test_success_after_constructor_has_already_failed_is_still_unhooked(self):
        self.exercise_timeout(self.user32.SetWindowsHookExW, 123, return_before_completion=True)
        self.user32.UnhookWindowsHookEx.assert_called_once_with(123)

    def test_timeout_during_installation_handles_late_failure(self):
        self.exercise_timeout(self.user32.SetWindowsHookExW, 0)
        self.user32.UnhookWindowsHookEx.assert_not_called()


if __name__ == '__main__':
    unittest.main()
