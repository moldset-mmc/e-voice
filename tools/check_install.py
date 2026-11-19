"""Check the installed binary with public test audio and isolated preferences."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave
from array import array

ROOT = Path(__file__).resolve().parents[1]


def main():
    exe = Path(sys.argv[1]).resolve(strict=True)
    audio = ROOT / 'tests/fixtures/russian-example.wav'
    with tempfile.TemporaryDirectory() as temp:
        subenv = dict(os.environ, EVOICE_DATA_DIR=temp)
        native_wav = Path(temp) / 'microphone-48k.wav'
        with wave.open(str(audio), 'rb') as source, wave.open(str(native_wav), 'wb') as dest:
            samples = array('h')
            samples.frombytes(source.readframes(source.getnframes()))
            dest.setparams((1, 2, 48000, 0, 'NONE', ''))
            dest.writeframes(array('h', (value for value in samples for _ in range(3))).tobytes())
        for name, args in [('speech', ['--transcribe', str(audio), '--output']),
                           ('speech-48k', ['--transcribe', str(native_wav), '--output']),
                           ('interaction', ['--test-audio', str(audio), '--interaction-test'])]:
            output = ROOT / 'build' / ('qa-' + name + '.json')
            output.unlink(missing_ok=True)
            subprocess.run([str(exe), *args, str(output)], cwd=temp, env=subenv, timeout=200, check=True)
            data = json.loads(output.read_text(encoding='utf-8'))
            if name.startswith('speech'):
                assert 'похвал' in data['text'].casefold() and len(data['text']) > 100, data
            else:
                assert data['passed'], data
            print(name, 'OK')


if __name__ == '__main__':
    main()
