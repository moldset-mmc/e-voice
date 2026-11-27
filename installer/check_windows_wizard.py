"""Drive the normal Inno wizard and open its real .lnk on a clean Windows runner."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import win32api
import win32con
import win32event
import win32gui
import win32process
from win32com.client import Dispatch
from win32com.shell import shell, shellcon

from check_windows import require_hosted_windows


def wait_for(probe, description, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = probe()
        if result:
            return result
        time.sleep(.2)
    raise TimeoutError(description)


def windows(parent=None):
    handles = []
    def collect(hwnd, _):
        handles.append(hwnd)
        return True
    if parent is None:
        win32gui.EnumWindows(collect, None)
    else:
        win32gui.EnumChildWindows(parent, collect, None)
    return handles


def caption(hwnd):
    # WM_GETTEXT also reads child controls belonging to the installer process.
    buf = ctypes.create_unicode_buffer(1024)
    win32gui.SendMessageTimeout(hwnd, win32con.WM_GETTEXT, len(buf),
                                ctypes.addressof(buf), win32con.SMTO_ABORTIFHUNG, 1000)
    return buf.value.replace('&', '').strip().rstrip('>›» ').strip()


def click_control(hwnd):
    # Use a real mouse click if this hosted session exposes the input desktop.
    # Send input only when the requested control is actually under the pointer.
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    point = ((left + right) // 2, (top + bottom) // 2)
    try:
        win32api.SetCursorPos(point)
        if win32gui.WindowFromPoint(point) == hwnd:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
            return
    except win32api.error as error:
        if error.winerror != 5:
            raise
    # Non-interactive desktop sessions still dispatch ordinary click messages
    # to the visible wizard. Native Delphi radio buttons need down and up.
    x, y = win32gui.ScreenToClient(hwnd, point)
    xy = win32api.MAKELONG(x, y)
    win32gui.SendMessageTimeout(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, xy, 2, 2000)
    win32gui.SendMessageTimeout(hwnd, win32con.WM_LBUTTONUP, 0, xy, 2, 2000)


def drive_wizard(setup, log, report):
    with subprocess.Popen([str(setup), '/LANG=russian', '/NORESTART', f'/LOG={log}']) as process:
        try:
            wizard = wait_for(lambda: next((h for h in windows() if
                              win32gui.GetClassName(h) == 'TWizardForm' and
                              'E Voice' in win32gui.GetWindowText(h) and win32gui.IsWindowVisible(h)), None),
                              'The normal E Voice setup wizard did not appear')
            def controls(enabled=True):
                if process.poll() is not None:
                    raise RuntimeError(f'Setup exited early with code {process.returncode}')
                return {caption(h): h for h in windows(wizard)
                        if win32gui.IsWindowVisible(h) and (not enabled or win32gui.IsWindowEnabled(h))}
            def click(labels):
                def find_button():
                    page = controls()
                    return next((page[label] for label in labels if label in page), None)
                button = wait_for(find_button, f'Missing wizard control: {labels}', 180)
                click_control(button)

            accept = 'Я принимаю условия соглашения'
            radio = wait_for(lambda: controls().get(accept), 'License acceptance control')
            click_control(radio)
            time.sleep(.3)
            if not win32gui.SendMessage(radio, win32con.BM_GETCHECK, 0, 0):
                # Delphi reflects BN_CLICKED to its radio button to update both
                # the checked value and the enablement of the real Next button.
                command = win32api.MAKELONG(win32gui.GetDlgCtrlID(radio), win32con.BN_CLICKED)
                win32gui.SendMessageTimeout(win32gui.GetParent(radio), win32con.WM_COMMAND, command, radio, 2, 2000)
            click(['Далее', 'Установить'])
            def finished():
                page = controls()
                if 'Завершить' in page:
                    if not any('трижды быстро щёлкните левой кнопкой' in text for text in page):
                        raise RuntimeError('The finish page is missing the triple-click hint')
                    return True
                if 'Установить' in page:
                    click_control(page['Установить'])
                return False
            wait_for(finished, 'Setup did not reach the finish page', 180)
            report['visible_finish_hint'] = True
            click(['Завершить'])
            if process.wait(timeout=60):
                raise RuntimeError(f'Setup failed with code {process.returncode}')
        except Exception:
            if 'wizard' in locals() and win32gui.IsWindow(wizard):
                for label, hwnd in controls(enabled=False).items():
                    print('Wizard control:', win32gui.GetClassName(hwnd), bool(win32gui.IsWindowEnabled(hwnd)),
                          ascii(label[:120]), flush=True)
            raise
        finally:
            if process.poll() is None:
                process.kill()


def tray_process(exe):
    for hwnd in windows():
        cls = win32gui.GetClassName(hwnd)
        if cls.startswith('EVoice') and cls.endswith('SystemTrayIcon'):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                                          False, pid)
            try:
                if Path(win32process.GetModuleFileNameEx(handle, 0)) == exe:
                    return pid
            finally:
                handle.Close()
    return None


def stop_instance(pid):
    handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE | win32con.SYNCHRONIZE, False, pid)
    try:
        win32api.TerminateProcess(handle, 0)
        if win32event.WaitForSingleObject(handle, 10000) != 0:
            raise RuntimeError('The tested E Voice instance did not exit')
    finally:
        handle.Close()


def check(setup, arch, output):
    require_hosted_windows()
    process_machine, native_machine = ctypes.c_ushort(), ctypes.c_ushort()
    if not ctypes.windll.kernel32.IsWow64Process2(ctypes.c_void_p(-1), ctypes.byref(process_machine),
                                                 ctypes.byref(native_machine)):
        raise ctypes.WinError()
    if native_machine.value != {'x64': 0x8664, 'arm64': 0xaa64}[arch]:
        raise RuntimeError('Unexpected native Windows architecture')
    desktop = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, 0, 0))
    shortcut = desktop / 'E Voice.lnk'
    exe = Path(os.environ['LOCALAPPDATA']) / 'Programs/E Voice/EVoice.exe'
    if shortcut.exists() or exe.exists():
        raise RuntimeError('The wizard check needs a clean installation')
    log = output / 'windows-qa' / 'wizard.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with setup.open('rb') as file:
        report = {'arch': arch, 'sha256': hashlib.file_digest(file, 'sha256').hexdigest(), 'passed': False}
    try:
        drive_wizard(setup, log, report)
        first = wait_for(lambda: tray_process(exe), 'Setup did not launch the installed tray application')
        report['automatic_tray_launch'] = True
        link = Dispatch('WScript.Shell').CreateShortcut(str(shortcut))
        if not shortcut.is_file() or Path(link.TargetPath) != exe or link.Arguments:
            raise RuntimeError('Incorrect Desktop shortcut')
        os.startfile(shortcut)
        wait_for(lambda: any(win32process.GetWindowThreadProcessId(h)[1] == first and
                             win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == 'Настройки · E Voice'
                             for h in windows()), 'The shortcut did not show settings in the running instance')
        report['shortcut_opens_settings'] = True
        stop_instance(first)
        os.startfile(shortcut)
        second = wait_for(lambda: tray_process(exe), 'Cold launch from Desktop shortcut failed')
        report['shortcut_cold_launch'] = True
        stop_instance(second)
        report['passed'] = True
    finally:
        (output / 'qa-windows-wizard.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('setup', type=Path)
    parser.add_argument('arch', choices=['x64', 'arm64'])
    args = parser.parse_args()
    check(args.setup.resolve(strict=True), args.arch, Path('build'))
