"""Keep model allocations outside the UI and release them between dictation sessions."""
import multiprocessing as mp


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
                text = engine.transcribe(audio) if kind == 'audio' else engine.transcribe_file(path)
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

    def warm(self):
        if self.process and self.process.is_alive():
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

    def transcribe(self, audio, test_file=None):
        connection = self.connection
        if test_file:
            connection.send(('file', str(test_file)))
        else:
            connection.send(('audio', None))
            connection.send_bytes(audio)
        ok, result = connection.recv()
        if not ok:
            raise RuntimeError(result)
        return result

    def close(self):
        connection, process = self.connection, self.process
        self.connection = self.process = None
        if process:
            if process.is_alive():
                process.terminate()
            process.join(1)
            if not process.is_alive():
                process.close()
        if connection:
            connection.close()
