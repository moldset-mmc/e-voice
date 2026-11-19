"""Shared dictation lifecycle for the Windows and Unix front ends.

Both front ends used to hand-roll the same worker thread and idle-release
timer. Centralising them here means a robustness fix — such as the
transcription timeout in :mod:`recognition_session` — lands once and applies
to every platform instead of being copied into two UIs.
"""
import threading

#: Release the recognizer process after this much idle time.
IDLE_RELEASE_MS = 30_000


def transcribe_in_background(session, events, audio, test_file, rate,
                             result_event='result', error_prefix=''):
    """Transcribe in a daemon worker; post ``(result_event, text)`` or ``('error', message)``."""
    def run():
        try:
            text = session.transcribe(audio, test_file, rate)
            events.put((result_event, text))
        except Exception as exc:
            events.put(('error', (error_prefix + str(exc)).strip()))
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread
