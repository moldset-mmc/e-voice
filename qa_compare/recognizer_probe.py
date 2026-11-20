"""Real IPC comparison; execute ONLY on disposable GitHub-hosted Windows.

No application, microphone, model, or input hooks are imported. The selected
checkout's RecognitionSession is unchanged; only its _recognize target is
replaced by a spawnable synthetic peer. Native I/O observers return the original
Win32 results unchanged. A pending overlapped operation AND peer acknowledgement
are mandatory evidence, never elapsed time alone.

CLI: python qa_compare/recognizer_probe.py --source CHECKOUT --output DIRECTORY
Exit 0 means the observations were collected, NOT that the product passed.
Missing evidence, malformed reports, unexpected exceptions and containment errors
exit 1. Each trial has a kill-on-close Windows Job and an external watchdog.
"""
import argparse
import hashlib
import importlib.util
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
from uuid import uuid4

SCENARIOS = ('blocked-send-timeout', 'no-response-timeout',
             'close-blocked-send', 'close-no-response')
AUDIO_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT = 3.0
WATCHDOG_SECONDS = 35.0


def require_hosted_windows():
    if (sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
        raise RuntimeError('IPC probes require a disposable GitHub-hosted Windows runner')


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class Journal:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def emit(self, event, **details):
        record = dict(event=event, monotonic=time.monotonic(), pid=os.getpid(), **details)
        with self.lock, self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())


def synthetic_peer(connection, config):
    require_hosted_windows()
    journal = Journal(Path(config['journal']))
    try:
        journal.emit('peer_ready', mode=config['mode'])
        config['ready'].set()
        if config['mode'] == 'no-read':
            # This branch deliberately contains NO read operation.
            config['release'].wait()
            raise RuntimeError('The no-read fault must never be released by the harness')
        kind, rate = connection.recv()
        if kind != 'audio' or rate != 16000:
            raise RuntimeError(f'Unexpected synthetic request: {kind!r}, {rate!r}')
        audio = connection.recv_bytes()
        digest = hashlib.sha256(audio).hexdigest()
        journal.emit('request_consumed', bytes=len(audio), sha256=digest,
                     response_withheld=config['mode'] == 'no-response')
        config['consumed'].set()
        if config['mode'] == 'no-response':
            # A separate Event blocks this peer; it never sends a reply.
            config['release'].wait()
            raise RuntimeError('The no-response fault must never be released')
        connection.send((True, f'synthetic:{len(audio)}:{digest}'))
        config['release'].wait()
    except BaseException as error:
        journal.emit('peer_error', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        connection.close()


class WindowsJob:
    def __init__(self):
        require_hosted_windows()
        import win32job
        self.api = win32job
        self.handle = win32job.CreateJobObject(None, f'Local\\EVoice-IPC-{uuid4().hex}')
        try:
            info = win32job.QueryInformationJobObject(
                self.handle, win32job.JobObjectExtendedLimitInformation)
            info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(
                self.handle, win32job.JobObjectExtendedLimitInformation, info)
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

    def active(self):
        return self.api.QueryInformationJobObject(
            self.handle, self.api.JobObjectBasicAccountingInformation)['ActiveProcesses']

    def wait_empty(self, timeout=10):
        deadline = time.monotonic() + timeout
        while self.active():
            if time.monotonic() >= deadline:
                raise RuntimeError('Job still contains live processes after teardown')
            time.sleep(0.05)

    def terminate(self):
        self.api.TerminateJobObject(self.handle, 124)
        self.wait_empty()

    def close(self):
        if self.handle is not None:
            self.handle.Close()
            self.handle = None


def load_session(source):
    path = source / 'recognition_session.py'
    spec = importlib.util.spec_from_file_location('recognition_session', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load selected recognition_session.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module._recognize = synthetic_peer
    return module


def peer_config(context, mode, output, suffix):
    return dict(mode=mode, ready=context.Event(), consumed=context.Event(),
                release=context.Event(), journal=str(output / f'peer-{suffix}.jsonl'))


def run_trial(source, output, scenario):
    require_hosted_windows()
    # The supervisor sends this byte only AFTER assigning this process to its Job.
    # No selected-source import, spawn, or native call is allowed before the gate.
    if sys.stdin.buffer.read(1) != b'G':
        raise RuntimeError('Missing supervisor Job assignment gate')
    journal = Journal(output / 'events.jsonl')
    journal.emit('job_gate_received')
    try:
        return trial_body(source, output, scenario, journal)
    except BaseException as error:
        journal.emit('harness_error', error=f'{type(error).__name__}: {error}',
                     traceback=traceback.format_exc())
        write_json(output / 'result.json', dict(status='harness_error',
                   error=f'{type(error).__name__}: {error}'))
        raise


def trial_body(source, output, scenario, journal):
    require_hosted_windows()
    import _winapi
    module = load_session(source)
    context = mp.get_context('spawn')
    blocked_send = 'blocked-send' in scenario
    close_scenario = scenario.startswith('close-')
    config = peer_config(context, 'no-read' if blocked_send else 'no-response', output, 'fault')
    session = module.RecognitionSession(config)
    session.warm()
    if not config['ready'].wait(10):
        raise RuntimeError('Synthetic peer never acknowledged readiness')
    fault_pid = session.process.pid
    handle = session.connection.fileno()
    journal.emit('fault_peer_ready', peer_pid=fault_pid, mode=config['mode'])
    pending = threading.Event()
    original_write, original_read = _winapi.WriteFile, _winapi.ReadFile

    def observe(operation, native_function, native_handle, buffer, *args, **kwargs):
        result = native_function(native_handle, buffer, *args, **kwargs)
        overlapped, error = result
        matching = ((operation == 'write' and blocked_send and len(buffer) == AUDIO_BYTES)
                    or (operation == 'read' and not blocked_send))
        if native_handle == handle and matching and error == _winapi.ERROR_IO_PENDING:
            # The kernel event is unsignalled NOW: real overlapped I/O is pending.
            # A mere call-entry marker or a sleep would not prove this condition.
            wait_result = _winapi.WaitForSingleObject(overlapped.event, 0)
            if wait_result == _winapi.WAIT_TIMEOUT and not pending.is_set():
                journal.emit('native_io_pending', operation=operation, winerror=error,
                             event_wait_result=wait_result,
                             size=len(buffer) if operation == 'write' else buffer)
                pending.set()
        return result

    def observed_write(native_handle, buffer, *args, **kwargs):
        return observe('write', original_write, native_handle, buffer, *args, **kwargs)

    def observed_read(native_handle, size, *args, **kwargs):
        return observe('read', original_read, native_handle, size, *args, **kwargs)

    _winapi.WriteFile, _winapi.ReadFile = observed_write, observed_read
    payload = b'\x00' * (AUDIO_BYTES if blocked_send else 4096)
    request_timeout = 60.0 if close_scenario else REQUEST_TIMEOUT
    request = {}

    def transcribe():
        started = time.monotonic()
        journal.emit('transcribe_entered', timeout_seconds=request_timeout, bytes=len(payload))
        try:
            request.update(outcome='unexpected_success', result=session.transcribe(
                payload, rate=16000, timeout=request_timeout))
        except Exception as error:
            request.update(outcome='exception', exception_type=type(error).__name__,
                           error=str(error), traceback=traceback.format_exc())
        finally:
            request['elapsed_seconds'] = time.monotonic() - started
            journal.emit('transcribe_returned', **request)

    thread = threading.Thread(target=transcribe, name='QA-transcribe', daemon=True)
    thread.start()
    if not pending.wait(10):
        raise RuntimeError('No native pending I/O evidence; fault injection is unproven')
    if not blocked_send and not config['consumed'].wait(10):
        raise RuntimeError('No peer acknowledgement of the entire consumed request')
    if not session.process or not session.process.is_alive():
        raise RuntimeError('Fault peer died before fault confirmation')
    if not thread.is_alive():
        raise RuntimeError('Transcription returned before the injected state was confirmed')
    journal.emit('injection_confirmed', mode=config['mode'], peer_pid=fault_pid,
                 peer_ready=True, request_consumed=not blocked_send,
                 native_io_pending=True)
    close_result = {'attempted': close_scenario}
    if close_scenario:
        journal.emit('close_entered')
        started = time.monotonic()
        try:
            session.close()
            close_result['outcome'] = 'returned'
        except RuntimeError as error:
            close_result.update(outcome='cleanup_error', error=str(error))
        close_result['elapsed_seconds'] = time.monotonic() - started
        journal.emit('close_returned', **close_result)
    journal.emit('waiting_for_transcribe')
    # Deliberately no inner timeout masquerading as production timeout. On the old
    # blocked-send implementation the EXTERNAL watchdog records and kills the Job.
    thread.join()
    _winapi.WriteFile, _winapi.ReadFile = original_write, original_read
    error = request.get('error', '')
    timed_out = (request.get('exception_type') == 'RuntimeError'
                 and 'Распознавание не ответило вовремя' in error)
    if request.get('exception_type') not in (None, 'RuntimeError', 'OSError',
                                             'EOFError', 'BrokenPipeError'):
        raise RuntimeError(f'Unexpected transcription exception: {request}')
    outcome = ('session_timeout' if timed_out else
               'close_cancelled' if close_scenario and request.get('outcome') == 'exception' else
               request.get('outcome', 'missing_request_result'))
    result = dict(status='observation', scenario=scenario, injection_confirmed=True,
                  outcome=outcome, observed_timeout=timed_out, request=request, close=close_result,
                  fault_peer_pid=fault_pid, recovery={'attempted': False},
                  cleanup_before_recovery=dict(connection_cleared=session.connection is None,
                                               process_cleared=session.process is None))
    # Same object, real warm(), fresh Pipe and child, then a second real exchange.
    # Do not forcibly close the first peer to manufacture successful recovery.
    if timed_out or close_scenario:
        journal.emit('recovery_entered')
        good = peer_config(context, 'success', output, 'recovery')
        session.model_directory = good
        session.warm()
        if not good['ready'].wait(10):
            raise RuntimeError('Recovery warm did not spawn the synthetic replacement')
        recovery_pid = session.process.pid
        audio = b'next request after fault'
        expected = f'synthetic:{len(audio)}:{hashlib.sha256(audio).hexdigest()}'
        text = session.transcribe(audio, rate=16000, timeout=REQUEST_TIMEOUT)
        result['recovery'] = dict(attempted=True, passed=text == expected and recovery_pid != fault_pid,
                                  peer_pid=recovery_pid, result=text, expected=expected)
        journal.emit('recovery_returned', **result['recovery'])
    else:
        result['recovery']['skip_reason'] = 'request did not end via timeout or explicit close'
    journal.emit('final_cleanup_entered')
    session.close()
    # IPC helper threads must also finish; daemon status is not cleanup evidence.
    helpers = [item for item in threading.enumerate() if item.name == 'EVoice-IPC']
    for helper in helpers:
        helper.join(2)
    result['cleanup'] = dict(connection_cleared=session.connection is None,
                             process_cleared=session.process is None,
                             ipc_threads_alive=sum(item.is_alive() for item in helpers))
    journal.emit('final_cleanup_returned', **result['cleanup'])
    write_json(output / 'result.json', result)
    return 0


def read_events(path, allow_truncated=False):
    if not path.exists():
        return []
    lines = path.read_text(encoding='utf-8').splitlines()
    records = []
    for index, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # A watchdog kill may interrupt only the final append, not history.
            if not allow_truncated or index != len(lines) - 1:
                raise
    return records


def supervise_trial(source, output, scenario):
    require_hosted_windows()
    output.mkdir(parents=True, exist_ok=False)
    job, process = WindowsJob(), None
    attached = False
    watchdog = False
    forced_cleanup = False
    active_before_forced_cleanup = None
    started = time.monotonic()
    try:
        with (output / 'process.log').open('wb') as log:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), '--source', str(source),
                 '--output', str(output), '--trial', scenario], cwd=output,
                stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
            job.attach(process)
            attached = True
            process.stdin.write(b'G')
            process.stdin.flush()
            process.stdin.close()
            try:
                code = process.wait(timeout=WATCHDOG_SECONDS)
            except subprocess.TimeoutExpired:
                watchdog = forced_cleanup = True
                active_before_forced_cleanup = job.active()
                job.terminate()
                code = process.wait(timeout=10)
            active_at_exit = job.active()
            if active_at_exit:
                forced_cleanup = True
                active_before_forced_cleanup = active_at_exit
                job.terminate()
            job.wait_empty()
            remaining = job.active()
        events = read_events(output / 'events.jsonl', allow_truncated=forced_cleanup)
        peer_events = read_events(output / 'peer-fault.jsonl', allow_truncated=forced_cleanup)
        recovery_events = read_events(output / 'peer-recovery.jsonl', allow_truncated=forced_cleanup)
        names = [event['event'] for event in events]
        errors = [event for event in events + peer_events + recovery_events
                  if event['event'] in ('harness_error', 'peer_error')]
        evidence = ('injection_confirmed' in names and 'native_io_pending' in names
                    and any(event['event'] == 'peer_ready' for event in peer_events))
        if 'no-response' in scenario:
            evidence = evidence and any(event['event'] == 'request_consumed'
                                        and event.get('response_withheld') is True
                                        and event.get('bytes') == 4096 for event in peer_events)
        if errors or not evidence:
            raise RuntimeError(f'Unproven injection or harness/peer error: {errors}; events={names}')
        if watchdog:
            # A hang before injection, or while loading/recovering, is NOT silently
            # accepted as the known pre-deadline blocked-send regression.
            if names[-1] not in ('waiting_for_transcribe', 'close_entered'):
                raise RuntimeError(f'Watchdog expired outside confirmed fault wait: {names[-1]}')
            result = dict(status='observation', scenario=scenario, injection_confirmed=True,
                          outcome='watchdog_timeout', observed_timeout=False,
                          recovery=dict(attempted=False, skip_reason='external watchdog killed fault wait'),
                          last_stage=names[-1])
        else:
            if code != 0:
                raise RuntimeError(f'Trial exited {code}; inspect process.log')
            result = json.loads((output / 'result.json').read_text(encoding='utf-8'))
            if result.get('status') != 'observation' or result.get('injection_confirmed') is not True:
                raise RuntimeError('Trial report failed validation')
        result['evidence'] = dict(
            native_io=[event for event in events if event['event'] == 'native_io_pending'],
            confirmation=[event for event in events if event['event'] == 'injection_confirmed'],
            peer=peer_events)
        result['supervisor'] = dict(exit_code=code, watchdog_timeout=watchdog,
                                    forced_cleanup=forced_cleanup,
                                    active_processes_before_forced_cleanup=active_before_forced_cleanup,
                                    active_processes_at_exit=active_at_exit,
                                    remaining_processes=remaining,
                                    elapsed_seconds=time.monotonic() - started,
                                    watchdog_seconds=WATCHDOG_SECONDS)
        write_json(output / 'observation.json', result)
        return result
    finally:
        job.close()
        if process is not None and process.poll() is None:
            if not attached:
                process.kill()  # Start gate guarantees it has spawned no children.
            process.wait(timeout=10)


def main():
    # Must precede output creation, selected-source imports and all native APIs.
    require_hosted_windows()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--trial', choices=SCENARIOS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    source, output = args.source.resolve(strict=True), args.output.resolve()
    if args.trial:
        return run_trial(source, output, args.trial)
    if args.repeats < 3:
        parser.error('--repeats must be at least 3')
    output.mkdir(parents=True, exist_ok=True)
    report = dict(schema_version=1, kind='recognizer-ipc-comparison', source=str(source),
                  source_sha256=hashlib.sha256((source / 'recognition_session.py').read_bytes()).hexdigest(),
                  python=sys.version, runner_os=os.environ.get('RUNNER_OS'),
                  runner_image=os.environ.get('ImageOS'), repeats=args.repeats,
                  request_timeout_seconds=REQUEST_TIMEOUT, audio_bytes=AUDIO_BYTES,
                  harness_passed=False, observations=[], errors=[])
    for scenario in SCENARIOS:
        for repeat in range(1, args.repeats + 1):
            name = f'{scenario}-{repeat}'
            try:
                observation = supervise_trial(source, output / name, scenario)
                report['observations'].append(dict(repeat=repeat, **observation))
            except Exception as error:
                report['errors'].append(dict(trial=name, error=f'{type(error).__name__}: {error}',
                                              traceback=traceback.format_exc()))
            write_json(output / 'summary.json', report)
    report['harness_passed'] = not report['errors']
    write_json(output / 'summary.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['harness_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
