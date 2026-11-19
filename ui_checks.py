"""Exercise our own real Windows/Tk event loop without injecting user input or using a microphone."""
import json
import threading
import time
import traceback
import wave
from array import array
from types import SimpleNamespace

import win32api
import win32con
import win32gui
from tkinter import ttk
from dpi_awareness import current_awareness
from glass_theme import GLASS_ALPHA, MENU_ALPHA, SETTINGS_ALPHA


def run_checks(root, app, result_path):
    checks = {}
    measurements = {}
    deadline = time.monotonic() + 110
    # Test state stays in this process; never overwrite the user's preferences.
    app.save_settings = lambda: None
    app.autopaste.set(False)  # This test must never type into another application.
    app.auto_hide_recording.set(False)
    app.summon_enabled.set(True)

    def finish(error=None):
        if error:
            checks['error'] = str(error)
        checks['passed'] = not error and all(value is True for value in checks.values())
        result_path.write_text(json.dumps({**checks, 'measurements': measurements}, ensure_ascii=False, indent=2), encoding='utf-8')
        app.exit()

    def guard(action):
        def wrapped():
            try:
                action()
            except Exception as exc:
                traceback.print_exc()
                finish(exc)
        return wrapped

    def wait_ready(next_action):
        if app.state == 'ready':
            next_action()
        elif time.monotonic() >= deadline:
            finish('GUI/model test timed out')
        else:
            root.after(50, guard(lambda: wait_ready(next_action)))

    def wait_for(predicate, action, message):
        if predicate():
            action()
        elif time.monotonic() >= deadline:
            finish(message)
        else:
            root.after(16, guard(lambda: wait_for(predicate, action, message)))

    def has_material(motion):
        glass = motion.glass
        return (bool(glass.hwnd and win32gui.IsWindowVisible(glass.hwnd)) and
                win32gui.GetWindow(glass.front, win32con.GW_OWNER) == glass.hwnd and
                win32gui.GetWindowRect(glass.front) == win32gui.GetWindowRect(glass.hwnd))

    def exercise_appearance():
        checks['model_absent_before_first_dictation'] = app.session.process is None
        app.floating.visible(False)
        root.after(200, guard(exercise_reveal))

    def exercise_reveal():
        control = app.floating
        anchor = win32gui.GetCursorPos()
        control.show_at(anchor)
        initial_rect = win32gui.GetWindowRect(control.hwnd)
        started = time.monotonic()
        frames = []
        stationary = True

        def sample():
            nonlocal stationary
            elapsed = time.monotonic() - started
            alpha = control.opacity
            frames.append((round(elapsed * 1000), alpha))
            stationary &= win32gui.GetWindowRect(control.hwnd) == initial_rect

            if 0 < alpha < GLASS_ALPHA and alpha > frames[0][1] and 'repeated_summon_does_not_flash' not in checks:
                control.show_at(anchor)
                checks['repeated_summon_does_not_flash'] = control.opacity == alpha

            if alpha < GLASS_ALPHA and elapsed < 1:
                root.after(16, guard(sample))
                return

            alphas = [opacity for _, opacity in frames]
            measurements['window_alpha_by_ms'] = frames
            checks['gradual_appearance_at_fixed_position'] = (
                stationary and 0 < alphas[0] < 128 and alpha == GLASS_ALPHA
                and len(set(alphas)) >= 4 and alphas == sorted(alphas)
            )
            checks.setdefault('repeated_summon_does_not_flash', False)
            control.show_at(anchor)
            checks['repeated_summon_keeps_settled_button_visible'] = (
                control.opacity == GLASS_ALPHA
                and win32gui.GetWindowRect(control.hwnd) == initial_rect
            )
            exercise_native_dispatch()

        sample()

    def exercise_native_dispatch():
        app.reveal_floating()
        checks['settings_hidden'] = root.state() == 'withdrawn'
        checks['per_monitor_dpi_awareness'] = current_awareness() == 2
        checks['mouse_hook_installed'] = bool(app.mouse_gesture and app.mouse_gesture.hook)
        # Post from a foreign thread so callbacks run inside Tcl_DoOneEvent, with
        # Tcl having released the GIL. Synchronous smoke tests missed that path.
        def post():
            for _ in range(300):
                win32gui.PostMessage(app.floating.hwnd, win32con.WM_NCHITTEST, 0, 0)
                win32gui.PostMessage(app.floating.hwnd, win32con.WM_NULL, 0, 0)
            win32gui.PostMessage(app.floating.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, 0)
            win32gui.PostMessage(app.floating.hwnd, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, 0)
            win32gui.PostMessage(app.floating.hwnd, win32con.WM_LBUTTONUP, 0, 0)
        threading.Thread(target=post, daemon=True).start()
        root.after(700, guard(exercise_drag))

    def mouse_press_release(button, delta=(0, 0)):
        # Deliver native messages to our own windows without moving the user's pointer.
        left, top, right, bottom = win32gui.GetWindowRect(button)
        center = ((left + right) // 2, (top + bottom) // 2)
        x, y = win32gui.ScreenToClient(button, center)
        win32gui.SendMessage(button, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, win32api.MAKELONG(x, y))
        target = app.floating.hwnd
        x, y = win32gui.ScreenToClient(target, (center[0] + delta[0], center[1] + delta[1]))
        win32gui.SendMessage(target, win32con.WM_LBUTTONUP, 0, win32api.MAKELONG(x & 0xffff, y & 0xffff))

    def drag_microphone():
        control = app.floating
        before = win32gui.GetWindowRect(control.hwnd)
        work = win32api.GetMonitorInfo(win32api.MonitorFromWindow(control.hwnd))['Work']
        distance = round(36 * control.dpi / 96)
        dx = distance if before[2] + distance < work[2] else -distance
        mouse_press_release(control.button, (dx, 0))
        return (win32gui.GetWindowRect(control.hwnd)[0] - before[0] == dx
                and ('toggle', None) not in list(app.events.queue) and control.drag_origin is None)

    def exercise_drag():
        checks['native_event_loop_survived'] = bool(win32gui.IsWindow(app.floating.hwnd))
        for i in range(100):
            app.floating.begin_drag()
            app.floating.drag_to((-5000 + i * 160, -3000 + i * 95))
            app.floating.end_drag()
        rect = win32gui.GetWindowRect(app.floating.hwnd)
        work = win32api.GetMonitorInfo(win32api.MonitorFromRect(rect, win32con.MONITOR_DEFAULTTONEAREST))['Work']
        checks['drag_keeps_button_on_screen'] = rect[0] >= work[0] and rect[1] >= work[1] and rect[2] <= work[2] and rect[3] <= work[3]
        checks['drag_capture_released'] = win32gui.GetCapture() != app.floating.hwnd
        app.reveal_floating()
        checks['drag_from_microphone_center_is_not_a_click'] = drag_microphone()
        app.reveal_floating()
        app._test_recording_position = win32gui.GetWindowRect(app.floating.hwnd)
        foreground = win32gui.GetForegroundWindow()
        mouse_press_release(app.floating.button, (1, 1))
        checks['microphone_click_preserves_foreground'] = win32gui.GetForegroundWindow() == foreground
        root.after(1200, guard(check_background_recording))

    def check_background_recording():
        checks['model_loads_when_recording_begins'] = bool(app.session.process and app.session.process.is_alive())
        checks['record_button_stays_visible'] = app.state == 'recording' and not win32gui.IsIconic(app.floating.hwnd)
        checks['record_button_does_not_move'] = win32gui.GetWindowRect(app.floating.hwnd) == app._test_recording_position
        checks['drag_while_recording_moves_without_stopping'] = drag_microphone()
        win32gui.SendMessage(app.floating.hwnd, win32con.WM_SYSCOMMAND, win32con.SC_MINIMIZE, 0)
        root.after(220, guard(check_minimize))

    def check_minimize():
        checks['one_icon_action_hides_button'] = bool(win32gui.IsIconic(app.floating.hwnd))
        checks['explicit_hide_preserves_recording'] = app.state == 'recording'
        point = win32gui.GetCursorPos()
        app._test_show_at = app.floating.show_at

        def observe_summon(cursor=None):
            anchor = cursor or win32gui.GetCursorPos()
            measurements['summon_cursor'] = anchor
            app._test_show_at(anchor)

        app.floating.show_at = observe_summon
        app.events.put(('summon_float', point))
        root.after(150, guard(check_summon))

    def check_summon():
        checks['summon_preserves_recording'] = app.state == 'recording'
        checks['summon_restores_button_only'] = not win32gui.IsIconic(app.floating.hwnd) and root.state() == 'withdrawn'
        app.floating.show_at = app._test_show_at
        cursor = measurements['summon_cursor']
        action = win32gui.GetWindowRect(app.floating.button)
        gap = max(action[0] - cursor[0], cursor[0] - action[2], action[1] - cursor[1], cursor[1] - action[3], 0)
        panel = win32gui.GetWindowRect(app.floating.hwnd)
        work = win32api.GetMonitorInfo(win32api.MonitorFromPoint(cursor, win32con.MONITOR_DEFAULTTONEAREST))['Work']
        # The pointer may be on the taskbar, outside the usable desktop.
        cx = min(max(cursor[0], work[0]), work[2]-1)
        cy = min(max(cursor[1], work[1]), work[3]-1)
        reachable_gap = max(action[0]-cx, cx-action[2], action[1]-cy, cy-action[3], 0)
        checks['action_is_close_to_cursor'] = reachable_gap <= 38 * app.floating.dpi / 96
        expected_left = cursor[0] - work[0] >= (panel[2] - panel[0]) + round(16 * app.floating.dpi / 96)
        checks['panel_prefers_left_with_space'] = panel[2] <= cursor[0] if expected_left else panel[0] >= cursor[0]
        measurements.update(dpi=app.floating.dpi, cursor=cursor, action_rect=action,
                            distance_to_action_pixels=gap, distance_to_action_dip=round(gap * 96 / app.floating.dpi, 2))
        stationary = win32gui.GetWindowRect(app.floating.hwnd)
        app.floating.begin_drag()
        app.floating.window_proc(app.floating.hwnd, win32con.WM_MOUSEMOVE, 0, 0)
        checks['released_mouse_cannot_drag_button'] = app.floating.drag_origin is None and win32gui.GetWindowRect(app.floating.hwnd) == stationary
        app._test_reveal_position = stationary
        root.after(350, guard(check_pinned))

    def check_pinned():
        checks['summoned_button_is_stationary'] = win32gui.GetWindowRect(app.floating.hwnd) == app._test_reveal_position
        checks['fade_finishes_at_glass_opacity'] = app.floating.opacity == GLASS_ALPHA
        checks['topmost_keeps_position'] = bool(win32gui.GetWindowLong(app.floating.hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST)
        rect = win32gui.GetWindowRect(app.floating.hwnd)
        cover = win32gui.CreateWindow('STATIC', 'E Voice window stacking test', win32con.WS_POPUP | win32con.SS_NOTIFY,
                                      rect[0]-10, rect[1]-10, rect[2]-rect[0]+20, rect[3]-rect[1]+20,
                                      0, 0, win32api.GetModuleHandle(None), None)
        try:
            win32gui.SetWindowPos(cover, win32con.HWND_TOP, 0, 0, 0, 0,
                                 win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
            app.floating.keep_on_top()
            action = win32gui.GetWindowRect(app.floating.button)
            on_top = win32gui.WindowFromPoint(((action[0]+action[2])//2, (action[1]+action[3])//2))
            checks['button_above_ordinary_window'] = on_top in (app.floating.hwnd, app.floating.button)
            menu = win32gui.GetWindowRect(app.floating.menu_button)
            a, b = sorted((action, menu))
            win32gui.SetWindowPos(cover, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                 win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
            app.floating.keep_on_top()
            gap = win32gui.WindowFromPoint(((a[2] + b[0]) // 2, (rect[1] + rect[3]) // 2))
            checks['empty_gap_passes_through_to_window'] = gap == cover
        finally:
            win32gui.DestroyWindow(cover)
        mouse_press_release(app.floating.button)
        root.after(100, guard(lambda: wait_ready(check_result)))

    def check_result():
        checks['dictation_still_works'] = bool(app.last_text)
        checks['finished_button_stays_visible'] = not win32gui.IsIconic(app.floating.hwnd)
        checks['finished_button_does_not_move'] = win32gui.GetWindowRect(app.floating.hwnd) == app._test_reveal_position
        checks['named_menu_button_visible'] = (bool(win32gui.IsWindowVisible(app.floating.menu_button))
                                               and win32gui.GetWindowText(app.floating.menu_button) == 'Открыть меню')
        open_original = app.toggle_quick_menu
        def observe_menu():
            open_original()
            motion = app.menu_motion
            checks['menu_opens_with_a_fade'] = motion.open and 0 < float(motion.window.attributes('-alpha')) < MENU_ALPHA
            checks['context_menu_keeps_settings_closed'] = root.state() == 'withdrawn'
            checks['ellipsis_click_keeps_recorder_idle'] = app.state == 'ready'
        app.toggle_quick_menu = observe_menu
        mouse_press_release(app.floating.menu_button)
        wait_for(lambda: app.menu_motion and app.menu_motion.open and app.menu_motion.glass.progress == 1.0,
                 check_menu_navigation, 'Quick menu did not stay open')

    def check_menu_navigation():
        menu = app.menu_motion.window
        measurements['quick_menu_settled'] = {
            'alpha': float(menu.attributes('-alpha')), 'state': menu.state(), 'open': app.menu_motion.open,
            'focus': str(menu.focus_get()), 'native_blur': app.menu_motion.glass.acrylic,
            'backdrop_visible': bool(win32gui.IsWindowVisible(app.menu_motion.glass.hwnd)),
        }
        checks['menu_has_its_own_glass_backdrop'] = has_material(app.menu_motion)
        app.menu_first.invoke()
        checks['settings_opens_with_a_fade'] = 0 < float(root.attributes('-alpha')) < SETTINGS_ALPHA
        wait_for(lambda: app.settings_motion.glass.progress == 1.0 and menu.state() == 'withdrawn',
                 check_shortcut_setup, 'Settings did not open')

    def check_shortcut_setup():
        checks['settings_settles_and_menu_dismisses'] = (
            root.state() == 'normal' and abs(float(root.attributes('-alpha')) - SETTINGS_ALPHA) < .02
            and app.menu_motion.window.state() == 'withdrawn'
        )
        checks['settings_has_its_own_glass_backdrop'] = has_material(app.settings_motion)
        app.hotkey_button.invoke()
        app.capture_hotkey(SimpleNamespace(keysym='F22', keycode=0x85))
        checks['settings_accepts_a_custom_key'] = app.key_var.get() == 'F22' and app.hotkey.thread.is_alive()
        checks['system_registers_custom_combination'] = app.install_hotkey('Ctrl + Shift + F24')
        row = next(w for w in app.preview.master.winfo_children() if isinstance(w, ttk.Checkbutton))
        loc = row.winfo_rootx() + row.winfo_width()*2//3, row.winfo_rooty() + row.winfo_height()//2
        material = app.settings_motion.glass.hwnd
        x, y = win32gui.ScreenToClient(material, loc)
        pos = win32api.MAKELONG(x, y)
        win32gui.SendMessage(material, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, pos)
        win32gui.SendMessage(material, win32con.WM_LBUTTONUP, 0, pos)
        root.after(50, guard(check_picker))

    def check_picker():
        checks['switch_click_works_on_glass_padding'] = app.autopaste.get()
        app.autopaste.set(False)
        app.device_box.invoke()
        wait_for(lambda: app.choices.motion.glass.progress == 1.0, select_device, 'Device picker did not stay open')

    def select_device():
        checks['device_picker_has_its_own_glass_backdrop'] = has_material(app.choices.motion)
        choice = app.choices.window.focus_get()
        checks['device_picker_selects_from_keyboard'] = isinstance(choice, ttk.Button) and 'Системный' in choice.cget('text')
        if isinstance(choice, ttk.Button):
            choice.event_generate('<Return>')
        wait_for(lambda: not app.choices.motion.open, hide_settings, 'Device selection did not dismiss picker')

    def hide_settings():
        app.hide()
        wait_for(lambda: root.state() == 'withdrawn', check_settings_dismissed, 'Settings did not close')

    def check_settings_dismissed():
        checks['settings_fades_closed'] = root.state() == 'withdrawn'
        app.tray.open_glass_menu(win32gui.GetCursorPos())
        wait_for(lambda: app.choices is not None and app.choices.motion.open and
                 app.choices.motion.glass.progress == 1.0, check_tray, 'Tray menu did not stay open')

    def check_tray():
        checks['tray_menu_has_its_own_glass_backdrop'] = has_material(app.choices.motion)
        app.choices.window.event_generate('<Escape>')
        wait_for(lambda: app.choices.window.state() == 'withdrawn', check_release, 'Escape did not dismiss tray menu')

    def check_release():
        # The remaining checks use a synthetic microphone. Remove the test
        # controls from the user's desktop while measuring the idle interval.
        app.summon_enabled.set(False)
        if app.mouse_gesture:
            app.mouse_gesture.close()
            app.mouse_gesture = None
        if app.hotkey:
            app.hotkey.close()
            app.hotkey = None
        app.tray.visible = False
        win32gui.ShowWindow(app.floating.hwnd, win32con.SW_HIDE)
        def released():
            checks['idle_releases_model_and_keeps_button'] = app.session.process is None and bool(win32gui.IsWindow(app.floating.hwnd))
            benchmark_short_phrase()
        wait_for(lambda: app.session.process is None, released, 'Idle model was not released')

    def benchmark_short_phrase():
        # Use the normal microphone byte path, fed exclusively by the test WAV.
        with wave.open(str(app.test_audio), 'rb') as source:
            frames = array('h')
            frames.frombytes(source.readframes(source.getframerate()))
        audio = array('f', (v / 32768 for v in frames)).tobytes()
        recorder = app.recorder
        app.recorder = SimpleNamespace(start=lambda device: None, stop=lambda: audio, stream=None, overflow=False, rate=16000)
        app.test_audio = None
        original_finish = app.finish
        trials = [('cold', 1000), ('warm', 1000)] * 3 + [('immediate', 0)]
        timings = measurements['one_second_phrase'] = []
        recognized = []
        def run(index):
            if index >= len(trials):
                checks['cold_and_warm_recognize_same_short_pcm'] = bool(recognized[0]) and len(set(recognized)) == 1
                app.finish = original_finish
                app.recorder = recorder
                finish()
                return
            kind, head_start = trials[index]
            if kind != 'warm' and app.session.process:
                app.release_idle_model()
            checks['consecutive_recordings_reuse_model'] = checks.get('consecutive_recordings_reuse_model', True) and (
                kind != 'warm' or bool(app.session.process and app.session.process.is_alive()))
            app.toggle()
            def stop():
                start = time.perf_counter()
                def received(text):
                    original_finish(text)
                    timings.append({'model': kind, 'stop_to_text_ms': round((time.perf_counter()-start)*1000)})
                    recognized.append(text)
                    root.after_idle(guard(lambda: run(index+1)))
                app.finish = received
                app.toggle()
            root.after(head_start, guard(stop))
        run(0)

    wait_ready(guard(exercise_appearance))
