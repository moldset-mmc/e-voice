"""The exact assets needed to redistribute and run the Russian transducer."""
import hashlib
import json
from pathlib import Path

MODEL_NAMES = {'encoder.int8.onnx', 'decoder.onnx', 'joiner.onnx', 'tokens.txt'}
MANIFEST_DIGEST = '973ce3711299cef5c24de81b58bfaab34ab054824cda6a5391555a2b34955d25'


def model_entries(directory):
    raw = (Path(directory) / 'manifest.json').read_bytes().replace(b'\r\n', b'\n')
    if hashlib.sha256(raw).hexdigest() != MANIFEST_DIGEST:
        raise ValueError('Повреждён список файлов модели (manifest.json).')
    manifest = json.loads(raw)
    names = [item['name'] for item in manifest['files']]
    if set(names) != MODEL_NAMES or len(names) != len(MODEL_NAMES):
        raise ValueError('Неполный комплект модели.')
    return manifest


def verified(path, entry):
    if not path.is_file() or path.stat().st_size != entry['bytes']:
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == entry['sha256']


def verify_model(directory):
    for item in model_entries(directory)['files']:
        if not verified(Path(directory) / item['name'], item):
            raise ValueError('Отсутствует или повреждён файл модели ' + item['name'])
