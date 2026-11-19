"""Installed Windows QA, deliberately restricted to disposable GitHub-hosted runners.

Never import or start the application on a developer's workstation. Unit tests mock
all process/job APIs. Keep profiles/logs on failure, and require the OS process tree
to exit: a JSON report written before Application.exit() is not proof of shutdown.
"""
from array import array
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_INTERACTION_CHECKS = (
    'floating_hidden_by_default',
    'mouse_hook_absent_by_default',
    'tray_left_click_opens_settings',
    'second_instance_opens_settings',
    'poll_survives_cursor_denial',
    'exit_requested_via_event_queue',
)


def require_hosted_windows():
    if (sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
        raise RuntimeError('Installed Windows QA is allowed only on GitHub-hosted Windows runners')


class WindowsJob:
    """Own the launched process tree, including the recognition subprocess."""

    def __init__(self):
        require_hosted_windows()
        import win32job
        self.api = win32job
        self.handle = win32job.CreateJobObject(None, None)
        try:
            info = win32job.QueryInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation)
            info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation, info)
        except BaseException:
            self.close()
            raise

    def attach(self, process):
        import win32api
        import win32con
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
                                      False, process.pid)
        try:
            self.api.AssignProcessToJobObject(self.handle, handle)
        finally:
            handle.Close()

    def active_processes(self):
        return self.api.QueryInformationJobObject(
            self.handle, self.api.JobObjectBasicAccountingInformation)['ActiveProcesses']

    def wait_empty(self, timeout=10):
        deadline = time.monotonic() + timeout
        while self.active_processes():
            if time.monotonic() >= deadline:
                raise RuntimeError('Installed application left child processes after exit')
            time.sleep(0.05)

    def close(self):
        if self.handle is not None:
            self.handle.Close()  # KILL_ON_JOB_CLOSE also cleans up timeout/orphan failures.
            self.handle = None


def run_checked(exe, arguments, profile, log, timeout=200):
    require_hosted_windows()
    env = dict(os.environ, EVOICE_DATA_DIR=str(profile))
    job = WindowsJob()
    process = None
    attached = False
    try:
        with log.open('wb') as stream:
            process = subprocess.Popen([str(exe), *map(str, arguments)], cwd=profile, env=env,
                                       stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
            job.attach(process)
            attached = True
            code = process.wait(timeout=timeout)
            if code:
                raise RuntimeError(f'Installed application exited with code {code}; see {log}')
            job.wait_empty()
            return {'exit_code': code, 'remaining_processes': job.active_processes()}
    finally:
        job.close()
        # Also cover assignment failure, before the process belonged to our job.
        if process is not None and process.poll() is None:
            if not attached:
                process.kill()
            process.wait(timeout=10)


def validate_result(name, data):
    if not isinstance(data, dict):
        raise RuntimeError(f'{name}: expected a JSON object')
    if name.startswith('speech'):
        text = data.get('text', '')
        if not isinstance(text, str) or 'похвал' not in text.casefold() or len(text) <= 100:
            raise RuntimeError(f'{name}: expected full Russian fixture transcript')
    else:
        if data.get('passed') is not True:
            raise RuntimeError(f'{name}: installed interaction checks failed: {data}')
        failed = [key for key in REQUIRED_INTERACTION_CHECKS if data.get(key) is not True]
        if failed:
            raise RuntimeError(f'{name}: missing or failed interaction checks: {", ".join(failed)}')


def make_48k_fixture(audio, destination):
    with wave.open(str(audio), 'rb') as source, wave.open(str(destination), 'wb') as output:
        if source.getparams()[:3] != (1, 2, 16000):
            raise ValueError('Expected the mono 16-bit 16 kHz test fixture')
        samples = array('h')
        samples.frombytes(source.readframes(source.getnframes()))
        output.setparams((1, 2, 48000, 0, 'NONE', ''))
        output.writeframes(array('h', (value for value in samples for _ in range(3))).tobytes())


def check_installed(exe, output):
    require_hosted_windows()
    exe = exe.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=True)
    diagnostics = output / 'windows-qa'
    diagnostics.mkdir(exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix='profile-', dir=diagnostics))
    audio = ROOT / 'tests/fixtures/russian-example.wav'
    native_wav = profile / 'microphone-48k.wav'
    report = {'passed': False, 'checks': {}}
    report_path = output / 'qa-windows-lifecycle.json'
    try:
        make_48k_fixture(audio, native_wav)
        for name, arguments in (
                ('speech', ['--transcribe', audio, '--output']),
                ('speech-48k', ['--transcribe', native_wav, '--output']),
                ('interaction', ['--test-audio', audio, '--interaction-test'])):
            result = output / f'qa-{name}.json'
            result.unlink(missing_ok=True)
            # A report may exist even when the process never exits. Check BOTH.
            status = run_checked(exe, [*arguments, result], profile, diagnostics / f'{name}.log')
            validate_result(name, json.loads(result.read_text(encoding='utf-8')))
            report['checks'][name] = status
            print(name, 'OK (process tree exited)', flush=True)
        report['passed'] = True
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'build')
    args = parser.parse_args()
    check_installed(args.executable, args.output.resolve())


if __name__ == '__main__':
    main()
