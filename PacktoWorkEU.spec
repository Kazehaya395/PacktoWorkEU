# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PKG_DIR = ROOT / 'packtoworkeu'
_CODE_SUFFIXES = {'.py', '.pyc', '.pyo', '.pyd', '.so'}


def _collect_local_datas(pkg_dir: Path) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for path in sorted(pkg_dir.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        parts = rel.parts
        if 'tests' in parts or '__pycache__' in parts:
            continue
        if path.suffix.lower() in _CODE_SUFFIXES:
            continue
        items.append((str(path), str(rel.parent)))
    return items


def _collect_local_submodules(pkg_dir: Path) -> list[str]:
    mods: list[str] = ['packtoworkeu']
    for path in sorted(pkg_dir.rglob('*.py')):
        rel = path.relative_to(ROOT)
        parts = rel.parts
        if 'tests' in parts or '__pycache__' in parts:
            continue
        mod = '.'.join(parts[:-1]) if path.name == '__init__.py' else '.'.join((*parts[:-1], path.stem))
        if mod and mod != 'packtoworkeu.tests' and not mod.startswith('packtoworkeu.tests.'):
            mods.append(mod)
    return mods


datas = [
    (src, dst)
    for src, dst in collect_data_files('packtoworkeu', excludes=['tests', 'tests/*'])
    if not str(dst).replace('\\', '/').startswith('packtoworkeu/tests')
]
_seen_datas = {(str(Path(src).resolve()), str(dst).replace('\\', '/')) for src, dst in datas}
for _src, _dst in _collect_local_datas(PKG_DIR):
    _key = (str(Path(_src).resolve()), str(_dst).replace('\\', '/'))
    if _key not in _seen_datas:
        datas.append((_src, _dst))
        _seen_datas.add(_key)

if not any(Path(_src).name == 'schema.sql' for _src, _ in datas):
    raise SystemExit('PacktoWorkEU.spec error: packtoworkeu/db/schema.sql missing from datas')

hiddenimports = ['greenlet', 'pyee'] + [
    'truststore',
    'shiboken6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
]
hiddenimports += collect_submodules(
    'packtoworkeu',
    filter=lambda name: name != 'packtoworkeu.tests' and not name.startswith('packtoworkeu.tests.'),
)
for _mod in _collect_local_submodules(PKG_DIR):
    if _mod not in hiddenimports:
        hiddenimports.append(_mod)


a = Analysis(
    [str(ROOT / 'packtoworkeu' / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(Path(SPECPATH) / 'build_hooks')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pandas',
        'PIL',
        'bs4',
        'lxml',
        'tkinter',
        'pytest',
        'packtoworkeu.tests',
        'PySide6.QtQuick',
        'PySide6.QtQuickWidgets',
        'PySide6.QtQuickControls2',
        'PySide6.QtQuickTest',
        'PySide6.QtQml',
        'PySide6.QtQmlFeatures',
        'PySide6.QtDesigner',
        'PySide6.QtHelp',
        'PySide6.QtSql',
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        'PySide6.QtUiTools',
        'PySide6.QtXml',
        'PySide6.QtTest',
        'PySide6.QtConcurrent',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtWebChannel',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DAnimation',
        'PySide6.Qt3DExtras',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtBluetooth',
        'PySide6.QtNfc',
        'PySide6.QtPositioning',
        'PySide6.QtLocation',
        'PySide6.QtRemoteObjects',
        'PySide6.QtScxml',
        'PySide6.QtSensors',
        'PySide6.QtSerialBus',
        'PySide6.QtSerialPort',
        'PySide6.QtStateMachine',
        'PySide6.QtTextToSpeech',
        'PySide6.QtWebSockets',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PacktoWorkEU',
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
    icon=[str(ROOT / 'packtoworkeu' / 'data' / 'packtoworkeu.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='PacktoWorkEU',
)
