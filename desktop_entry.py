"""Public entry points that can run without a display, microphone or network."""
import argparse
import json
from pathlib import Path
import sys


def model_directory():
    if not getattr(sys, 'frozen', False):
        base = Path(__file__).resolve().parent
    elif sys.platform in ('darwin', 'linux'):
        base = Path(sys._MEIPASS)
    else:
        base = Path(sys.executable).resolve().parent
    return base / 'models' / 'gigaam-v3'


def dispatch():
    if '--transcribe' in sys.argv[1:]:
        parser = argparse.ArgumentParser(description='E Voice · локальное распознавание WAV')
        parser.add_argument('--transcribe', type=Path, required=True, metavar='WAV')
        parser.add_argument('--output', type=Path, help='Save UTF-8 JSON here')
        args = parser.parse_args()
        from speech_engine import SpeechEngine
        result = SpeechEngine(model_directory()).transcribe_file(args.transcribe)
        data = json.dumps({'text': result}, ensure_ascii=False)
        if args.output:
            args.output.write_text(data + '\n', encoding='utf-8')
        elif sys.stdout is not None:
            print(data)
        else:
            parser.error('Укажите --output для запуска без консоли.')
        return True
    if sys.platform in ('darwin', 'linux'):
        from unix_desktop import main
        main()
        return True
    return False
