"""Restore the model pinned in git, or verify an existing offline copy."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.request import urlopen

MODEL = Path(__file__).resolve().parents[1] / 'models' / 'gigaam-v3'


def verified(path, entry):
    if not path.is_file() or path.stat().st_size != entry['bytes']:
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == entry['sha256']


def prepare(offline=False):
    manifest = json.loads((MODEL / 'manifest.json').read_text(encoding='utf-8'))
    remote = f"https://huggingface.co/{manifest['repository']}/resolve/{manifest['revision']}/"
    for entry in manifest['files']:
        name = entry['name']
        target = MODEL / name
        if verified(target, entry):
            continue
        if offline:
            raise RuntimeError(f'Отсутствует или повреждён файл модели {name}')
        with tempfile.NamedTemporaryFile(dir=MODEL, delete=False, suffix='.partial') as out:
            temporary = Path(out.name)
        try:
            with urlopen(remote + name, timeout=120) as response, temporary.open('wb') as out:
                while block := response.read(1024 * 1024):
                    out.write(block)
            if not verified(temporary, entry):
                raise RuntimeError(f'Контрольная сумма файла {name} не совпала')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true', help='Verify all files without any downloads')
    prepare(parser.parse_args().offline)
