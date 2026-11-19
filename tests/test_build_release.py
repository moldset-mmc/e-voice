"""Release assembly and CI harness regressions; no installed app, GUI or hooks."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import build_release
from installer import check_windows

ROOT = Path(__file__).resolve().parents[1]


class ReleaseAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'artifacts'
        self.destination = self.root / 'release'
        for asset, names in build_release.RELEASE_PACKAGES.items():
            folder = self.source / f'packages-{asset}'
            folder.mkdir(parents=True)
            for name in names:
                (folder / name).write_bytes(name.encode('ascii'))
            self.report(asset, 'qa-offline.json', {'text': f'похвала {asset}'})
            self.report(asset, 'qa-interaction.json', {'passed': True, 'platform': asset})
        self.report('Windows-x64', 'qa-windows-lifecycle.json', {'passed': True})
        self.report('Windows-arm64', 'qa-arm64.json', {'passed': True})

    def report(self, asset, name, data):
        folder = self.source / f'qa-{asset}'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def collect(self, tag='v9.8.7'):
        build_release.collect_release_artifacts(self.source, self.destination, '9.8.7', tag)

    def test_complete_matrix_preserves_each_platform_report_and_checksums(self):
        self.collect()
        for asset in build_release.RELEASE_PACKAGES:
            report = self.destination / f'qa-{asset}-interaction.json'
            self.assertEqual(json.loads(report.read_text())['platform'], asset)
        self.assertTrue((self.destination / 'qa-Windows-x64-windows-lifecycle.json').is_file())
        self.assertTrue((self.destination / 'qa-Windows-arm64-arm64.json').is_file())
        sums = (self.destination / 'SHA256SUMS.txt').read_text().splitlines()
        self.assertEqual(len(sums), 7)
        self.assertEqual(len({line.split('  ')[1] for line in sums}), 7)

    def test_missing_architecture_is_not_silently_released(self):
        missing = self.source / 'packages-Linux-arm64/E-Voice-Linux-arm64.deb'
        missing.unlink()
        with self.assertRaisesRegex(ValueError, 'Linux-arm64: expected packages'):
            self.collect()
        self.assertFalse(self.destination.exists())

    def test_extra_or_empty_package_rejected(self):
        package = self.source / 'packages-Windows-x64/E-Voice-Setup-x64.exe'
        package.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'Empty release package'):
            self.collect()
        package.write_bytes(b'installer')
        (package.parent / 'stale.exe').write_bytes(b'old')
        with self.assertRaisesRegex(ValueError, 'Windows-x64: expected packages'):
            self.collect()

    def test_bad_version_tag_rejected_before_copying(self):
        for tag in ('v1.1.2', None, '9.8.7'):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, 'does not match VERSION'):
                self.collect(tag)
        self.assertFalse(self.destination.exists())

    def test_reusing_release_directory_never_overwrites_files(self):
        self.destination.mkdir()
        existing = self.destination / 'keep.txt'
        existing.write_text('existing work')
        with self.assertRaisesRegex(ValueError, 'must be empty'):
            self.collect()
        self.assertEqual(existing.read_text(), 'existing work')

    def test_missing_failed_and_non_boolean_qa_are_rejected(self):
        for value in (False, 'true', 1, None):
            self.report('Windows-arm64', 'qa-arm64.json', {'passed': value})
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'failed QA report'):
                self.collect()
        self.report('Windows-arm64', 'qa-arm64.json', {'passed': True}).unlink()
        with self.assertRaisesRegex(ValueError, 'missing QA reports'):
            self.collect()
        self.assertFalse(self.destination.exists())

    def test_duplicate_nested_report_rejected_not_overwritten(self):
        duplicate = self.source / 'qa-Linux-amd64/nested'
        duplicate.mkdir()
        (duplicate / 'qa-interaction.json').write_text('{"passed": true}')
        with self.assertRaisesRegex(ValueError, 'Duplicate QA report'):
            self.collect()

    def test_incomplete_speech_evidence_rejected(self):
        self.report('macOS-arm64', 'qa-offline.json', {'text': ''})
        with self.assertRaisesRegex(ValueError, 'invalid speech report'):
            self.collect()


class WindowsHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.exe = self.directory / 'EVoice.exe'
        self.exe.write_bytes(b'not an executable; tests must mock Popen')

    def test_host_guard_refuses_local_self_hosted_and_non_windows(self):
        cases = [('win32', {}), ('win32', {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'self-hosted'}),
                 ('linux', {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'})]
        for platform, environment in cases:
            with self.subTest(platform=platform, environment=environment), \
                    patch.object(check_windows.sys, 'platform', platform), \
                    patch.dict(os.environ, environment, clear=True), \
                    patch.object(check_windows.subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(RuntimeError, 'only on GitHub-hosted Windows'):
                    check_windows.check_installed(self.exe, self.directory)
                launch.assert_not_called()

    def test_host_guard_accepts_hosted_windows_without_starting_anything(self):
        with patch.object(check_windows.sys, 'platform', 'win32'), \
                patch.dict(os.environ, GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted'):
            check_windows.require_hosted_windows()

    def run_fake(self, process, job):
        with patch.object(check_windows, 'require_hosted_windows'), \
                patch.object(check_windows, 'WindowsJob', return_value=job), \
                patch.object(check_windows.subprocess, 'Popen', return_value=process) as launch:
            status = check_windows.run_checked(self.exe, ['--interaction-test', 'result.json'],
                                               self.directory, self.directory / 'stdout.log', timeout=3)
            return status, launch.call_args

    def test_success_requires_process_exit_and_empty_job(self):
        process = Mock(pid=42)
        process.wait.return_value = process.poll.return_value = 0
        job = Mock()
        job.active_processes.return_value = 0
        result, call = self.run_fake(process, job)
        self.assertEqual(result, {'exit_code': 0, 'remaining_processes': 0})
        job.attach.assert_called_once_with(process)
        job.wait_empty.assert_called_once_with()
        job.close.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3)
        process.kill.assert_not_called()
        self.assertEqual(call.kwargs['env']['EVOICE_DATA_DIR'], str(self.directory))
        self.assertIsNot(call.kwargs['stdout'], subprocess.PIPE)

    def test_timeout_kills_job_and_does_not_accept_preexisting_passed_json(self):
        (self.directory / 'result.json').write_text('{"passed": true}')
        process = Mock(pid=42)
        process.wait.side_effect = [subprocess.TimeoutExpired('EVoice', 3), 1]
        process.poll.return_value = None
        job = Mock()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_fake(process, job)
        job.close.assert_called_once_with()
        # The job terminates its whole tree; reap it without racing a second kill.
        process.kill.assert_not_called()
        self.assertEqual(process.wait.call_count, 2)
        job.wait_empty.assert_not_called()

    def test_orphan_child_fails_even_after_main_process_exits_zero(self):
        process = Mock(pid=42)
        process.wait.return_value = process.poll.return_value = 0
        job = Mock()
        job.wait_empty.side_effect = RuntimeError('left child processes')
        with self.assertRaisesRegex(RuntimeError, 'child processes'):
            self.run_fake(process, job)
        job.close.assert_called_once_with()

    def test_assignment_failure_still_reaps_main_process(self):
        process = Mock(pid=42)
        process.poll.return_value = None
        job = Mock()
        job.attach.side_effect = OSError('cannot assign job')
        with self.assertRaisesRegex(OSError, 'cannot assign job'):
            self.run_fake(process, job)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=10)
        job.close.assert_called_once_with()

    def test_nonzero_exit_is_not_reported_as_success(self):
        process = Mock(pid=42)
        process.wait.return_value = process.poll.return_value = 7
        job = Mock()
        with self.assertRaisesRegex(RuntimeError, 'exit.*code 7'):
            self.run_fake(process, job)
        job.close.assert_called_once_with()

    def test_timeout_persists_failure_summary_and_isolated_profile(self):
        with patch.object(check_windows, 'require_hosted_windows'), \
                patch.object(check_windows, 'make_48k_fixture'), \
                patch.object(check_windows, 'run_checked', side_effect=subprocess.TimeoutExpired('EVoice', 200)):
            with self.assertRaises(subprocess.TimeoutExpired):
                check_windows.check_installed(self.exe, self.directory)
        report = json.loads((self.directory / 'qa-windows-lifecycle.json').read_text())
        self.assertIs(report['passed'], False)
        self.assertIn('TimeoutExpired', report['error'])
        self.assertEqual(len(list((self.directory / 'windows-qa').glob('profile-*'))), 1)

    def test_each_phase_is_required_before_lifecycle_passes(self):
        def fake_run(exe, arguments, profile, log):
            data = ({'passed': True, **dict.fromkeys(check_windows.REQUIRED_INTERACTION_CHECKS, True)}
                    if '--interaction-test' in arguments else {'text': 'похвала ' * 20})
            arguments[-1].write_text(json.dumps(data), encoding='utf-8')
            return {'exit_code': 0, 'remaining_processes': 0}
        with patch.object(check_windows, 'require_hosted_windows'), \
                patch.object(check_windows, 'make_48k_fixture'), \
                patch.object(check_windows, 'run_checked', side_effect=fake_run) as launch:
            check_windows.check_installed(self.exe, self.directory)
        report = json.loads((self.directory / 'qa-windows-lifecycle.json').read_text())
        self.assertIs(report['passed'], True)
        self.assertEqual(set(report['checks']), {'speech', 'speech-48k', 'interaction'})
        self.assertEqual(launch.call_count, 3)

    def test_invalid_interaction_or_transcript_is_not_success(self):
        for name, data in [('interaction', {'passed': 1}), ('interaction', {}),
                           ('speech', {'text': 'похвала'}), ('speech-48k', {'text': None})]:
            with self.subTest(name=name, data=data), self.assertRaises(RuntimeError):
                check_windows.validate_result(name, data)

    def test_interaction_requires_every_tray_default_and_exit_check(self):
        expected = {'floating_hidden_by_default', 'mouse_hook_absent_by_default',
                    'tray_left_click_opens_settings', 'second_instance_opens_settings',
                    'poll_survives_cursor_denial', 'exit_requested_via_event_queue'}
        self.assertEqual(set(check_windows.REQUIRED_INTERACTION_CHECKS), expected)
        data = {'passed': True, **dict.fromkeys(expected, True)}
        check_windows.validate_result('interaction', data)
        for key in expected:
            missing = dict(data)
            del missing[key]
            with self.subTest(key=key, missing=True), self.assertRaisesRegex(RuntimeError, key):
                check_windows.validate_result('interaction', missing)
            for value in (False, 1, 'true', None):
                invalid = dict(data, **{key: value})
                with self.subTest(key=key, value=value), self.assertRaisesRegex(RuntimeError, key):
                    check_windows.validate_result('interaction', invalid)

    def test_non_object_reports_are_rejected(self):
        for name in ('speech', 'interaction'):
            for data in (None, [], 'passed'):
                with self.subTest(name=name, data=data), self.assertRaisesRegex(RuntimeError, 'JSON object'):
                    check_windows.validate_result(name, data)

    def test_job_setup_enables_tree_cleanup_and_closes_on_configuration_failure(self):
        api = Mock(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=0x2000)
        api.QueryInformationJobObject.return_value = {'BasicLimitInformation': {'LimitFlags': 0}}
        handle = api.CreateJobObject.return_value
        with patch.object(check_windows, 'require_hosted_windows'), \
                patch.dict('sys.modules', win32job=api), \
                patch.object(check_windows, 'uuid4', side_effect=[Mock(hex='a' * 32), Mock(hex='b' * 32)]):
            job = check_windows.WindowsJob()
            api.CreateJobObject.assert_called_once_with(None, 'Local\\EVoice-QA-' + 'a' * 32)
            limits = api.SetInformationJobObject.call_args.args[2]
            self.assertEqual(limits['BasicLimitInformation']['LimitFlags'], 0x2000)
            job.close()
            handle.Close.assert_called_once_with()
            handle.Close.reset_mock()
            api.SetInformationJobObject.side_effect = OSError('job limit failed')
            with self.assertRaisesRegex(OSError, 'job limit failed'):
                check_windows.WindowsJob()
            api.CreateJobObject.assert_called_with(None, 'Local\\EVoice-QA-' + 'b' * 32)
            self.assertEqual(api.CreateJobObject.call_count, 2)
            handle.Close.assert_called_once_with()

    def test_empty_job_wait_is_bounded(self):
        job = check_windows.WindowsJob.__new__(check_windows.WindowsJob)
        job.active_processes = Mock(return_value=1)
        with patch.object(check_windows.time, 'monotonic', side_effect=[0, 11]):
            with self.assertRaisesRegex(RuntimeError, 'left child processes'):
                job.wait_empty(timeout=10)
        job.active_processes.return_value = 0
        job.wait_empty()


class HostedWindowsJobSmokeTests(unittest.TestCase):
    def test_native_empty_job_create_query_limits_and_close(self):
        # Run before packaging on hosted Windows, never use native APIs locally.
        try:
            check_windows.require_hosted_windows()
        except RuntimeError as error:
            self.skipTest(str(error))
        job = check_windows.WindowsJob()
        self.addCleanup(job.close)
        self.assertEqual(job.active_processes(), 0)
        info = job.api.QueryInformationJobObject(job.handle, job.api.JobObjectExtendedLimitInformation)
        self.assertTrue(info['BasicLimitInformation']['LimitFlags'] & job.api.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        job.wait_empty(timeout=0)
        job.close()
        self.assertIsNone(job.handle)


class WorkflowContractTests(unittest.TestCase):
    def test_release_requires_lint_native_and_arm_checks(self):
        workflow = (ROOT / '.github/workflows/desktop.yml').read_text(encoding='utf-8')
        release = workflow.split('\n  release:\n')[1].split('\n  windows-arm-compatibility:\n')[0]
        self.assertIn('needs: [lint, native, windows-arm-compatibility]', release)
        self.assertIn('--collect-artifacts package', release)
        self.assertNotIn('find package', release)

    def test_windows_qa_is_guarded_and_diagnostics_survive_failures(self):
        workflow = (ROOT / '.github/workflows/desktop.yml').read_text(encoding='utf-8')
        self.assertIn('python installer/check_windows.py $exe', workflow)
        self.assertIn('name: qa-${{ matrix.asset }}', workflow)
        self.assertIn('name: packages-${{ matrix.asset }}', workflow)
        self.assertGreaterEqual(workflow.count('if: always()'), 2)
        self.assertIn('build/windows-qa/', workflow)
        self.assertIn('package/E-Voice-Setup-x64.exe', workflow)
        self.assertNotIn('package/dist/E-Voice-Setup-x64.exe', workflow)


if __name__ == '__main__':
    unittest.main()
