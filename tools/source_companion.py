"""Publish matching source and notices alongside the binary packages, outside the installers."""
import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ('Windows-x64', 'macOS-arm64', 'macOS-x86_64', 'Linux-amd64', 'Linux-arm64')


def sha256(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def pinned_sources(destination):
    with (ROOT / 'licenses/source-inputs.tsv').open(encoding='utf-8') as manifest:
        for row in csv.DictReader(manifest, delimiter='\t'):
            target = destination / row['file']
            target.parent.mkdir(parents=True, exist_ok=True)
            for attempt in range(3):
                try:
                    with urlopen(row['official_url'], timeout=90) as reply, target.open('wb') as output:
                        shutil.copyfileobj(reply, output)
                    break
                except OSError:
                    if attempt == 2:
                        raise
                    time.sleep(1 + attempt)
            if target.stat().st_size != int(row['bytes']) or sha256(target) != row['sha256']:
                raise ValueError('Source checksum mismatch: ' + row['file'])


def ubuntu_sources(destination, packages):
    for source, version in sorted(packages):
        # apt validates the download against the signed Ubuntu source index.
        directory = destination / 'ubuntu' / source / version.replace(':', '_')
        directory.mkdir(parents=True)
        subprocess.run(['apt-get', '-qq', '-o', 'Acquire::Retries=3', 'source', '--download-only',
                        '--only-source', f'{source}={version}'], cwd=directory, check=True)
        descriptor, = directory.glob('*.dsc')
        metadata = descriptor.read_text(encoding='utf-8')
        if f'\nSource: {source}\n' not in metadata or f'\nVersion: {version}\n' not in metadata:
            raise ValueError(f'Unexpected source package: {descriptor.name}')
        rows = metadata.split('\nChecksums-Sha256:\n', 1)[1].splitlines()
        checked = set()
        for line in rows:
            if not line.startswith(' '):
                break
            digest, size, filename = line.split()
            path = directory / filename
            if path.stat().st_size != int(size) or sha256(path) != digest:
                raise ValueError('Incorrect Debian source member: ' + filename)
            checked.add(filename)
        if not checked or checked != {p.name for p in directory.iterdir() if p != descriptor}:
            raise ValueError('Incomplete Debian source download: ' + descriptor.name)


def assemble(artifacts, release):
    version = (ROOT / 'VERSION').read_text().strip()
    final = release / f'E-Voice-{version}-Licenses-and-Sources.zip'
    with tempfile.TemporaryDirectory(dir=release.parent) as work:
        temp = Path(work)
        sources = temp / 'sources'
        sources.mkdir()
        output = temp / final.name
        packages = set()
        with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
            for platform in PLATFORMS:
                report = release / f'qa-{platform}-licenses.json'
                data = json.loads(report.read_text('utf-8'))
                if data.get('passed') is not True or data['version'] != version or data['python'] != '3.14.10':
                    raise ValueError(f'Incorrect runtime or incomplete audit: {platform}')
                expected = {'pystray': '0.19.5'} if platform.startswith('Windows') else {'pynput': '1.8.2'}
                if platform.startswith('Linux'):
                    expected['python-xlib'] = '0.33'
                    if not data.get('tar_matches_deb') or not data.get('ubuntu_packages'):
                        raise ValueError(f'Incomplete Linux package audit: {platform}')
                    for item in data['ubuntu_packages']:
                        _, name, revision = item.split('\t')
                        packages.add((name, revision))
                if data['input_libraries'] != expected:
                    raise ValueError(f'Input library sources need updating for {platform}')
                archive.write(report, 'inventory/' + report.name)
                with ZipFile(artifacts / f'notices-{platform}/platform-notices.zip') as notices:
                    for name in notices.namelist():
                        archive.writestr(f'notices/{platform}/{name}', notices.read(name))
            pinned_sources(sources)
            ubuntu_sources(sources, packages)
            app_source = sources / f'E-Voice-{version}-source.tar.gz'
            subprocess.run(['git', 'archive', '--format=tar.gz', '--prefix=e-voice/', '-o', str(app_source.resolve()),
                            'HEAD'], cwd=ROOT, check=True)
            archive.write(ROOT / 'licenses/source-inputs.tsv', 'sources/pinned-urls.tsv')
            sums = []
            for path in sorted(sources.rglob('*')):
                if path.is_file():
                    name = 'sources/' + path.relative_to(sources).as_posix()
                    sums.append(f'{sha256(path)}  {name}\n')
                    archive.write(path, name, compress_type=ZIP_STORED)
            archive.writestr('SOURCE-SHA256SUMS.txt', ''.join(sums))
            archive.write(release / 'SHA256SUMS.txt', 'INSTALLER-SHA256SUMS.txt')
            archive.write(ROOT / 'THIRD_PARTY.md', 'THIRD_PARTY.md')
            archive.write(ROOT / 'README.md', 'README.md')
        with ZipFile(output) as archive:
            if archive.testzip() is not None:
                raise ValueError('Corrupt source companion')
        output.replace(final)
    return final


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifacts', type=Path)
    parser.add_argument('release', type=Path)
    args = parser.parse_args()
    print(assemble(args.artifacts, args.release))
