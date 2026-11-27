"""Check the installed release, archive its actual notices, and compare both Linux packages."""
import hashlib
import json
import os
import platform
import sys
import tarfile
from importlib import metadata
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model_files import verify_model


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def compare_tar(archive, files, links):
    regular, symbolic = {}, {}
    with tarfile.open(archive, 'r:gz') as package:
        for entry in package:
            name = entry.name.removeprefix('EVoice/')
            if entry.isfile():
                with package.extractfile(entry) as stream:
                    regular[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
            elif entry.issym():
                symbolic[name] = entry.linkname
            elif entry.islnk():
                regular[name] = regular[entry.linkname.removeprefix('EVoice/')]
    require(regular == files and symbolic == links, 'DEB and tar.gz application payloads differ')


def check():
    if sys.platform == 'win32':
        app = Path(os.environ['LOCALAPPDATA']) / 'Programs/E Voice'
        resources = app
    elif sys.platform == 'darwin':
        app = Path.home() / 'Applications/E Voice.app'
        resources = app / 'Contents/Resources'
    else:
        app = Path('/opt/e-voice')
        resources = app / '_internal'
    files, links = {}, {}
    for path in sorted(app.rglob('*')):
        name = path.relative_to(app).as_posix()
        require(not name.lower().endswith('-asio.dll'), f'Unexpected ASIO binary: {name}')
        if path.is_symlink():
            links[name] = os.readlink(path)
        elif path.is_file():
            files[name] = digest(path)
    verify_model(resources / 'models/gigaam-v3')
    notices = resources / 'licenses'
    required = [resources / 'LICENSE', resources / 'THIRD_PARTY.md', resources / 'models/gigaam-v3/LICENSE',
                notices / 'Python.txt', notices / 'GPL-3.0.txt', notices / 'PortAudio.txt',
                notices / 'ONNX-Runtime-ThirdPartyNotices.txt']
    for path in required:
        require(path.is_file() and path.stat().st_size, f'Missing notice: {path}')
    for local, official in [(required[0], ROOT / 'LICENSE'), (required[2], ROOT / 'models/gigaam-v3/LICENSE')]:
        require(local.read_text(encoding='utf-8') == official.read_text(encoding='utf-8'), 'MIT notice differs')
    report = {'platform': sys.platform, 'arch': platform.machine(), 'python': platform.python_version(),
              'version': (ROOT / 'VERSION').read_text().strip(), 'files': files, 'links': links}
    if sys.platform == 'win32':
        records = json.loads((notices / 'Microsoft-runtime-files.json').read_text(encoding='utf-8-sig'))
        expected = {'vcruntime140.dll', 'vcruntime140_1.dll'}
        require({r['name'] for r in records} == expected, 'Unexpected Microsoft runtime inventory')
        for record in records:
            require(digest(app / '_internal' / record['name']) == record['sha256'], 'Installed runtime differs')
        require((notices / 'windows-runtime-terms.txt').is_file(), 'Missing Windows conditions')
        require(not any(Path(n).name.lower().startswith(('api-ms-win-', 'ucrtbase.dll')) for n in files),
                'Windows system CRT must come from Windows')
        require(any(n.endswith('/libportaudio64bit.dll') for n in files), 'Missing standard Windows audio driver')
        report['runtime'] = records
    elif sys.platform == 'darwin':
        require(any(n.endswith('/libportaudio.dylib') for n in files), 'Missing Core Audio library')
        require(not any(n.lower().endswith('.dll') for n in files), 'Unexpected Windows DLL in Mac package')
    else:
        archive, = (ROOT / 'dist').glob('E-Voice-Linux-*.tar.gz')
        compare_tar(archive, files, links)
        report['ubuntu_packages'] = (notices / 'system/packages.tsv').read_text().splitlines()
        require(report['ubuntu_packages'], 'Empty package/source inventory')
        expected_alsa = {'x86_64': '887ee61e5644956d5f29121ebee8af1b58904ae7a2cb0388fd497f8fc2303914',
                         'aarch64': '719d17f13b24f7efb4a8ad20e21705ed6da001b57a872d18c53eac430316e9e0'}
        require(expected_alsa[platform.machine()] in files.values(), 'Unknown vendored ALSA build')
        report['tar_matches_deb'] = True
    input_libs = ['pystray'] if sys.platform == 'win32' else ['pynput']
    if sys.platform == 'linux':
        input_libs.append('python-xlib')
    report['input_libraries'] = {name: metadata.version(name) for name in input_libs}
    report['passed'] = True
    out = ROOT / 'build'
    (out / 'qa-licenses.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    with ZipFile(out / 'platform-notices.zip', 'w', ZIP_DEFLATED) as archive:
        for path in sorted(notices.rglob('*')):
            if path.is_file():
                archive.write(path, 'licenses/' + path.relative_to(notices).as_posix())
        for path in required[:3]:
            archive.write(path, path.relative_to(resources).as_posix())
    print(f'Checked {len(files)} installed files on {report["platform"]} {report["arch"]}')


if __name__ == '__main__':
    check()
