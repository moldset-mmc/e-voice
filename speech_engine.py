"""Offline microphone capture and recognition of normalized float32 samples."""
from array import array
from pathlib import Path
import hashlib
import json
import math
import threading
import wave

RATE = 16000


def segments(samples, rate=RATE, seconds=20):
    """Bound model memory; prefer a quiet 200 ms cut in the last five seconds."""
    limit = int(rate * seconds)
    cursor = 0
    while cursor < len(samples):
        end = min(cursor + limit, len(samples))
        if end < len(samples):
            window = max(1, rate // 5)
            candidates = range(max(cursor + rate, end - 5 * rate), end - window + 1, window)
            cut = min(candidates, key=lambda i: sum(v * v for v in samples[i:i + window]))
            end = cut + window // 2
        yield samples[cursor:end]
        cursor = end


class SpeechEngine:
    def __init__(self, model_dir):
        import sherpa_onnx
        model_dir = Path(model_dir)
        manifest = json.loads((model_dir / 'manifest.json').read_text(encoding='utf-8'))
        for item in manifest['files']:
            with (model_dir / item['name']).open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() != item['sha256']:
                    raise RuntimeError('Повреждена модель: ' + item['name'])
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model_dir / 'encoder.int8.onnx'),
            decoder=str(model_dir / 'decoder.onnx'),
            joiner=str(model_dir / 'joiner.onnx'),
            tokens=str(model_dir / 'tokens.txt'),
            num_threads=4,
            sample_rate=RATE,
            feature_dim=80,
            decoding_method='greedy_search',
            model_type='nemo_transducer',
        )

    def transcribe(self, samples, cancel=None):
        if isinstance(samples, (bytes, bytearray)):
            floats = array('f')
            floats.frombytes(samples)
            samples = floats
        elif not isinstance(samples, array) or samples.typecode != 'f':
            samples = array('f', samples)
        if len(samples) < RATE // 5:
            return ''
        padding = array('f', [0]) * (RATE // 5)
        texts = []
        for part in segments(samples):
            if cancel is not None and cancel.is_set():
                break
            if max(part) < 0.001 and min(part) > -0.001:
                continue
            stream = self.recognizer.create_stream()
            stream.accept_waveform(RATE, padding + part + padding)
            self.recognizer.decode_stream(stream)
            text = stream.result.text.strip()
            if text:
                texts.append(text)
        return ' '.join(texts)

    def transcribe_file(self, path, cancel=None):
        with wave.open(str(path), 'rb') as source:
            if source.getframerate() != RATE or source.getsampwidth() != 2 or source.getnchannels() != 1:
                raise ValueError('Нужен WAV: PCM16, моно, 16000 Гц')
            pcm = array('h')
            pcm.frombytes(source.readframes(source.getnframes()))
            samples = array('f', (value / 32768 for value in pcm))
        return self.transcribe(samples, cancel)


class Recorder:
    def __init__(self, events):
        self.events = events
        self.stream = None
        self.samples = bytearray()
        self.lock = threading.Lock()
        self.frames = 0
        self.overflow = False
        self.limit_reached = False

    def start(self, device=None):
        import sounddevice as sd
        self.samples = bytearray()
        self.frames = 0
        self.overflow = False
        self.limit_reached = False
        def receive(data, frames, timing, status):
            with self.lock:
                if self.frames >= RATE * 600:
                    if not self.limit_reached:
                        self.limit_reached = True
                        self.events.put(('record_limit', None))
                    return
                self.samples.extend(data)
                self.frames += frames
                self.overflow = self.overflow or bool(status.input_overflow)
            values = memoryview(data).cast('f')
            level = math.sqrt(sum(v * v for v in values) / frames) if frames else 0
            self.events.put(('level', level))
        self.stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype='float32',
                                        blocksize=1600, device=device, callback=receive)
        try:
            self.stream.start()
        except Exception:
            self.stream.close()
            self.stream = None
            raise

    def stop(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        with self.lock:
            result, self.samples = self.samples, bytearray()
        return result
