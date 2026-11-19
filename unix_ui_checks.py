"""Exercise the shipping desktop with public fixture audio on native runners."""
import json
import time


def run(app, output):
    root = app.root
    checks = {}
    started = time.monotonic()
    def done(error=None):
        if error:
            checks['error'] = str(error)
        output.write_text(json.dumps({**checks, 'passed': not error and all(checks.values())}, indent=2))
        app.close()
    root.report_callback_exception = lambda kind, value, tb: done(value)
    def begin():
        checks['idle_starts_without_model'] = app.engine.process is None
        checks['orb_present'] = app.orb.winfo_ismapped() == 1
        app.hide_orb()
        checks['orb_hides'] = app.orb.state() == 'withdrawn'
        app.visible.set(True)
        app.show_changed()
        app.settings()
        app.action.invoke()
        checks['recording'] = app.state == 'recording' and app.orb.state() != 'withdrawn'
        root.after(120, stop)
    def stop():
        app.action.invoke()
        checks['decoding'] = app.state == 'transcribing'
        root.after(50, wait)
    def wait():
        if app.state == 'ready':
            checks['text'] = 'похвал' in app.text.casefold()
            checks['preview'] = app.preview.get('1.0', 'end-1c') == app.text
            app.copy.invoke()
            checks['clipboard'] = bool(app.text) and root.clipboard_get() == app.text
            checks['worker_warm'] = bool(app.engine.process and app.engine.process.is_alive())
            app.release_model()
            checks['worker_released'] = app.engine.process is None
            done()
        elif time.monotonic()-started < 90:
            root.after(50, wait)
        else:
            done('Recognition timeout')
    root.after(450, begin)
