"""Hosted-Windows source GUI shutdown comparison; never an installed/frozen test.

--source CHECKOUT --output REPORT.json [--repeats 3]
Only the recorder and recognizer implementation are synthetic. Tk, Application,
its poll/exit methods, dictation thread, spawn process and Pipe remain real.
"""
import os
import sys


def require_hosted_windows():
    if (sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
        raise RuntimeError('GUI probe requires a disposable GitHub-hosted Windows runner')


# Also guards spawn's __mp_main__ import, before any application/native imports.
require_hosted_windows()

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time
import traceback


PAYLOAD_BYTES = 8 * 1024 * 1024


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def fake_recognizer(connection, model_directory):
    """Top-level function: multiprocessing spawn can pickle it without closures."""
    marker = Path(os.environ['EVOICE_PROBE_TRIAL']) / 'peer.json'
    state = {'pid': os.getpid(), 'stage': 'ready', 'scenario': os.environ['EVOICE_PROBE_SCENARIO']}
    save(marker, state)
    if state['scenario'] == 'no_response':
        kind, rate = connection.recv()
        payload = connection.recv_bytes()
        save(marker.with_name('received.json'),
             dict(state, stage='request_received', kind=kind, rate=rate, payload_bytes=len(payload)))
    # unread_pipe deliberately never reads even the request header.
    threading.Event().wait()


class SyntheticRecorder:
    rate = 16000
    overflow = limit_reached = False
    stream = None

    def __init__(self, events):
        pass

    def start(self, device):
        self.stream = True

    def stop(self):
        self.stream = None
        return bytes(PAYLOAD_BYTES)


def read_events(path):
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # A final append may still be in flight; never invent its contents.
    return result


def child(args):
    # Parent releases this gate only after successful JobObject assignment.
    # EOF (parent crashed before assignment) exits without app imports/children.
    if sys.stdin.buffer.read(1) != b'G':
        return 2
    sys.path.insert(0, str(args.source))
    import _winapi
    import recognition_session
    import app as application

    recognition_session._recognize = fake_recognizer
    application.Recorder = SyntheticRecorder
    application.enable_dpi_awareness()
    application.enable_diagnostics()
    trial = args.trial
    lock = threading.Lock()

    def note(event, **data):
        with lock, (trial / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(event=event, clock=time.perf_counter(), **data)) + '\n')

    root = application.tk.Tk()
    root.withdraw()
    app = application.Application(root, test_mode=True)
    app.autopaste.set(False)
    note('gui_ready', frozen=bool(getattr(sys, 'frozen', False)), autopaste=app.autopaste.get())
    original_exit = app.exit

    def observed_exit():
        note('exit_entered', state=app.state, worker_alive=bool(app.worker and app.worker.is_alive()))
        original_exit()
        note('exit_returned')

    app.exit = observed_exit

    def callback_error(typ, value, tb):
        note('callback_error', error=f'{typ.__name__}: {value}')
        traceback.print_exception(typ, value, tb)
        app.events.put(('exit', None))

    root.report_callback_exception = callback_error
    io_state = {}
    originals = {'write': _winapi.WriteFile, 'read': _winapi.ReadFile}

    def native_observer(operation):
        def observed(handle, buffer, *positional, **keywords):
            result = originals[operation](handle, buffer, *positional, **keywords)
            matching = ((operation == 'write' and args.scenario == 'unread_pipe'
                         and len(buffer) == PAYLOAD_BYTES)
                        or (operation == 'read' and args.scenario == 'no_response'))
            if handle == io_state.get('handle') and matching:
                overlapped, error = result
                if (error == _winapi.ERROR_IO_PENDING
                        and _winapi.WaitForSingleObject(overlapped.event, 0) == _winapi.WAIT_TIMEOUT):
                    note('native_pending', operation=operation, error=error, event_unsignaled=True)
                    io_state.update(overlapped=overlapped, operation=operation, error=error)
            return result  # Preserve native arguments/results and original Pipe semantics.
        return observed

    def wait_fault():
        marker = trial / 'received.json'
        if not marker.exists():
            marker = trial / 'peer.json'
        peer = json.loads(marker.read_text(encoding='utf-8'))
        pending = bool(app.worker and app.worker.is_alive() and app.state == 'transcribing')
        overlapped = io_state.get('overlapped')
        unsignaled = (overlapped is not None
                      and _winapi.WaitForSingleObject(overlapped.event, 0) == _winapi.WAIT_TIMEOUT)
        injected = (unsignaled and pending and app.session.process.is_alive()
                    and (peer['stage'] == 'ready' if args.scenario == 'unread_pipe'
                         else peer['stage'] == 'request_received'))
        if not injected:
            root.after(25, wait_fault)
            return
        note('exit_queued', injection_confirmed=True, scenario=args.scenario,
             pending_transcription=pending, native_operation=io_state['operation'],
             native_error=io_state['error'], native_event_unsignaled=True, peer=peer)
        app.events.put(('exit', None))  # Original poll dispatches the actual exit.

    def wait_peer():
        if not (trial / 'peer.json').exists():
            root.after(25, wait_peer)
            return
        io_state['handle'] = app.session.connection.fileno()
        _winapi.WriteFile = native_observer('write')
        _winapi.ReadFile = native_observer('read')
        app.toggle()  # Normal synthetic recorder.stop -> transcribe_in_background.
        root.after(25, wait_fault)

    def begin():
        app.toggle()  # Normal recorder.start -> session.warm -> real spawn/Pipe.
        if app.state != 'recording':
            raise RuntimeError(f'Unable to start synthetic recording: {app.state}')
        note('recognizer_spawned', pid=app.session.process.pid)
        wait_peer()

    root.after(100, begin)
    try:
        root.mainloop()
    finally:
        _winapi.WriteFile, _winapi.ReadFile = originals['write'], originals['read']
    note('mainloop_returned')  # NOT process exit: only the external parent can prove it.
    return 0


def trial_run(args, scenario, index, directory, WindowsJob):
    trial = directory / f'{scenario}-{index}'
    trial.mkdir()  # Do not accidentally reuse markers from an earlier run.
    result = dict(scenario=scenario, repeat=index, artifacts=str(trial), watchdog_fired=False,
                  forced_cleanup=False, natural_zero_processes=False, cleanup_zero_processes=False,
                  observed_exit_seconds=None, observed_zero_seconds=None)
    job, process, attached = None, None, False
    started = time.perf_counter()
    exit_observed = None
    try:
        job = WindowsJob()
        env = dict(os.environ, EVOICE_PROBE_TRIAL=str(trial), EVOICE_PROBE_SCENARIO=scenario,
                   EVOICE_DATA_DIR=str(trial / 'profile'), PYTHONUNBUFFERED='1')
        command = [sys.executable, str(Path(__file__).resolve()), '--source', str(args.source),
                   '--output', str(args.output), '--trial', str(trial), '--scenario', scenario]
        with (trial / 'process.log').open('wb') as log:
            process = subprocess.Popen(command, cwd=trial, env=env, stdin=subprocess.PIPE,
                                       stdout=log, stderr=subprocess.STDOUT)
            job.attach(process)
            attached = True
            process.stdin.write(b'G')
            process.stdin.close()
            while True:
                now = time.perf_counter()
                events = read_events(trial / 'events.jsonl')
                queued = next((e for e in events if e['event'] == 'exit_queued'), None)
                if process.poll() is not None and exit_observed is None:
                    exit_observed = now
                count = job.active_processes()
                if exit_observed is not None and count == 0:
                    result['natural_zero_processes'] = True
                    if queued:
                        result['observed_zero_seconds'] = now - queued['clock']
                    break
                if now >= (queued['clock'] + args.exit_timeout if queued else started + args.startup_timeout):
                    result['watchdog_fired'] = True
                    result['watchdog_phase'] = 'shutdown' if queued else 'startup_or_injection'
                    break
                time.sleep(.05)
            result['process_exit_code_before_cleanup'] = process.poll()
            result['active_processes_before_cleanup'] = job.active_processes()
            if exit_observed is not None and queued:
                result['observed_exit_seconds'] = exit_observed - queued['clock']
    except Exception as exc:
        result['harness_error'] = f'{type(exc).__name__}: {exc}'
        traceback.print_exc()
    finally:
        try:
            if attached:
                if job.active_processes():
                    result['forced_cleanup'] = True
                    # Keep the handle open to QUERY real zero, not assume kill-on-close worked.
                    job.api.TerminateJobObject(job.handle, 124)
                job.wait_empty(timeout=10)
                result['active_processes_after_cleanup'] = job.active_processes()
                result['cleanup_zero_processes'] = result['active_processes_after_cleanup'] == 0
            elif process is not None:
                process.kill()  # Gate never released: it cannot have spawned descendants.
            if process is not None:
                result['final_process_exit_code'] = process.wait(timeout=10)
        except Exception as exc:
            result['cleanup_error'] = f'{type(exc).__name__}: {exc}'
        finally:
            if job is not None:
                job.close()  # Last-resort kill-on-close on watchdog/query/cleanup failures.
            if process is not None and process.stdin and not process.stdin.closed:
                process.stdin.close()
    events = read_events(trial / 'events.jsonl')
    result['events'] = events
    result['injection_confirmed'] = any(e['event'] == 'exit_queued' and e['injection_confirmed'] for e in events)
    result['exit_method_entered'] = any(e['event'] == 'exit_entered' for e in events)
    result['callback_errors'] = [e for e in events if e['event'] == 'callback_error']
    natural_success = (result['natural_zero_processes'] and not result['watchdog_fired']
                       and result.get('process_exit_code_before_cleanup') == 0)
    result['shutdown_watchdog_confirmed'] = bool(
        result['watchdog_fired'] and result.get('watchdog_phase') == 'shutdown'
        and result.get('active_processes_before_cleanup', 0) > 0
        and result.get('process_exit_code_before_cleanup') in (None, 0))
    result['valid_measurement'] = bool(result['injection_confirmed'] and result['exit_method_entered']
                                      and not result['callback_errors'] and 'harness_error' not in result
                                      and 'cleanup_error' not in result and result['cleanup_zero_processes']
                                      and (natural_success or result['shutdown_watchdog_confirmed']))
    result['clean_exit'] = bool(result['valid_measurement'] and natural_success)
    save(trial / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='JSON report; sibling directory holds logs')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--startup-timeout', type=float, default=60)
    parser.add_argument('--exit-timeout', type=float, default=15)
    parser.add_argument('--trial', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--scenario', choices=('unread_pipe', 'no_response'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.source = args.source.resolve(strict=True)
    args.output = args.output.resolve()
    if args.repeats < 3 or args.startup_timeout <= 0 or args.exit_timeout <= 0:
        parser.error('Require >=3 repeats and positive timeouts')
    if args.trial:
        return child(args)
    # Import the harness wrapper, never a source snapshot's potentially older one.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from installer.check_windows import WindowsJob
    args.output.parent.mkdir(parents=True, exist_ok=True)
    directory = args.output.with_suffix('.artifacts')
    directory.mkdir()  # Preserve previous evidence; require a fresh output location.
    report = dict(kind='source_tk_gui_exit', frozen_installer_test=False, source=str(args.source),
                  python=sys.version, payload_bytes=PAYLOAD_BYTES, repeats=args.repeats,
                  startup_timeout=args.startup_timeout, exit_timeout=args.exit_timeout,
                  timing='Cross-process perf_counter; external 50ms polling observations, not exact OS exit timestamps',
                  version=(args.source / 'VERSION').read_text(encoding='utf-8').strip(),
                  source_sha256={name: hashlib.sha256((args.source / name).read_bytes()).hexdigest()
                                 for name in ('app.py', 'recognition_session.py', 'dictation.py')}, trials=[])
    save(args.output, report)
    for scenario in ('unread_pipe', 'no_response'):
        for index in range(1, args.repeats + 1):
            report['trials'].append(trial_run(args, scenario, index, directory, WindowsJob))
            save(args.output, report)
    report['measurement_complete'] = all(row['valid_measurement'] for row in report['trials'])
    report['all_clean_exits'] = all(row['clean_exit'] for row in report['trials'])
    save(args.output, report)
    # A measured shutdown timeout is a comparison result, NOT infrastructure failure.
    return 0 if report['measurement_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
