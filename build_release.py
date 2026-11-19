"""Build E Voice and its single offline Windows installer."""
import argparse
from importlib import metadata
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tkinter

from tools.prepare_model import MODEL, prepare

ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'dist'
APP = DIST / 'EVoice'


def include_licenses(destination):
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(sys.base_prefix) / 'LICENSE.txt', destination / 'Python.txt')
    for terms in (Path(sys.base_prefix) / 'tcl').glob('*/license.terms'):
        shutil.copy2(terms, destination / (terms.parent.name + '-license.txt'))
    for dist in ('numpy', 'pillow', 'pystray', 'sounddevice', 'six', 'cffi', 'pywin32',
                 'sherpa-onnx', 'sherpa-onnx-core'):
        package = metadata.distribution(dist)
        for entry in package.files or ():
            if entry.name.lower().startswith(('license', 'copying', 'notice', 'thirdparty', 'third_party')):
                source = Path(package.locate_file(entry))
                if source.is_file():
                    location = destination / dist / str(entry).replace('..', '_parent_')
                    location.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, location)
    shutil.copytree(ROOT / 'licenses', destination, dirs_exist_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--iscc', type=Path, help='Path to the Inno Setup 7 compiler')
    parser.add_argument('--offline', action='store_true', help='Use existing verified model files')
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.maxsize <= 2**32:
        parser.error('Требуется 64-битный Python для Windows')
    compiler = args.iscc or shutil.which('ISCC.exe')
    if not compiler:
        for parent in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
            base = Path(os.environ.get(parent, '.'))
            for folder in (base, base / 'Programs'):
                candidate = folder / 'Inno Setup 7' / 'ISCC.exe'
                if candidate.is_file():
                    compiler = candidate
                    break
            if compiler:
                break
    if not compiler or not Path(compiler).is_file():
        parser.error('Передай путь к ISCC.exe через --iscc')
    version = (ROOT / 'VERSION').read_text(encoding='ascii').strip()
    tkinter.Tcl().eval('info patchlevel')
    prepare(args.offline)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', 'EVoice.spec'],
                   cwd=ROOT, check=True)
    for relative in ('EVoice.exe', '_internal/_tkinter.pyd', '_internal/_tk_data/tk.tcl',
                     '_internal/_tcl_data/init.tcl', '_internal/assets/evoice.ico'):
        if not (APP / relative).is_file():
            raise RuntimeError('Missing from build: ' + relative)
    shutil.copytree(MODEL, APP / 'models' / MODEL.name, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('*.partial'))
    include_licenses(APP / 'licenses')
    shutil.copy2(ROOT / 'THIRD_PARTY.md', APP)
    subprocess.run([str(Path(compiler).resolve()), f'/DAppVersion={version}',
                    str(ROOT / 'installer' / 'EVoice.iss')], cwd=ROOT, check=True)
    print(DIST / 'E-Voice-Setup-x64.exe')


if __name__ == '__main__':
    main()
