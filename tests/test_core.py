import sys
from pathlib import Path
import unittest
import queue
import ctypes
import threading
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from speech_engine import segments, SpeechEngine, RATE
from app import Application
from windows_io import GlobalHotkey, INPUT, choose_target, hotkey_label, parse_hotkey, text_key_events
from floating_control import CURSOR_GAP_DIP, HEIGHT, WIDTH, control_metrics, fit_to_work_area, placement_near_cursor
from mouse_gesture import TripleClick


class CoreTests(unittest.TestCase):
    def test_action_is_close_to_pointer_at_all_scales_and_screen_edges(self):
        for dpi in (96, 120, 144, 192, 216, 288):
            for work in ((0, 0, 3840, 2100), (-2560, -100, 0, 1340)):
                left, top, right, bottom = work
                for cursor in ((left, top), (right-1, top), (left, bottom-1), (right-1, bottom-1), ((left+right)//2, (top+bottom)//2)):
                    position, on_left = placement_near_cursor(cursor, work, dpi)
                    metrics = control_metrics(dpi, on_left)
                    x, y = position
                    width, height = metrics['size']
                    self.assertTrue(left <= x and x+width <= right and top <= y and y+height <= bottom)
                    ax, ay, aw, ah = metrics['action']
                    gap = max(x+ax-cursor[0], cursor[0]-(x+ax+aw), y+ay-cursor[1], cursor[1]-(y+ay+ah), 0)
                    self.assertLessEqual(gap, 34*dpi/96)
                    mx, my, mw, mh = metrics['menu']
                    self.assertTrue(mx + mw < ax if on_left else ax + aw < mx)
                    self.assertTrue(0 <= my <= height - mh)

    def test_panel_appears_left_with_a_gap_and_flips_when_space_runs_out(self):
        work = (0, 0, 1600, 900)
        (x, y), mirrored = placement_near_cursor((800, 450), work)
        self.assertTrue(mirrored)
        self.assertEqual(800 - (x + WIDTH), CURSOR_GAP_DIP)
        self.assertLess(y, 450)
        self.assertGreater(y + HEIGHT, 450)
        (x, _), mirrored = placement_near_cursor((8, 450), work)
        self.assertFalse(mirrored)
        self.assertEqual(x - 8, CURSOR_GAP_DIP)

    def test_custom_shortcuts_round_trip_as_keys_or_chords(self):
        for shortcut in ('F9', 'Ctrl + Alt + Space', 'Ctrl + Shift + F10', 'Alt + Num Plus', 'Shift + K'):
            self.assertEqual(hotkey_label(*parse_hotkey(shortcut)), shortcut)
        for invalid in ('Ctrl', 'Ctrl + Ctrl + F8', 'Other + A', 'Ctrl +'):
            with self.assertRaises(ValueError):
                parse_hotkey(invalid)

    def test_consecutive_dictation_inserts_one_trailing_space_per_result(self):
        app = Application.__new__(Application)
        app.preview, app.copy_button, app.status, app.ready = Mock(), Mock(), Mock(), Mock()
        app.autopaste, app.root = Mock(), Mock()
        app.autopaste.get.return_value = True
        app.root.after.side_effect = lambda delay, callback: callback()
        app.closing = False
        app.target = (10, 99)
        with patch('app.modifiers_down', return_value=False), patch('app.foreground', return_value=app.target), \
                patch('app.insert_text', return_value=True) as insert:
            app.finish('Первая фраза.')
            app.finish('Вторая фраза.  ')
            app.finish('  ')
        self.assertEqual(''.join(call.args[0] for call in insert.call_args_list), 'Первая фраза. Вторая фраза. ')
        self.assertEqual(insert.call_count, 2)

    def click(self, gesture, point, now):
        gesture.press(point, now)
        return gesture.release(point, now + 0.03)

    def test_three_quick_clicks_summon_once(self):
        gesture = TripleClick()
        self.assertFalse(self.click(gesture, (500, 300), 1.0))
        self.assertFalse(self.click(gesture, (502, 301), 1.2))
        self.assertTrue(self.click(gesture, (501, 302), 1.4))
        self.assertFalse(self.click(gesture, (501, 302), 1.6))

    def test_slow_or_distant_clicks_do_not_summon(self):
        for positions, times in [([(0, 0)] * 3, [1, 2, 3]), ([(0, 0), (50, 0), (100, 0)], [1, 1.2, 1.4])]:
            gesture = TripleClick()
            self.assertFalse(any(self.click(gesture, point, now) for point, now in zip(positions, times)))

    def test_drag_and_long_press_reset_the_mouse_gesture(self):
        gesture = TripleClick()
        self.click(gesture, (0, 0), 1)
        self.click(gesture, (0, 0), 1.1)
        gesture.press((0, 0), 1.2)
        self.assertFalse(gesture.release((100, 0), 1.3))
        self.assertFalse(self.click(gesture, (100, 0), 1.4))
        gesture.press((100, 0), 1.5)
        self.assertFalse(gesture.release((100, 0), 2.2))

    def test_control_stays_above_taskbar_and_inside_one_monitor(self):
        self.assertEqual(fit_to_work_area((1900, 1020), (0, 0, 1920, 1040)), (1920 - WIDTH, 1040 - HEIGHT))
        self.assertEqual(fit_to_work_area((-1900, -100), (-1280, 0, 0, 984)), (-1280, 0))

    def test_long_audio_is_neither_dropped_nor_duplicated(self):
        audio = np.arange(RATE * 63, dtype=np.float32) / 1e8
        parts = list(segments(audio))
        np.testing.assert_array_equal(np.concatenate(parts), audio)
        self.assertTrue(all(0 < len(x) <= RATE * 20 for x in parts))

    def test_silence_and_tiny_recording_do_not_call_model(self):
        engine = SpeechEngine.__new__(SpeechEngine)
        self.assertEqual(engine.transcribe(np.zeros(RATE)), '')
        self.assertEqual(engine.transcribe(np.ones(100)), '')

    def test_quiet_cut_is_preferred(self):
        audio = np.ones(RATE * 30, dtype=np.float32) * 0.1
        audio[RATE * 17:RATE * 18] = 0
        first = next(segments(audio))
        self.assertTrue(RATE * 17 <= len(first) <= RATE * 18)

    def test_cancel_does_not_start_native_recognition(self):
        engine = SpeechEngine.__new__(SpeechEngine)
        cancelled = threading.Event()
        cancelled.set()
        self.assertEqual(engine.transcribe(np.ones(RATE), cancelled), '')

    def test_windows_input_has_native_64bit_layout(self):
        self.assertEqual(ctypes.sizeof(INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

    def test_caret_can_move_to_a_different_application(self):
        self.assertEqual(choose_target((222, 20), (111, 10), 99), (222, 20))

    def test_own_button_does_not_replace_external_target(self):
        self.assertEqual(choose_target((333, 99), (111, 10), 99), (111, 10))

    def test_no_previous_target_is_not_guessed(self):
        self.assertIsNone(choose_target((333, 99), None, 99))

    def test_russian_input_is_independent_of_keyboard_layout(self):
        self.assertEqual(text_key_events('Я'), [(0, ord('Я'), 4), (0, ord('Я'), 6)])

    def test_unicode_surrogate_pairs_and_line_breaks(self):
        events = text_key_events('😀\r\nА')
        self.assertEqual([x[1] for x in events[:4:2]], [0xD83D, 0xDE00])
        self.assertEqual(events[4:6], [(0x0D, 0, 0), (0x0D, 0, 2)])
        with self.assertRaises(ValueError):
            text_key_events('\0')

    def test_hotkey_conflict_preserves_original_registration(self):
        first = GlobalHotkey('Ctrl + Shift + Space', queue.Queue())
        try:
            with self.assertRaisesRegex(RuntimeError, 'занята'):
                GlobalHotkey('Ctrl + Shift + Space', queue.Queue())
            self.assertTrue(first.thread.is_alive())
        finally:
            first.close()
        self.assertFalse(first.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
