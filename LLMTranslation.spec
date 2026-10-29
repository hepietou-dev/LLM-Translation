# -*- mode: python ; coding: utf-8 -*-
# Onedir build.  The NSIS installer ships Node + LibreOffice Kit separately
# under %PUBLIC%\LLMTranslationKit because the native engine cannot start from
# non-ASCII paths (Chinese usernames or folder names).
import os

EXCLUDES = [
    'pandas', 'numpy', 'matplotlib', 'scipy', 'fsspec', 'aiohttp', 'yarl',
    'multidict', 'frozenlist', 'propcache', 'attrs', 'ujson', 'tqdm', 'psutil',
    'yaml', 'tzdata', 'dateutil', 'pydantic', 'pydantic_core', 'annotated_types',
    'typing_inspection', 'bidi', 'fontTools', 'uharfbuzz', 'cryptography',
    'OpenSSL', 'IPython', 'pytest', 'pandas.libs', 'numpy.libs',
]

ROOT = SPECPATH  # noqa: F821  (PyInstaller injects SPECPATH)

a = Analysis(
    [os.path.join(ROOT, 'run_app.py')],
    pathex=[os.path.join(ROOT, 'src')],
    binaries=[],
    datas=[(os.path.join(ROOT, 'config'), 'config'),
           (os.path.join(ROOT, 'assets', 'app.ico'), 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LLMTranslation',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # packed binaries are a common antivirus false positive
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, 'assets', 'app.ico'),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='LLMTranslation',
)
