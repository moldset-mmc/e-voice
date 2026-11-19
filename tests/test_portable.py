from array import array
from pathlib import Path
import queue
import io
import wave
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop_entry import model_directory
from model_files import MODEL_NAMES, model_entries
from preferences import read_settings, write_settings
from speech_engine import RATE, Recorder, SpeechEngine, segments, wav_segments
from unix_input import shortcut_expression


class PortableTests(unittest.TestCase):
    def test_locked_manifest_only_contains_redistributable_assets(self):
        self.assertEqual({e['name'] for e in model_entries(model_directory())['files']}, MODEL_NAMES)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'manifest.json').write_text('{"files": []}')
            with self.assertRaisesRegex(ValueError, 'manifest'):
                model_entries(directory)

    def test_invalid_and_old_preferences_are_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            for bad in ('null', '[]', '7', '{oops', '{"microphone":[],"show_float":"yes"}'):
                path.write_text(bad)
                self.assertEqual(read_settings(path), {})
            expected = {'floating_position': [-900, 45], 'show_float': False, 'hotkey': 'F9'}
            write_settings(path, dict(expected, unexpected='ignore'))
            self.assertEqual(read_settings(path), expected)

    def test_hardware_rate_fallback_and_exact_ten_minute_boundary(self):
        import sounddevice as sd
        events = queue.Queue()
        recorder = Recorder(events)
        calls = []
        def substitute(**args):
            calls.append(args)
            if args['samplerate'] == RATE:
                raise sd.PortAudioError('Unsupported rate')
            return Mock()
        with patch('sounddevice.RawInputStream', side_effect=substitute), patch('sounddevice.query_devices', return_value={'default_samplerate': 48000}):
            recorder.start()
            self.assertEqual(recorder.rate, 48000)
            recorder.frames = 48000 * 600 - 2
            for _ in range(2):
                calls[-1]['callback'](array('f', [.25]*4), 4, None, Mock(input_overflow=False))
            self.assertEqual(recorder.stop(), array('f', [.25]*2).tobytes())
        self.assertEqual(list(events.queue).count(('record_limit', None)), 1)

    def test_native_rate_reaches_speech_engine(self):
        engine = SpeechEngine.__new__(SpeechEngine)
        engine.recognizer = Mock()
        stream = engine.recognizer.create_stream.return_value
        stream.result.text = 'Готово'
        self.assertEqual(engine.transcribe(array('f', [.1]*24000).tobytes(), rate=48000), 'Готово')
        rate, audio = stream.accept_waveform.call_args.args
        self.assertEqual((rate, len(audio)), (48000, 24000+19200))

    def test_long_wav_uses_the_same_quiet_cuts_with_bounded_reads(self):
        rate = 8000
        samples = array('h', (i % 319 for i in range(rate*46)))
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as dest:
            dest.setparams((1, 2, rate, 0, 'NONE', ''))
            dest.writeframes(samples.tobytes())
        buffer.seek(0)
        with wave.open(buffer, 'rb') as source:
            with patch.object(source, 'readframes', wraps=source.readframes) as reads:
                chunks = list(wav_segments(source, rate))
                self.assertLessEqual(max(call.args[0] for call in reads.call_args_list), rate*20+1)
        self.assertEqual(chunks, list(segments(array('f', (s/32768 for s in samples)), rate)))

    def test_portable_shortcut_handles_single_keys_and_chords(self):
        self.assertEqual(shortcut_expression('Ctrl + Alt + Space'), '<ctrl>+<alt>+<space>')
        self.assertEqual(shortcut_expression('F9'), '<f9>')
        self.assertEqual(shortcut_expression('Cmd + K'), '<cmd>+k')
        for invalid in ('Ctrl', 'Ctrl + Ctrl + A', 'Unknown', 'Alt + '):
            with self.assertRaises(ValueError):
                shortcut_expression(invalid)
