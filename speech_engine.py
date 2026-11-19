"""Offline microphone capture and recognition of normalized float32 samples."""
from array import array
from pathlib import Path
import math
import threading
import sys
import wave
from model_files import verify_model

RATE = 16000


def segment_end(samples, cursor, rate, limit):
    end = min(cursor + limit, len(samples))
    if end < len(samples):
        window = max(1, rate // 5)
        candidates = range(max(cursor + rate, end - 5 * rate), end - window + 1, window)
        cut = min(candidates, key=lambda i: sum(v * v for v in samples[i:i + window]))
        return cut + window // 2
    return end


def segments(samples, rate=RATE, seconds=20):
    """Bound model memory; prefer a quiet 200 ms cut in the last five seconds."""
    limit = int(rate * seconds)
    cursor = 0
    while cursor < len(samples):
        end = segment_end(samples, cursor, rate, limit)
        yield samples[cursor:end]
        cursor = end


def wav_segments(source, rate):
    """Look ahead by one sample to make exactly the same cuts as microphone audio."""
    remaining, limit = source.getnframes(), rate * 20
    pending = array('f')
    while remaining or pending:
        count = min(remaining, limit + 1 - len(pending))
        if count:
            pcm = array('h')
            pcm.frombytes(source.readframes(count))
            if len(pcm) != count:
                raise ValueError('Неполный WAV-файл.')
            if sys.byteorder == 'big':
                pcm.byteswap()
            pending.extend(value / 32768 for value in pcm)
            remaining -= count
        end = segment_end(pending, 0, rate, limit)
        yield pending[:end]
        del pending[:end]


class SpeechEngine:
    def __init__(self, model_dir):
        import sherpa_onnx
        model_dir = Path(model_dir)
        verify_model(model_dir)
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

    def transcribe(self, samples, cancel=None, rate=RATE):
        if isinstance(samples, (bytes, bytearray)):
            samples = memoryview(samples).cast('f')
        elif not isinstance(samples, array) or samples.typecode != 'f':
            samples = array('f', samples)
        if len(samples) < rate // 5:
            return ''
        return self._decode(segments(samples, rate), rate, cancel)

    def _decode(self, parts, rate, cancel):
        padding = array('f', [0]) * (rate // 5)
        texts = []
        for part in parts:
            if cancel is not None and cancel.is_set():
                break
            if max(part) < 0.001 and min(part) > -0.001:
                continue
            stream = self.recognizer.create_stream()
            waveform = array('f', padding)
            waveform.extend(part)
            waveform.extend(padding)
            stream.accept_waveform(rate, waveform)
            self.recognizer.decode_stream(stream)
            text = stream.result.text.strip()
            if text:
                texts.append(text)
        return ' '.join(texts)

    def transcribe_file(self, path, cancel=None):
        with wave.open(str(path), 'rb') as source:
            rate = source.getframerate()
            if not 8000 <= rate <= 192000 or source.getsampwidth() != 2 or source.getnchannels() != 1:
                raise ValueError('Нужен моно WAV PCM16 с частотой от 8000 до 192000 Гц.')
            if source.getnframes() > rate * 600:
                raise ValueError('Максимальная длительность одной диктовки — 10 минут.')
            if source.getnframes() < rate // 5:
                return ''
            return self._decode(wav_segments(source, rate), rate, cancel)


class Recorder:
    def __init__(self, events):
        self.events = events
        self.stream = None
        self.samples = bytearray()
        self.lock = threading.Lock()
        self.frames = 0
        self.rate = RATE
        self.overflow = False
        self.limit_reached = False
        self._level_tick = 0

    def _on_audio(self, data, frames, timing, status):
        buffer = memoryview(data).cast('B')
        with self.lock:
            count = min(frames, self.rate * 600 - self.frames)
            if count <= 0:
                return
            self.samples.extend(buffer[:count * 4])
            self.frames += count
            if self.frames == self.rate * 600 and not self.limit_reached:
                self.limit_reached = True
                self.events.put(('record_limit', None))
            self.overflow = self.overflow or bool(status.input_overflow)
        # Throttle the meter to ~5 Hz (callback fires roughly every 100 ms);
        # the first frame still reports immediately so the meter reacts at once.
        self._level_tick += 1
        if self._level_tick % 2 == 1:
            values = buffer[:count * 4].cast('f')
            level = math.sqrt(sum(v * v for v in values) / count)
            self.events.put(('level', level))

    def start(self, device=None):
        import sounddevice as sd
        self.samples = bytearray()
        self.frames = 0
        self.overflow = False
        self.limit_reached = False
        self._level_tick = 0
        def open_stream(rate):
            return sd.RawInputStream(samplerate=rate, channels=1, dtype='float32',
                                     blocksize=rate // 10, device=device, callback=self._on_audio)
        self.rate = RATE
        try:
            self.stream = open_stream(self.rate)
        except sd.PortAudioError:
            native_rate = round(sd.query_devices(device, 'input')['default_samplerate'])
            if native_rate == RATE or not 8000 <= native_rate <= 192000:
                raise
            self.rate = native_rate
            self.stream = open_stream(self.rate)
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
