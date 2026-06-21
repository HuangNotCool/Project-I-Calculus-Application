# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all


webview_datas, webview_binaries, webview_hiddenimports = collect_all('webview')

datas = [
    ('templates', 'templates'),
    ('static', 'static'),
    ('migrations', 'migrations'),
    ('fami-exam.cls', '.'),
    ('.env.example', '.'),
] + webview_datas

binaries = webview_binaries
hiddenimports = webview_hiddenimports + [
    'logging.config',
    'sqlalchemy.dialects.sqlite',
    'waitress',
]

a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GiaiTichHUST',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name='GiaiTichHUST',
)
