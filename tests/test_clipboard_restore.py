import os
import sys
import unittest
from unittest.mock import Mock

from unix_input import DesktopInput


class Clipboard:
    def __init__(self):
        self.text = 'original'
        self.owner = 1
        self.callbacks = []

    def clipboard_get(self):
        return self.text

    def clipboard_clear(self):
        self.text = ''
        self.owner = 2

    def clipboard_append(self, text):
        self.text += text

    def update_idletasks(self):
        pass

    def after(self, delay, callback):
        self.callbacks.append(callback)


class ClipboardRestoreTests(unittest.TestCase):
    def setUp(self):
        self.board = Clipboard()
        self.io = DesktopInput.__new__(DesktopInput)
        self.io.root = self.board
        self.io._pending_clipboard = None
        self.io._clipboard_stamp = Mock(side_effect=lambda: self.board.owner)

    def test_untouched_clipboard_is_restored_after_paste(self):
        self.io._publish_for_paste('dictation')
        self.assertEqual(self.board.text, 'dictation')
        self.board.callbacks.pop()()
        self.assertEqual(self.board.text, 'original')

    def test_external_copy_even_of_identical_text_survives(self):
        for replacement in ('new copy', 'dictation'):
            with self.subTest(replacement=replacement):
                self.board.text = 'original'
                self.io._publish_for_paste('dictation')
                self.board.owner = 3
                self.board.text = replacement
                self.board.callbacks.pop()()
                self.assertEqual(self.board.text, replacement)

    def test_changed_text_with_same_owner_survives(self):
        self.io._publish_for_paste('dictation')
        self.board.text = 'new copy'
        self.board.callbacks.pop()()
        self.assertEqual(self.board.text, 'new copy')

    def test_repeated_pastes_restore_original_and_ignore_obsolete_callback(self):
        self.io._publish_for_paste('one')
        self.io._publish_for_paste('two')
        self.board.callbacks[0]()
        self.assertEqual(self.board.text, 'two')
        self.board.callbacks[1]()
        self.assertEqual(self.board.text, 'original')

    def test_explicit_copy_invalidates_pending_restore(self):
        self.io._publish_for_paste('dictation')
        self.io.copy('dictation')
        self.board.callbacks.pop()()
        self.assertEqual(self.board.text, 'dictation')

    def test_unknown_owner_disables_deferred_write(self):
        self.board.owner = None
        self.io._clipboard_stamp = lambda: None
        self.io._publish_for_paste('dictation')
        self.assertEqual(self.board.callbacks, [])

    @unittest.skipUnless(sys.platform == 'darwin' and os.getenv('GITHUB_ACTIONS') == 'true'
                         and os.getenv('RUNNER_ENVIRONMENT') == 'github-hosted', 'disposable Mac pasteboard')
    def test_native_mac_copy_after_paste_is_preserved(self):
        import tkinter as tk
        from AppKit import NSPasteboard, NSPasteboardTypeString
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.withdraw()
        io = DesktopInput(root, None)
        io.copy('original')
        io._publish_for_paste('dictation')
        self.assertIsNotNone(io._pending_clipboard)
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_('external copy', NSPasteboardTypeString)
        done = tk.BooleanVar(root, False)
        root.after(800, lambda: done.set(True))
        root.wait_variable(done)
        self.assertIsNone(io._pending_clipboard)
        self.assertEqual(pasteboard.stringForType_(NSPasteboardTypeString), 'external copy')
