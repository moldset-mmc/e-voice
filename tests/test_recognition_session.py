"""Bounded recognizer IPC and teardown, without loading a speech model."""
import multiprocessing as mp
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recognition_session import RecognitionSession


def wait_without_reading(pipe, ready):
    ready.set()
    try:
        time.sleep(10)
    finally:
        pipe.close()


class FakeProcess:
    def __init__(self, alive=True):
        self.alive = alive
        self.is_alive = Mock(side_effect=lambda: self.alive)
        self.terminate = Mock(side_effect=self.stop)
        self.kill = Mock(side_effect=self.stop)
        self.join = Mock()
        self.close = Mock()

    def stop(self):
        self.alive = False


class RecognitionCleanupTests(unittest.TestCase):
    def session(self, alive=True):
        session = RecognitionSession('unused-model-directory')
        process, connection = FakeProcess(alive), Mock()
        session.process, session.connection = process, connection
        return session, process, connection

    def test_close_stops_process_closes_pipe_and_is_idempotent(self):
        session, process, connection = self.session()
        session.close()
        session.close()
        process.terminate.assert_called_once_with()
        process.join.assert_called_once_with(1)
        process.kill.assert_not_called()
        process.close.assert_called_once_with()
        connection.close.assert_called_once_with()
        self.assertIsNone(session.process)
        self.assertIsNone(session.connection)
        self.assertFalse(session._teardown_pending)

    def test_already_dead_process_is_reaped_without_terminate(self):
        session, process, connection = self.session(alive=False)
        session.close()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        process.join.assert_called_once_with(1)
        process.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_abort_uses_the_same_bounded_cleanup(self):
        session, process, connection = self.session()
        process.terminate.side_effect = None  # Graceful termination fails to stop it.
        session._abort()
        process.kill.assert_called_once_with()
        self.assertEqual(process.join.call_count, 2)
        self.assertTrue(all(call.args == (1,) for call in process.join.call_args_list))
        process.close.assert_called_once_with()
        connection.close.assert_called_once_with()
        self.assertIsNone(session.process)

    def test_terminate_exception_still_kills_closes_and_reports_original_error(self):
        session, process, connection = self.session()
        error = OSError('terminate denied')
        process.terminate.side_effect = error
        with self.assertRaisesRegex(RuntimeError, 'terminate denied') as caught:
            session.close()
        self.assertIs(caught.exception.__cause__, error)
        process.kill.assert_called_once_with()
        process.close.assert_called_once_with()
        connection.close.assert_called_once_with()
        self.assertIsNone(session.process)
        self.assertIsNone(session.connection)

    def test_join_exception_does_not_skip_kill_or_pipe_close(self):
        session, process, connection = self.session()
        process.terminate.side_effect = None
        process.join.side_effect = [OSError('join failed'), None]
        with self.assertRaisesRegex(RuntimeError, 'join failed'):
            session.close()
        process.kill.assert_called_once_with()
        process.close.assert_called_once_with()
        connection.close.assert_called_once_with()
        self.assertIsNone(session.process)

    def test_all_process_api_failures_still_close_pipe_and_keep_unverified_handle(self):
        session, process, connection = self.session()
        for method in (process.is_alive, process.terminate, process.kill, process.join):
            method.side_effect = OSError('native process API failed')
        with self.assertRaisesRegex(RuntimeError, 'Recognizer remains alive'):
            session.close()
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.join.call_count, 2)
        process.close.assert_not_called()
        connection.close.assert_called_once_with()
        self.assertIs(session.process, process)
        self.assertIsNone(session.connection)

    def test_surviving_process_is_visible_retryable_and_not_reused_by_warm(self):
        session, process, connection = self.session()
        process.terminate.side_effect = None
        process.kill.side_effect = OSError('kill denied')
        with self.assertRaisesRegex(RuntimeError, 'Recognizer remains alive'):
            session.close()
        self.assertIs(session.process, process)
        self.assertIsNone(session.connection)
        process.close.assert_not_called()
        connection.close.assert_called_once_with()
        with patch('recognition_session.mp.get_context') as context:
            with self.assertRaisesRegex(RuntimeError, 'Recognizer remains alive'):
                session.warm()
        context.assert_not_called()
        process.kill.side_effect = process.stop
        session.close()
        process.close.assert_called_once_with()
        connection.close.assert_called_once_with()
        self.assertIsNone(session.process)
        self.assertFalse(session._teardown_pending)

    def test_process_handle_close_failure_does_not_skip_pipe_close(self):
        session, process, connection = self.session()
        process.close.side_effect = OSError('process close failed')
        with self.assertRaisesRegex(RuntimeError, 'process close failed'):
            session.close()
        connection.close.assert_called_once_with()
        self.assertIs(session.process, process)
        self.assertIsNone(session.connection)
        process.close.side_effect = None
        session.close()
        self.assertIsNone(session.process)
        self.assertEqual(process.close.call_count, 2)

    def test_failed_pipe_close_is_reported_and_retryable(self):
        session, process, connection = self.session()
        connection.close.side_effect = OSError('pipe close failed')
        with self.assertRaisesRegex(RuntimeError, 'pipe close failed'):
            session.close()
        process.close.assert_called_once_with()
        self.assertIsNone(session.process)
        self.assertIs(session.connection, connection)
        connection.close.side_effect = None
        session.close()
        self.assertIsNone(session.connection)
        self.assertEqual(connection.close.call_count, 2)

    def test_simultaneous_exit_and_timeout_close_each_native_handle_once(self):
        session, process, connection = self.session()
        entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
        errors = []

        def terminate():
            entered.set()
            if not release.wait(3):
                raise RuntimeError('test gate not released')
            process.stop()

        def run(action, started=None):
            if started:
                started.set()
            try:
                action()
            except BaseException as exc:
                errors.append(exc)

        process.terminate.side_effect = terminate
        first = threading.Thread(target=run, args=(session.close,), daemon=True)
        second = threading.Thread(target=run, args=(session._abort, second_started), daemon=True)
        first.start()
        try:
            self.assertTrue(entered.wait(2))
            second.start()
            self.assertTrue(second_started.wait(2))
            connection.close.assert_not_called()
        finally:
            release.set()
            first.join(3)
            if second.ident is not None:
                second.join(3)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        process.terminate.assert_called_once_with()
        process.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_transcription_does_not_hold_or_wait_for_teardown_lock(self):
        session, process, connection = self.session()
        connection.poll.return_value = True
        connection.recv.return_value = (True, 'text')
        completed, results = threading.Event(), []

        def transcribe():
            try:
                results.append(session.transcribe(b'audio', timeout=0.1))
            finally:
                completed.set()

        worker = threading.Thread(target=transcribe, daemon=True)
        with session._close_lock:
            worker.start()
            finished_without_lock = completed.wait(2)
        worker.join(2)
        self.assertTrue(finished_without_lock, 'transcription must not take the teardown lock')
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, ['text'])
        process.terminate.assert_not_called()

    def test_deadline_covers_header_audio_and_partial_reply_and_allows_retry(self):
        for stage, test_file in [('send', 'sample.wav'), ('send_bytes', None), ('recv', None)]:
            with self.subTest(stage=stage):
                session, process, pipe = self.session()
                entered, released, exited = threading.Event(), threading.Event(), threading.Event()

                def blocked(*args):
                    entered.set()
                    try:
                        released.wait(3)
                        raise EOFError
                    finally:
                        exited.set()

                getattr(pipe, stage).side_effect = blocked
                pipe.close.side_effect = released.set
                try:
                    with self.assertRaisesRegex(RuntimeError, 'не ответило'):
                        session.transcribe(b'audio', test_file=test_file, timeout=0.15)
                    self.assertTrue(entered.is_set())
                    self.assertTrue(exited.wait(1))
                    process.terminate.assert_called_once()
                    session.connection = Mock()
                    session.connection.recv.return_value = (True, 'следующая запись')
                    self.assertEqual(session.transcribe(b'next'), 'следующая запись')
                finally:
                    released.set()

    def test_old_exchange_cannot_close_replacement_pipe(self):
        session, process, pipe = self.session()
        replacement = Mock()
        session.connection = replacement
        session.close(expected_connection=pipe)
        process.terminate.assert_not_called()
        replacement.close.assert_not_called()

    def test_real_full_pipe_is_released_by_timeout(self):
        context = mp.get_context('spawn')
        local, remote = context.Pipe()
        ready = context.Event()
        process = context.Process(target=wait_without_reading, args=(remote, ready), daemon=True)
        session = RecognitionSession('unused')
        process.start()
        remote.close()
        session.connection, session.process = local, process
        try:
            self.assertTrue(ready.wait(8))
            with self.assertRaisesRegex(RuntimeError, 'не ответило'):
                session.transcribe(bytes(2 * 1024 * 1024), timeout=0.15)
            self.assertIsNone(session.process)
            self.assertTrue(local.closed)
        finally:
            session.close()


if __name__ == '__main__':
    unittest.main()
