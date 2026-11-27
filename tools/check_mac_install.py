"""Exercise the actual user-scoped PKG and its desktop shortcut on fresh macOS runners."""
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import time

from AppKit import NSRunningApplication
import Quartz

APP = Path.home() / 'Applications/E Voice.app'
SHORTCUT = Path.home() / 'Desktop/E Voice.app'


def wait_for(check, description):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.25)
    raise RuntimeError(f'Timed out waiting for {description}')


def instances():
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_('io.github.moldset-mmc.evoice')
    return [app.processIdentifier() for app in apps if app.bundleURL()
            and Path(app.bundleURL().path()).resolve() == APP.resolve() and not app.isTerminated()]


def visible_pid():
    pids = instances()
    if len(pids) != 1:
        return None
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID)
    for window in windows:
        bounds = window[Quartz.kCGWindowBounds]
        if (window[Quartz.kCGWindowOwnerPID] == pids[0] and
                bounds['Width'] >= 70 and bounds['Height'] >= 35):
            return pids[0]
    return None


def main():
    if os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or platform.system() != 'Darwin':
        raise RuntimeError('This installation check requires a fresh hosted macOS runner.')
    if any(os.path.lexists(p) for p in (APP, SHORTCUT, SHORTCUT.with_name('E Voice 2.app'))):
        raise RuntimeError('The test requires an unused installation and shortcut path.')
    pkg, = Path('dist').glob('E-Voice-macOS-*.pkg')
    install = ['/usr/sbin/installer', '-pkg', str(pkg), '-target', 'CurrentUserHomeDirectory']
    subprocess.run(install, check=True)
    pid = wait_for(visible_pid, 'automatic launch with a visible E Voice window')
    assert int(subprocess.check_output(['ps', '-o', 'uid=', '-p', str(pid)])) == os.getuid()
    assert SHORTCUT.is_symlink() and SHORTCUT.samefile(APP)
    inode = SHORTCUT.lstat().st_ino
    subprocess.run(install, check=True)
    wait_for(visible_pid, 'one visible application after reinstall')
    assert SHORTCUT.lstat().st_ino == inode
    assert len([p for p in SHORTCUT.parent.iterdir() if p.is_symlink() and p.resolve() == APP]) == 1
    os.kill(visible_pid(), signal.SIGTERM)
    wait_for(lambda: not instances(), 'the tested instance to exit')
    subprocess.run(['/usr/bin/open', str(SHORTCUT)], check=True)
    wait_for(visible_pid, 'a fresh launch from the Desktop shortcut')
    SHORTCUT.unlink()
    with SHORTCUT.open('x') as existing:
        existing.write('Existing user file')
    try:
        subprocess.run(install, check=True)
        assert SHORTCUT.read_text() == 'Existing user file'
        assert SHORTCUT.with_name('E Voice 2.app').samefile(APP)
    finally:
        if SHORTCUT.is_file() and not SHORTCUT.is_symlink() and SHORTCUT.read_text() == 'Existing user file':
            SHORTCUT.unlink()
    report = {'passed': True, 'macos': platform.mac_ver()[0], 'arch': platform.machine(),
              'cpu': subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string'], text=True).strip(),
              'automatic_visible_launch': True, 'desktop_shortcut_launch': True,
              'reinstall_reuses_shortcut': True, 'existing_file_preserved': True}
    Path('build/qa-mac-install.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
