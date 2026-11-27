"""Publish matching source and notices alongside the binary packages, outside the installers."""
import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ('Windows-x64', 'macOS-arm64', 'macOS-x86_64', 'Linux-amd64', 'Linux-arm64')
UBUNTU_SERIES = 'jammy'
UBUNTU_KEYRING = '/usr/share/keyrings/ubuntu-archive-keyring.gpg'
LAUNCHPAD = 'https://api.launchpad.net/1.0/ubuntu'
SNAPSHOT_START = datetime(2023, 3, 1, tzinfo=UTC)


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


def ubuntu_packages(rows):
    packages = set()
    for row in rows:
        _, source, version = row.split('\t')
        if not re.fullmatch(r'[a-z0-9][a-z0-9+.-]+', source) or not re.fullmatch(
                r'(?:[0-9]+:)?[0-9][a-zA-Z0-9.+:~\-]*', version):
            raise ValueError(f'Invalid Ubuntu source package: {source}={version}')
        packages.add((source, version))
    if not packages:
        raise ValueError('Missing Ubuntu source package inventory')
    return sorted(packages)


def ubuntu_source_path(source, version):
    return Path('ubuntu', source, version.replace(':', '_'))


def validate_ubuntu_source(directory, source, version):
    descriptors = list(directory.glob('*.dsc'))
    if len(descriptors) != 1:
        raise ValueError(f'Expected one Debian source descriptor for {source}={version} in {directory}')
    descriptor, = descriptors
    metadata = '\n' + descriptor.read_text(encoding='utf-8')
    if f'\nSource: {source}\n' not in metadata or f'\nVersion: {version}\n' not in metadata:
        raise ValueError(f'Unexpected source package: {descriptor.name}')
    _, _, checksums = metadata.partition('\nChecksums-Sha256:\n')
    checked = {descriptor.name}
    for line in checksums.splitlines():
        if not line.startswith(' '):
            break
        digest, size, filename = line.split()
        if filename in checked or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.+~\-]*', filename):
            raise ValueError(f'Invalid Debian source member: {filename}')
        path = directory / filename
        if not path.is_file() or path.stat().st_size != int(size) or sha256(path) != digest:
            raise ValueError(f'Incorrect Debian source member: {filename}')
        checked.add(filename)
    if len(checked) == 1 or checked != {path.name for path in directory.iterdir()}:
        raise ValueError(f'Incomplete Debian source package: {descriptor.name}')
    return descriptor


def ubuntu_snapshots(source, version):
    query = urlencode({'ws.op': 'getPublishedSources', 'source_name': source, 'version': version,
                       'exact_match': 'true', 'distro_series': f'{LAUNCHPAD}/{UBUNTU_SERIES}'})
    url = f'{LAUNCHPAD}/+archive/primary?{query}'
    now = datetime.now(UTC)
    suites = {'Release': UBUNTU_SERIES, 'Updates': f'{UBUNTU_SERIES}-updates',
              'Security': f'{UBUNTU_SERIES}-security', 'Backports': f'{UBUNTU_SERIES}-backports'}
    while url:
        with urlopen(url, timeout=30) as reply:
            records = json.load(reply)
        for entry in records['entries']:
            suite = suites.get(entry['pocket'])
            component = entry['component_name']
            published = entry['date_published']
            if not suite or component not in ('main', 'restricted', 'universe', 'multiverse') or not published:
                continue
            start = max(datetime.fromisoformat(published), SNAPSHOT_START)
            retired = [datetime.fromisoformat(entry[key]) for key in ('date_superseded', 'date_removed')
                       if entry[key]]
            end = min([now, *retired])
            if start < end:
                # Avoid the edges of the publication window while archive indexes were changing.
                date = start + (end - start) / 2
                yield date.strftime('%Y%m%dT%H%M%SZ'), suite, component
        url = records.get('next_collection_link')


def download_ubuntu_source(directory, source, version, options=()):
    # APT verifies the .dsc and each source member against the signed Ubuntu index.
    with tempfile.TemporaryDirectory(dir=directory.parent) as work:
        staging = Path(work) / 'source'
        staging.mkdir()
        subprocess.run(['apt-get', '-qq', '-o', 'Acquire::Retries=3', *options, 'source', '--download-only',
                        '--only-source', f'{source}={version}'], cwd=staging, check=True)
        validate_ubuntu_source(staging, source, version)
        staging.rename(directory)


def download_historical_ubuntu_source(directory, source, version):
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        apt_sources = root / 'sources.list'
        options = ('-o', f'Dir::Etc::sourcelist={apt_sources}', '-o', 'Dir::Etc::sourceparts=-',
                   '-o', f'Dir::State::lists={root / "lists"}',
                   '-o', 'Dir::Cache::pkgcache=', '-o', 'Dir::Cache::srcpkgcache=')
        for snapshot, suite, component in ubuntu_snapshots(source, version):
            # Historical indexes retain their signatures after Valid-Until has elapsed.
            apt_sources.write_text(
                f'deb-src [signed-by={UBUNTU_KEYRING} check-valid-until=no] '
                f'https://snapshot.ubuntu.com/ubuntu/{snapshot}/ {suite} {component}\n', encoding='ascii')
            (root / 'lists' / 'partial').mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run(['apt-get', '-qq', '-o', 'Acquire::Retries=3', '-o', 'APT::Update::Error-Mode=any',
                                *options, 'update'], check=True)
                download_ubuntu_source(directory, source, version, options)
                return
            except subprocess.CalledProcessError:
                shutil.rmtree(root / 'lists')
    raise ValueError(f'No signed Ubuntu snapshot supplied {source}={version}')


def collect_ubuntu_sources(inventory, destination):
    rows = inventory.read_text(encoding='utf-8').splitlines()
    for source, version in ubuntu_packages(rows):
        directory = (destination / ubuntu_source_path(source, version)).resolve()
        directory.parent.mkdir(parents=True, exist_ok=True)
        if directory.exists():
            validate_ubuntu_source(directory, source, version)
            continue
        try:
            download_ubuntu_source(directory, source, version)
        except subprocess.CalledProcessError:
            download_historical_ubuntu_source(directory, source, version)


def copy_ubuntu_sources(artifact, destination, rows):
    for source, version in ubuntu_packages(rows):
        relative = ubuntu_source_path(source, version)
        descriptor = validate_ubuntu_source(artifact / relative, source, version)
        target = destination / relative
        if target.exists():
            existing = target / descriptor.name
            if not existing.is_file() or sha256(existing) != sha256(descriptor):
                raise ValueError(f'Conflicting Ubuntu source artifacts for {source}={version}')
        else:
            shutil.copytree(descriptor.parent, target)


def assemble(artifacts, release):
    version = (ROOT / 'VERSION').read_text().strip()
    final = release / f'E-Voice-{version}-Licenses-and-Sources.zip'
    with tempfile.TemporaryDirectory(dir=release.parent) as work:
        temp = Path(work)
        sources = temp / 'sources'
        sources.mkdir()
        output = temp / final.name
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
                    copy_ubuntu_sources(artifacts / f'ubuntu-sources-{platform}', sources, data['ubuntu_packages'])
                if data['input_libraries'] != expected:
                    raise ValueError(f'Input library sources need updating for {platform}')
                archive.write(report, 'inventory/' + report.name)
                with ZipFile(artifacts / f'notices-{platform}/platform-notices.zip') as notices:
                    for name in notices.namelist():
                        archive.writestr(f'notices/{platform}/{name}', notices.read(name))
            pinned_sources(sources)
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
    parser.add_argument('--ubuntu-inventory', type=Path,
                        help='Collect Ubuntu sources from this packages.tsv into the output directory')
    parser.add_argument('artifacts', type=Path)
    parser.add_argument('release', type=Path, nargs='?')
    args = parser.parse_args()
    if args.ubuntu_inventory and args.release is None:
        collect_ubuntu_sources(args.ubuntu_inventory, args.artifacts)
    elif not args.ubuntu_inventory and args.release is not None:
        print(assemble(args.artifacts, args.release))
    else:
        parser.error('Use ARTIFACTS RELEASE_DIR or --ubuntu-inventory PACKAGES_TSV OUTPUT_DIR')
