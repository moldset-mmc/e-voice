import json
import subprocess
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from tools import source_companion


class SourceCompanionTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.artifacts = self.root / 'artifacts'
        self.release = self.root / 'release'
        self.release.mkdir()
        self.version = (source_companion.ROOT / 'VERSION').read_text().strip()

    @staticmethod
    def write_source(directory, version, content=b'archive member'):
        directory.mkdir(parents=True, exist_ok=True)
        member = directory / 'example.tar.xz'
        member.write_bytes(content)
        (directory / 'example.dsc').write_text(
            f'Source: example\nVersion: {version}\nChecksums-Sha256:\n'
            f' {source_companion.sha256(member)} {len(content)} {member.name}\n', encoding='utf-8')
        return member

    def prepare_matrix(self):
        (self.release / 'SHA256SUMS.txt').write_text('installer checksum\n', encoding='utf-8')
        members = []
        for platform in source_companion.PLATFORMS:
            report = {'version': self.version, 'passed': True, 'python': '3.14.10'}
            report['input_libraries'] = {'pystray': '0.19.5'} if platform == 'Windows-x64' else {'pynput': '1.8.2'}
            if platform.startswith('Linux'):
                version = '1:1.0-1' if platform == 'Linux-amd64' else '1:1.0-2'
                report.update(ubuntu_packages=[f'example-bin\texample\t{version}'], tar_matches_deb=True)
                report['input_libraries']['python-xlib'] = '0.33'
                artifact = self.artifacts / f'ubuntu-sources-{platform}'
                members.append(self.write_source(artifact / source_companion.ubuntu_source_path('example', version),
                                                 version))
            path = self.release / f'qa-{platform}-licenses.json'
            path.write_text(json.dumps(report), encoding='utf-8')
            notices = self.artifacts / f'notices-{platform}'
            notices.mkdir(parents=True)
            with ZipFile(notices / 'platform-notices.zip', 'w') as archive:
                archive.writestr('notice.txt', platform)
        return members

    def test_assembly_keeps_exact_source_versions_from_both_linux_builds(self):
        self.prepare_matrix()
        with patch.object(source_companion, 'pinned_sources'), \
                patch.object(source_companion, 'download_ubuntu_source') as download:
            result = source_companion.assemble(self.artifacts, self.release)
        download.assert_not_called()
        with ZipFile(result) as archive:
            sums = archive.read('SOURCE-SHA256SUMS.txt').decode()
            for version in ('1:1.0-1', '1:1.0-2'):
                source_path = source_companion.ubuntu_source_path('example', version).as_posix()
                name = f'sources/{source_path}/example.tar.xz'
                self.assertEqual(archive.read(name), b'archive member')
                self.assertIn(name, sums)

    def test_missing_and_corrupted_artifact_members_block_assembly(self):
        _, member = self.prepare_matrix()
        member.unlink()
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                if corrupt:
                    member.write_bytes(b'changed member')
                with self.assertRaisesRegex(ValueError, 'Incorrect Debian source member'):
                    source_companion.assemble(self.artifacts, self.release)
        self.assertEqual(list(self.release.glob('*.zip')), [])

    def test_native_collection_retries_superseded_version_with_signed_snapshot(self):
        inventory = self.root / 'packages.tsv'
        inventory.write_text('example-bin\texample\t1:1.0-1\n', encoding='utf-8')
        destination = self.root / 'sources'
        calls = []

        def apt(command, *, check, cwd=None):
            calls.append(command[-1])
            if 'Dir::Etc::sourceparts=-' not in command:
                self.assertTrue(check)
                raise subprocess.CalledProcessError(100, command)
            if command[-1] == 'update':
                sources = next(option.split('=', 1)[1] for option in command if option.startswith('Dir::Etc::sourcelist='))
                config = Path(sources).read_text(encoding='ascii')
                self.assertIn('signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg', config)
                self.assertIn('https://snapshot.ubuntu.com/ubuntu/20240427T185943Z/ jammy-updates main', config)
            else:
                self.write_source(cwd, '1:1.0-1')

        with patch.object(source_companion, 'ubuntu_snapshots', return_value=[('20240427T185943Z', 'jammy-updates', 'main')]), \
                patch.object(source_companion.subprocess, 'run', side_effect=apt):
            source_companion.collect_ubuntu_sources(inventory, destination)
        self.assertEqual(calls, ['example=1:1.0-1', 'update', 'example=1:1.0-1'])
        package = destination / source_companion.ubuntu_source_path('example', '1:1.0-1')
        self.assertTrue((package / 'example.dsc').is_file())

    def test_historical_lookup_retries_a_network_timeout(self):
        entries = [{'pocket': 'Updates', 'component_name': 'main', 'date_published': '2024-04-01T00:00:00Z',
                    'date_superseded': '2024-05-01T00:00:00Z', 'date_removed': None}]
        response = BytesIO(json.dumps({'entries': entries}).encode())
        with patch.object(source_companion, 'urlopen', side_effect=[TimeoutError(), response]) as get, \
                patch.object(source_companion.time, 'sleep'):
            snapshots = list(source_companion.ubuntu_snapshots('example', '1:1.0-1'))
        self.assertEqual(snapshots, [('20240416T000000Z', 'jammy-updates', 'main')])
        self.assertEqual(get.call_count, 2)
