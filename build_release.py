"""Build E Voice and its single offline Windows installer."""
import argparse
from importlib import metadata
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tkinter
import platform
import sysconfig
import tempfile

from tools.prepare_model import MODEL, prepare
from model_files import MODEL_NAMES, verify_model

ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'dist'
APP = DIST / 'EVoice'


def include_licenses(destination):
    destination.mkdir(parents=True, exist_ok=True)
    python_terms = [Path(sys.base_prefix) / 'LICENSE.txt', Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt']
    shutil.copy2(next(p for p in python_terms if p.is_file()), destination / 'Python.txt')
    tcl_dir = Path(tkinter.Tcl().eval('info library'))
    for folder in (tcl_dir, tcl_dir.parent / ('tk' + str(tkinter.TkVersion))):
        for terms in folder.glob('license.terms'):
            shutil.copy2(terms, destination / (terms.parent.name + '-license.txt'))
    packages = ['pillow', 'sounddevice', 'six', 'cffi', 'sherpa-onnx', 'sherpa-onnx-core']
    if sys.platform == 'win32':
        packages.extend(('pystray', 'pywin32'))
    else:
        packages.append('pynput')
        if sys.platform == 'linux':
            packages.append('python-xlib')
        else:
            packages.extend(('pyobjc-core', 'pyobjc-framework-Cocoa', 'pyobjc-framework-Quartz',
                             'pyobjc-framework-CoreText', 'pyobjc-framework-ApplicationServices'))
    for dist in packages:
        package = metadata.distribution(dist)
        for entry in package.files or ():
            if entry.name.lower().startswith(('license', 'copying', 'notice', 'thirdparty', 'third_party')):
                source = Path(package.locate_file(entry))
                if source.is_file():
                    location = destination / dist / str(entry).replace('..', '_parent_')
                    location.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, location)
    shutil.copytree(ROOT / 'licenses', destination, dirs_exist_ok=True)


def stage_assets(destination):
    model = destination / 'models' / MODEL.name
    model.mkdir(parents=True, exist_ok=True)
    for name in sorted(MODEL_NAMES | {'manifest.json', 'LICENSE'}):
        shutil.copy2(MODEL / name, model / name)
    verify_model(model)
    include_licenses(destination / 'licenses')
    for name in ('THIRD_PARTY.md', 'LICENSE'):
        shutil.copy2(ROOT / name, destination)


def build_unix(version):
    build = ROOT / 'build'
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=build, prefix='assets-') as temporary:
        stage_assets(Path(temporary))
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', 'Desktop.spec'], cwd=ROOT,
                       env=dict(os.environ, EVOICE_RELEASE_ASSETS=temporary), check=True)
    architecture = platform.machine().lower()
    if sys.platform == 'darwin':
        package = build / 'EVoice-component.pkg'
        with tempfile.TemporaryDirectory(dir=build, prefix='pkg-') as payload:
            subprocess.run(['ditto', str(DIST / 'E Voice.app'), str(Path(payload) / 'E Voice.app')], check=True)
            subprocess.run(['pkgbuild', '--root', payload, '--component-plist',
                            str(ROOT / 'installer/mac-components.plist'), '--install-location',
                            '/Applications', '--identifier', 'io.github.moldset-mmc.evoice',
                            '--version', version, str(package)], check=True)
        subprocess.run(['productbuild', '--distribution', str(ROOT / 'installer' / 'mac.xml'),
                        '--package-path', str(build), str(DIST / f'E-Voice-macOS-{architecture}.pkg')], check=True)
    else:
        arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}[architecture]
        with tempfile.TemporaryDirectory(dir=build, prefix='deb-') as temp:
            build_deb(Path(temp), arch, version)
        archive = DIST / f'E-Voice-Linux-{arch}.tar.gz'
        subprocess.run(['tar', '-czf', str(archive), '-C', str(DIST), 'EVoice'], check=True)


def build_deb(root, arch, version):
    shutil.copytree(APP, root / 'opt' / 'e-voice', symlinks=True)
    for source, name in [('installer/e-voice.desktop', 'usr/share/applications/e-voice.desktop'),
                         ('installer/linux-control', 'DEBIAN/control')]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((ROOT / source).read_text().replace('@VERSION@', version).replace('@ARCH@', arch))
    from PIL import Image
    icon = root / 'usr/share/icons/hicolor/64x64/apps/e-voice.png'
    icon.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(ROOT / 'assets/evoice.ico') as original:
        original.convert('RGBA').save(icon)
    link = root / 'usr/bin/e-voice'
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to('/opt/e-voice/EVoice')
    subprocess.run(['dpkg-deb', '--root-owner-group', '-Zxz', '-z4', '--build', str(root),
                    str(DIST / f'E-Voice-Linux-{arch}.deb')], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--iscc', type=Path, help='Path to the Inno Setup 7 compiler')
    parser.add_argument('--offline', action='store_true', help='Use existing verified model files')
    args = parser.parse_args()
    version = (ROOT / 'VERSION').read_text(encoding='ascii').strip()
    if sys.platform in ('darwin', 'linux'):
        prepare(args.offline)
        build_unix(version)
        return
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
    tkinter.Tcl().eval('info patchlevel')
    prepare(args.offline)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', 'EVoice.spec'],
                   cwd=ROOT, check=True)
    for relative in ('EVoice.exe', '_internal/_tkinter.pyd', '_internal/_tk_data/tk.tcl',
                     '_internal/_tcl_data/init.tcl', '_internal/assets/evoice.ico',
                     '_internal/sherpa_onnx/lib/onnxruntime.dll',
                     '_internal/_sounddevice_data/portaudio-binaries/libportaudio64bit.dll'):
        if not (APP / relative).is_file():
            raise RuntimeError('Missing from build: ' + relative)
    stage_assets(APP)
    subprocess.run([str(Path(compiler).resolve()), f'/DAppVersion={version}',
                    str(ROOT / 'installer' / 'EVoice.iss')], cwd=ROOT, check=True)
    print(DIST / 'E-Voice-Setup-x64.exe')


if __name__ == '__main__':
    main()
