"""Build E Voice and its single offline Windows installer."""
import argparse
import json
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
    library = tkinter.Tcl().eval('info library')
    if not library.startswith('//zipfs:'):
        tcl_dir = Path(library)
        for folder in (tcl_dir, tcl_dir.parent / ('tk' + str(tkinter.TkVersion))):
            for terms in folder.glob('license.terms'):
                shutil.copy2(terms, destination / (terms.parent.name + '-license.txt'))
    packages = ['pillow', 'sounddevice', 'six', 'cffi', 'sherpa-onnx', 'sherpa-onnx-core', 'pyinstaller']
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
    if sys.platform == 'linux':
        from PIL import Image
        with Image.open(ROOT / 'assets/evoice.ico') as original:
            original.convert('RGBA').save(destination / 'e-voice.png')


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
        linux_library_notices()
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
    icon = root / 'usr/share/icons/hicolor/64x64/apps/e-voice.png'
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(APP / '_internal/e-voice.png', icon)
    link = root / 'usr/bin/e-voice'
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to('/opt/e-voice/EVoice')
    subprocess.run(['dpkg-deb', '--root-owner-group', '-Zxz', '-z4', '--build', str(root),
                    str(DIST / f'E-Voice-Linux-{arch}.deb')], check=True)


def linux_library_notices():
    """Keep the distro's copyright files alongside bundled system libraries."""
    system = Path('/usr/lib') / sysconfig.get_config_var('MULTIARCH')
    destination = APP / '_internal/licenses/system'
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree('/usr/share/common-licenses', destination / 'common-licenses', dirs_exist_ok=True)
    packages = set()
    for name in {p.name for p in (APP / '_internal').rglob('*.so*')}:
        library = system / name
        if not library.is_file():
            continue
        candidates = [str(library.resolve()), str(library)]
        candidates += [path.removeprefix('/usr') for path in candidates if path.startswith('/usr/lib/')]
        owners = subprocess.run(['dpkg-query', '-S', *candidates], capture_output=True, text=True)
        for line in owners.stdout.splitlines():
            packages.add(line.partition(': ')[0])
    records = []
    for package in sorted(packages):
        name = package.split(':')[0]
        copyright_file = Path('/usr/share/doc') / name / 'copyright'
        if copyright_file.is_file():
            shutil.copy2(copyright_file, destination / (name + '.txt'))
            record = subprocess.check_output(['dpkg-query', '-W', '-f=${source:Package}\t${source:Version}', package], text=True)
            records.append(name + '\t' + record)
    (destination / 'packages.tsv').write_text('\n'.join(records) + '\n', encoding='utf-8')


RELEASE_PACKAGES = {
    'Windows-x64': ('E-Voice-Setup-x64.exe',),
    'Linux-amd64': ('E-Voice-Linux-amd64.deb', 'E-Voice-Linux-amd64.tar.gz'),
    'Linux-arm64': ('E-Voice-Linux-arm64.deb', 'E-Voice-Linux-arm64.tar.gz'),
    'macOS-x86_64': ('E-Voice-macOS-x86_64.pkg',),
    'macOS-arm64': ('E-Voice-macOS-arm64.pkg',),
}


def collect_release_artifacts(source, destination, version, tag):
    """Validate the full matrix before copying; never silently overwrite QA JSON."""
    if tag != f'v{version}':
        raise ValueError(f'Release tag {tag!r} does not match VERSION {version!r}')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Release destination must be empty')
    copies = {}
    for asset, names in RELEASE_PACKAGES.items():
        folder = source / f'packages-{asset}'
        actual = {p.name for p in folder.iterdir() if p.is_file()} if folder.is_dir() else set()
        if actual != set(names):
            raise ValueError(f'{asset}: expected packages {names}, found {sorted(actual)}')
        for name in names:
            package = folder / name
            if not package.stat().st_size:
                raise ValueError(f'Empty release package: {package}')
            copies[name] = package

    for asset in (*RELEASE_PACKAGES, 'Windows-arm64'):
        folder = source / f'qa-{asset}'
        reports = {}
        for report in folder.rglob('qa-*.json'):
            if report.name in reports:
                raise ValueError(f'Duplicate QA report for {asset}: {report.name}')
            reports[report.name] = report
        required = {'qa-arm64.json'} if asset == 'Windows-arm64' else {'qa-offline.json', 'qa-interaction.json'}
        if asset != 'Windows-arm64':
            required.add('qa-licenses.json')
        if asset == 'Windows-x64':
            required.add('qa-windows-lifecycle.json')
        if not required.issubset(reports):
            raise ValueError(f'{asset}: missing QA reports {sorted(required - reports.keys())}')
        for name, path in reports.items():
            data = json.loads(path.read_text(encoding='utf-8'))
            if name in ('qa-interaction.json', 'qa-windows-lifecycle.json', 'qa-arm64.json', 'qa-licenses.json'):
                if data.get('passed') is not True:
                    raise ValueError(f'{asset}: failed QA report {name}')
            elif name in ('qa-offline.json', 'qa-speech.json', 'qa-speech-48k.json'):
                text = data.get('text', '')
                if not isinstance(text, str) or 'похвал' not in text.casefold():
                    raise ValueError(f'{asset}: invalid speech report {name}')
            # Prefix each report with its architecture, preserving every platform's evidence.
            copies[f'qa-{asset}-{name.removeprefix("qa-")}'] = path

    destination.mkdir(parents=True, exist_ok=True)
    for name, source_path in copies.items():
        shutil.copy2(source_path, destination / name)
    from tools.checksums import write
    write(destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--iscc', type=Path, help='Path to the Inno Setup 7 compiler')
    parser.add_argument('--offline', action='store_true', help='Use existing verified model files')
    parser.add_argument('--collect-artifacts', type=Path, help='Assemble downloaded, validated CI artifacts')
    parser.add_argument('--release-directory', type=Path, default=Path('release'))
    parser.add_argument('--release-tag', help='Must exactly match the VERSION file when collecting artifacts')
    args = parser.parse_args()
    version = (ROOT / 'VERSION').read_text(encoding='ascii').strip()
    if args.collect_artifacts:
        collect_release_artifacts(args.collect_artifacts, args.release_directory, version, args.release_tag)
        return
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
    embedded_tcl = tkinter.Tcl().eval('info library').startswith('//zipfs:')
    prepare(args.offline)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', 'EVoice.spec'],
                   cwd=ROOT, check=True)
    required = ['EVoice.exe', '_internal/_tkinter.pyd', '_internal/assets/evoice.ico',
                     '_internal/sherpa_onnx/lib/onnxruntime.dll',
                     '_internal/_sounddevice_data/portaudio-binaries/libportaudio64bit.dll']
    if not embedded_tcl:
        required.extend(('_internal/_tk_data/tk.tcl', '_internal/_tcl_data/init.tcl'))
    for relative in required:
        if not (APP / relative).is_file():
            raise RuntimeError('Missing from build: ' + relative)
    stage_assets(APP)
    subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'RemoteSigned', '-File',
                    str(ROOT / 'installer/stage_runtime.ps1'), '-AppDirectory', str(APP)], check=True)
    subprocess.run([str(Path(compiler).resolve()), f'/DAppVersion={version}',
                    str(ROOT / 'installer' / 'EVoice.iss')], cwd=ROOT, check=True)
    print(DIST / 'E-Voice-Setup-x64.exe')


if __name__ == '__main__':
    main()
