"""Check the installed binary with public test audio and isolated preferences."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    exe = Path(sys.argv[1]).resolve(strict=True)
    audio = ROOT / 'tests/fixtures/russian-example.wav'
    with tempfile.TemporaryDirectory() as temp:
        subenv = dict(os.environ, EVOICE_DATA_DIR=temp)
        for name, args in [('speech', ['--transcribe', str(audio), '--output']),
                           ('interaction', ['--test-audio', str(audio), '--interaction-test'])]:
            output = ROOT / 'build' / ('qa-' + name + '.json')
            output.unlink(missing_ok=True)
            subprocess.run([str(exe), *args, str(output)], cwd=temp, env=subenv, timeout=200, check=True)
            data = json.loads(output.read_text(encoding='utf-8'))
            if name == 'speech':
                assert 'похвал' in data['text'].casefold() and len(data['text']) > 100, data
            else:
                assert data['passed'], data
            print(name, 'OK')


if __name__ == '__main__':
    main()
