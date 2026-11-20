# Isolated comparison: 1.1.3 / 1.1.4

This QA-only branch does not change product code, tags, releases or main.
Never execute the probes on an owner's workstation. They require GitHub-hosted Windows.

## Immutable inputs

- 1.1.3 source: `40df3e8106680175d7eb57875bf9f96390ed4366`
- 1.1.4 source: `ca578a38f82360a053125c5850be7b8a7c36cbb9`
- Same runner: Windows Server 2022, Python 3.14.10, identical locked dependencies.
- Same harness/checker, 3 repeats per fault scenario, separate trial processes/profiles.
- Raw logs and JSON are uploaded even when a step fails.

## Source faults

`recognizer_probe.py`: synthetic subprocess either never reads from a real Pipe (8 MiB audio), or consumes the request but withholds its response. The native pending I/O and the peer's state must both be evidenced. Test whole-request timeout (accelerated to 3 seconds), explicit `close()` during the fault, cleanup and a second request via `warm()` on the same session object. External watchdog is 35 seconds; 1.1.3's blocked send can legitimately hit it. Watchdog cleanup is not product recovery and must not be reported as a pass.

`gui_exit_probe.py`: actual Tk/Application, normal dictation thread and event queue, real Pipe/subprocess; only audio recorder and recognition computation are synthetic. After a confirmed fault, enqueue the usual exit event. Measure actual process termination and zero remaining Job processes, not just a pre-exit JSON report. Three repeats each of unread audio and no response. A measured failure is an observation, not automatically a broken test harness.

Both scripts use a launch gate: no application import or recognizer spawn before assignment to a kill-on-close Windows Job. Each trial has external deadlines and evidence of natural versus forced termination.

## Published packages (separate evidence)

`installed_probe.ps1` downloads the unmodified public Windows installers on the hosted runner, verifies pinned SHA-256 and FileVersion, installs to separate directories, blocks app outbound networking, then uses the same existing `installer/check_windows.py` for both versions. This checks Russian WAV speech, 48 kHz resampling, tray/UI interactions, normal exit and absence of surviving processes.

These binary checks do NOT inject recognizer faults. Source fault results do NOT prove the packaged binary's injected-fault behavior. No physical microphone, owner's input devices, or prolonged soak are tested. A green workflow means evidence collection succeeded; consult individual outcomes before claiming product stability.
