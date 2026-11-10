"""Shared pytest fixtures.

PACKTOWORKEU_DATA_DIR must be set BEFORE importing packtoworkeu.paths,
because paths.USER_DATA_DIR is computed at import time.
"""
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Redirect the per-user data dir into a pytest tmp dir (pre-import).

    ``paths.USER_DATA_DIR`` is computed at import time, so the ``packtoworkeu``
    modules are dropped from ``sys.modules`` and re-imported against the
    patched env var.

    The module set that existed before the purge is snapshotted and restored on
    teardown.  Without that restore the purge leaves *two* live copies of every
    module: the one other test modules imported at collection time, and the
    fresh one later function-level imports resolve through ``sys.modules``.
    A test that monkeypatches an attribute on a module it imported at the top
    of the file (e.g. ``seed_manager.user_ats_path``) would then see its patch
    silently bypassed by code doing ``from packtoworkeu.application import
    seed_manager`` inside a function.
    """
    monkeypatch.setenv("PACKTOWORKEU_DATA_DIR", str(tmp_path / "userdata"))
    saved = {name: mod for name, mod in list(sys.modules.items())
             if name.startswith("packtoworkeu")}
    # Force a re-import of paths so USER_DATA_DIR/SEEDS_DIR point at tmp.
    for name in saved:
        del sys.modules[name]
    try:
        import packtoworkeu.paths as paths
        yield paths.ensure_user_data_dir()
    finally:
        for name in [m for m in list(sys.modules)
                     if m.startswith("packtoworkeu")]:
            del sys.modules[name]
        sys.modules.update(saved)


@pytest.fixture()
def db_path(data_dir):
    from packtoworkeu.db import database as db
    path = str(data_dir / "test.db")
    if os.path.exists(path):
        os.remove(path)
    db.initialize(path)
    return path


_browser_cache = {}


def _test_browser(original_home):
    """Resolve once without launching a browser; works with the SDK on each OS."""
    if not _browser_cache:
        from playwright.sync_api import sync_playwright
        explicit=os.environ.get('PACKTOWORKEU_TEST_BROWSER_EXECUTABLE')
        with sync_playwright() as pw:full=Path(explicit or pw.chromium.executable_path)
        cache=next((p.parent for p in full.parents if p.name.startswith('chromium-')),original_home/'.cache/ms-playwright')
        _browser_cache.update(path=full,cache=cache)
    return _browser_cache['path'],_browser_cache['cache']


@pytest.fixture(autouse=True)
def browser_test_profile(tmp_path, monkeypatch):
    """Explicit TEST-ONLY choice, never a real user's app/browser setup.

    Lean unit runs use a dummy executable at mocked launch sites. Opt-in real
    browser tests require an explicitly installed full SDK Chromium (no shell),
    or PACKTOWORKEU_TEST_BROWSER_EXECUTABLE. SDK launches in legacy fixtures are
    also directed to that full executable, not the separate headless shell.
    Path.home stays isolated even when data_dir reloads PacktoWorkEU modules.
    """
    original_home=Path.home()
    full,cache=_test_browser(original_home)
    home=tmp_path/'test-profile';home.mkdir()
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    monkeypatch.setenv('HOME',str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME',str(home/'.config'))
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH',str(cache))
    if not full.is_file():
        full=home/'unit-only-no-browser';full.write_text('Not a real browser: mocked unit launch sites only.')
    if os.name=='nt':
        # No test may delete real HKCU settings, even when testing purge logic.
        import winreg,ctypes
        monkeypatch.setattr(winreg,'DeleteKey',lambda *args:None)
        monkeypatch.setattr(ctypes.windll.shell32,'IsUserAnAdmin',lambda:0)
        from PySide6.QtCore import QSettings
        previous_format=QSettings.defaultFormat()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,str(home/'.config'))
        monkeypatch.setenv('APPDATA',str(home/'Roaming'))
        monkeypatch.setenv('LOCALAPPDATA',str(home/'Local'))
    from packtoworkeu.services import browser_policy as policy
    chosen=dict(family='Chromium',kind='installed',path=str(full),validated=True,
                fingerprint=policy.fingerprint(str(full)),versions=['test fixture','test fixture'])
    policy.save_selection(chosen)
    from playwright.sync_api import BrowserType
    launch=BrowserType.launch
    def full_browser(self,*args,**kwargs):
        if self.name=='chromium':
            kwargs.setdefault('channel','chromium')
            kwargs.setdefault('executable_path',str(full))
        return launch(self,*args,**kwargs)
    monkeypatch.setattr(BrowserType,'launch',full_browser)
    try:yield home
    finally:
        policy.remove_private_temp()
        if os.name=='nt':QSettings.setDefaultFormat(previous_format)
