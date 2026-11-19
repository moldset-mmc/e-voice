"""Tests for the review-hardening changes: transcription timeout, shared worker,
and the throttled level meter. Platform-neutral — no Win32 imports."""
import queue
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recognition_session import RecognitionSession, TIMEOUT_CAP, TIMEOUT_FLOOR
from dictation import transcribe_in_background
from speech_engine import Recorder


class TimeoutPolicyTests(unittest.TestCase):
    def test_default_timeout_scales_with_audio_duration(self):
        audio = bytes(10 * 4 * 16000)  # 10 s of float32 @ 16 kHz
        self.assertEqual(RecognitionSession._default_timeout(audio, None, 16000), 10 * 3 + 60)

    def test_default_timeout_respects_floor_and_cap(self):
        # Empty bytes still get the 1-second minimum: 1 * 3 + 60.
        self.assertEqual(RecognitionSession._default_timeout(b'', None, 16000), 63)
        huge = bytes(700 * 4 * 16000)  # 700 s -> 2160 s, capped
        self.assertEqual(RecognitionSession._default_timeout(huge, None, 16000), TIMEOUT_CAP)
        # A non-bytes payload (e.g. an array/list) falls back to the floor.
        self.assertEqual(RecognitionSession._default_timeout([], None, 16000), TIMEOUT_FLOOR)

    def test_timeout_kills_process_and_raises(self):
        class Pipe:
            def __init__(self): self.closed = threading.Event()
            def send(self, *_): pass
            def send_bytes(self, _): pass
            def recv(self):
                self.closed.wait(2)
                raise EOFError
            def close(self): self.closed.set()

        class Proc:
            alive = True
            def is_alive(self): return self.alive
            def terminate(self): self.alive = False
            def kill(self): self.alive = False
            def join(self, _): pass
            def close(self): pass

        session = RecognitionSession('unused-model-directory')
        process = Proc()
        session.connection, session.process = Pipe(), process
        with self.assertRaisesRegex(RuntimeError, 'не ответило'):
            session.transcribe(b'audio', rate=16000, timeout=0.05)
        self.assertFalse(process.alive, 'process must be terminated on timeout')
        self.assertIsNone(session.process, 'session must reset so the next warm() respawns')


class BackgroundWorkerTests(unittest.TestCase):
    def test_posts_result(self):
        events = queue.Queue()

        class Session:
            def transcribe(self, audio, test_file, rate):
                return 'привет'

        transcribe_in_background(Session(), events, b'', None, 16000, result_event='text')
        self.assertEqual(events.get(timeout=5), ('text', 'привет'))

    def test_posts_error_with_prefix(self):
        events = queue.Queue()

        class Session:
            def transcribe(self, audio, test_file, rate):
                raise RuntimeError('сбой')

        transcribe_in_background(Session(), events, b'', None, 16000,
                                 result_event='result', error_prefix='Не удалось: ')
        name, value = events.get(timeout=5)
        self.assertEqual(name, 'error')
        self.assertEqual(value, 'Не удалось: сбой')


class LevelThrottleTests(unittest.TestCase):
    def test_level_emitted_every_other_callback(self):
        class Status:
            input_overflow = False

        events = queue.Queue()
        recorder = Recorder(events)
        recorder.rate = 16000
        frames = 1600
        silence = bytes(frames * 4)  # all zeros -> RMS 0.0
        for _ in range(6):
            recorder._on_audio(silence, frames, None, Status())

        levels = 0
        while True:
            try:
                name, _ = events.get_nowait()
            except queue.Empty:
                break
            if name == 'level':
                levels += 1
        self.assertEqual(levels, 3, '6 callbacks must yield 3 level events (~5 Hz)')

    def test_record_limit_is_signalled_once(self):
        class Status:
            input_overflow = False

        events = queue.Queue()
        recorder = Recorder(events)
        recorder.rate = 16000
        frames = recorder.rate * 600  # the whole 10-minute budget at once
        recorder._on_audio(bytes(frames * 4), frames, None, Status())
        recorder._on_audio(b'', 0, None, Status())

        self.assertTrue(recorder.limit_reached)
        limits = [name for name, _ in list(events.queue) if name == 'record_limit']
        self.assertEqual(len(limits), 1, 'the 10-minute limit must fire once')


if __name__ == '__main__':
    unittest.main()
