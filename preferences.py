"""Read user settings defensively; never persist a recording or its transcript."""
import json
import os
from pathlib import Path
import tempfile


def read_settings(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    for key in ('autopaste', 'show_float', 'triple_click', 'auto_hide_recording'):
        if isinstance(data.get(key), bool):
            result[key] = data[key]
    for key in ('hotkey', 'microphone'):
        if isinstance(data.get(key), str):
            result[key] = data[key]
    point = data.get('floating_position')
    if isinstance(point, list) and len(point) == 2 and all(type(n) is int and abs(n) < 1000000 for n in point):
        result['floating_position'] = point
    return result


def write_settings(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
