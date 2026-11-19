"""Keep model allocations outside the UI and release them between dictation sessions."""
import multiprocessing as mp
import threading
import time

#: A dictation can run up to 10 minutes; allow generous CPU headroom before
#: declaring the recognizer process unresponsive (see :meth:`RecognitionSession.transcribe`).
TIMEOUT_CAP = 1800.0
TIMEOUT_FLOOR = 60.0


def _recognize(connection, model_directory):
    from speech_engine import SpeechEngine
    try:
        try:
            engine = SpeechEngine(model_directory)
            error = None
        except Exception as exc:
            engine, error = None, str(exc)
        while True:
            kind, path = connection.recv()
            audio = connection.recv_bytes() if kind == 'audio' else None
            if error is not None:
                connection.send((False, error))
                return
            try:
                text = engine.transcribe(audio, rate=path) if kind == 'audio' else engine.transcribe_file(path)
                connection.send((True, text))
            except Exception as exc:
                connection.send((False, str(exc)))
    except (OSError, EOFError):
        pass
    finally:
        connection.close()


class RecognitionSession:
    def __init__(self, model_directory):
        self.model_directory = model_directory
        self.process = self.connection = None
        self._close_lock = threading.Lock()
        self._teardown_pending = False

    def warm(self):
        if not self._teardown_pending and self.connection is not None and self.process and self.process.is_alive():
            return
        self.close()
        context = mp.get_context('spawn')
        local, remote = context.Pipe()
        process = context.Process(target=_recognize, args=(remote, self.model_directory), daemon=True)
        try:
            process.start()
        except BaseException:
            local.close()
            raise
        finally:
            remote.close()
        self.connection, self.process = local, process

    def transcribe(self, audio, test_file=None, rate=16000, timeout=None):
        """Transcribe with a bounded wait so the UI can never hang forever.

        The recognizer runs in a separate process; if it stops answering
        (a rare deadlock on unusual hardware), the caller is released after
        ``timeout`` seconds and the process is killed so the next session
        respawns cleanly.
        """
        connection = self.connection
        if test_file:
            connection.send(('file', str(test_file)))
        else:
            connection.send(('audio', rate))
            connection.send_bytes(audio)
        if timeout is None:
            timeout = self._default_timeout(audio, test_file, rate)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._abort()
                raise RuntimeError('Распознавание не ответило вовремя. Попробуйте ещё раз.')
            if connection.poll(remaining):
                ok, result = connection.recv()
                if not ok:
                    raise RuntimeError(result)
                return result

    @staticmethod
    def _default_timeout(audio, test_file, rate):
        if test_file:
            # transcribe_file caps WAV input at 10 minutes.
            return min(TIMEOUT_CAP, 660.0)
        if isinstance(audio, (bytes, bytearray)):
            seconds = max(1, len(audio) // (4 * rate))
            return min(TIMEOUT_CAP, max(TIMEOUT_FLOOR, seconds * 3 + 60))
        return TIMEOUT_FLOOR

    def _abort(self):
        self.close()

    def close(self):
        # Timeout recovery and GUI exit can race. Serialize only teardown, never
        # transcription's pipe wait, so each native handle is closed just once.
        with self._close_lock:
            self._teardown_pending = True
            connection, process = self.connection, self.process
            self.connection = None
            errors = []

            def attempt(name, action, default=None):
                try:
                    return action()
                except Exception as exc:
                    errors.append((name, exc))
                    return default

            try:
                if process:
                    if attempt('is_alive', process.is_alive, True):
                        attempt('terminate', process.terminate)
                    attempt('join', lambda: process.join(1))
                    if attempt('is_alive', process.is_alive, True):
                        # Still try kill if terminate or the first join failed.
                        attempt('kill', process.kill)
                        attempt('join after kill', lambda: process.join(1))
                    if attempt('is_alive', process.is_alive, True):
                        errors.append(('process', RuntimeError('Recognizer remains alive after terminate/kill')))
                        # Keep the handle visible and retryable, rather than
                        # reporting an idle session while a model still lives.
                    else:
                        def release_process():
                            process.close()
                            self.process = None
                        attempt('process.close', release_process)
            finally:
                if connection:
                    try:
                        connection.close()
                    except Exception as exc:
                        self.connection = connection  # Preserve a failed close for retry.
                        errors.append(('connection.close', exc))
            if errors:
                details = '; '.join(f'{name}: {exc}' for name, exc in errors)
                raise RuntimeError('Recognizer cleanup failed: ' + details) from errors[0][1]
            self._teardown_pending = False
