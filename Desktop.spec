# Native macOS and Linux builds. Keep inference, Tcl/Tk and audio in the offline kit.
from importlib.util import find_spec
from pathlib import Path
import sys
import sysconfig
import os
from PyInstaller.utils.hooks import collect_submodules

mac = sys.platform == 'darwin'
stage = Path(os.environ['EVOICE_RELEASE_ASSETS'])
datas = [(str(stage / name), name if name in ('models', 'licenses') else '.') for name in ('models', 'licenses', 'LICENSE', 'THIRD_PARTY.md')]
lib = Path(find_spec('sherpa_onnx').origin).parent / 'lib'
extension = '*.dylib' if mac else '*.so*'
binaries = [(str(p), 'sherpa_onnx/lib') for p in lib.glob(extension) if p.is_file()]
backend = '_darwin' if mac else '_xorg'
hidden = ['pynput.keyboard.' + backend, 'pynput.mouse.' + backend, 'PIL._tkinter_finder', 'PIL.PngImagePlugin']
if not mac:
    datas.append((str(stage / 'e-voice.png'), '.'))
    system_lib = Path('/usr/lib') / sysconfig.get_config_var('MULTIARCH')
    binaries.append((str(system_lib / 'libportaudio.so.2'), '.'))
    hidden += collect_submodules('Xlib.ext') + ['Xlib.support.unix_connect', 'Xlib.keysymdef.latin1']
excludes = ['numpy', 'evdev', 'app', 'win32gui', 'pystray', 'popup_glass', 'ui_checks',
            'pynput.mouse._win32', 'pynput.keyboard._win32', 'pynput.keyboard._uinput']
if not mac:
    excludes += ['pynput.mouse._darwin', 'pynput.keyboard._darwin', 'AppKit', 'Quartz']
else:
    excludes += ['pynput.mouse._xorg', 'pynput.keyboard._xorg', 'Xlib']
a = Analysis(['desktop_launcher.py'], binaries=binaries, datas=datas, hiddenimports=hidden, excludes=excludes,
             module_collection_mode={'pynput': 'py', 'Xlib': 'py'})
def native_audio(entry):
    path = entry[0].replace('\\', '/').lower()
    return '_sounddevice_data/portaudio-binaries/' not in path or (mac and path.endswith('/libportaudio.dylib'))

a.binaries = list(filter(native_audio, a.binaries))
a.datas = list(filter(native_audio, a.datas))
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='EVoice', console=not mac, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='EVoice', upx=False)
if mac:
    app = BUNDLE(coll, name='E Voice.app', icon='assets/evoice.ico',
                 bundle_identifier='io.github.moldset-mmc.evoice',
                 info_plist={'CFBundleShortVersionString': Path('VERSION').read_text().strip(),
                             'CFBundleVersion': Path('VERSION').read_text().strip(),
                             'NSMicrophoneUsageDescription': 'E Voice распознаёт диктовку на этом Mac.',
                             'NSHighResolutionCapable': True})
