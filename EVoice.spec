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
    module_collection_mode={'pystray': 'py'},
    runtime_hooks=[],
    excludes=['unix_desktop', 'unix_input', 'numpy', 'PIL.AvifImagePlugin', 'PIL._avif', 'PIL.WebPImagePlugin', 'PIL._webp',
              'PIL._imagingft', 'PIL._imagingcms', 'pystray._darwin', 'pystray._xorg',
              'pystray._gtk', 'pystray._appindicator'],
    noarchive=False,
    optimize=0,
)
# Windows 10+ supplies UCRT. Stage the two signed VC runtime DLLs from
# Visual Studio REDIST after collection. Only the non-ASIO audio driver is used.
def windows_payload(entry):
    path = entry[0].replace('\\', '/').lower()
    name = path.rsplit('/', 1)[-1]
    if name.startswith(('api-ms-win-', 'vcruntime140')) or name == 'ucrtbase.dll':
        return False
    return '_sounddevice_data/portaudio-binaries/' not in path or name == 'libportaudio64bit.dll'

a.datas = list(filter(windows_payload, a.datas))
a.binaries = list(filter(windows_payload, a.binaries))
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
