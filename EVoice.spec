# -*- mode: python ; coding: utf-8 -*-
from importlib.util import find_spec
from pathlib import Path

datas = [('assets/evoice.ico', 'assets')]
sherpa = Path(find_spec('sherpa_onnx').origin).parent
binaries = [(str(sherpa / 'lib' / 'onnxruntime.dll'), 'sherpa_onnx/lib')]
hiddenimports = ['win32timezone', 'pystray._win32', 'PIL.IcoImagePlugin', 'PIL.PngImagePlugin']


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'PIL.AvifImagePlugin', 'PIL._avif', 'PIL.WebPImagePlugin', 'PIL._webp',
              'PIL._imagingft', 'PIL._imagingcms', 'pystray._darwin', 'pystray._xorg',
              'pystray._gtk', 'pystray._appindicator'],
    noarchive=False,
    optimize=0,
)
# sounddevice's hook also collects PortAudio builds for other operating systems
# and CPU types. Preserve both Windows x64 drivers, including the ASIO variant.
def windows_audio(entries):
    return [entry for entry in entries
            if '_sounddevice_data/portaudio-binaries/' not in entry[0].replace('\\', '/')
            or entry[0].endswith(('libportaudio64bit.dll', 'libportaudio64bit-asio.dll'))]

a.datas = windows_audio(a.datas)
a.binaries = windows_audio(a.binaries)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EVoice',
    icon='assets/evoice.ico',
    manifest='assets/EVoice.manifest',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='EVoice',
)
