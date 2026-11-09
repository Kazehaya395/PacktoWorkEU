"""Isolated, offline Qt startup smoke used only by the packaging CLI flag."""
from __future__ import annotations
import gc
import os
import tempfile
import time
import traceback
from pathlib import Path


def _private_ini_settings(organization, application):
    # The named QSettings(org, app) overload ignores setDefaultFormat and uses
    # NativeFormat. Explicit format/scope are required to avoid native HKCU.
    from PySide6.QtCore import QSettings
    return QSettings(QSettings.IniFormat, QSettings.UserScope, organization, application)


def gui_smoke() -> int:
    # This runs in a separate short-lived build-check process, before paths/UI
    # imports. Never read the user's database/browser selection or native HKCU.
    from sponsorscout.main import _self_check_write
    try:
        with tempfile.TemporaryDirectory(
            prefix='sponsorscout-gui-check-',
            ignore_cleanup_errors=True,
        ) as temporary:
            root = Path(temporary)
            os.environ.update(HOME=str(root), USERPROFILE=str(root),
                APPDATA=str(root/'roaming'), LOCALAPPDATA=str(root/'local'),
                XDG_CONFIG_HOME=str(root/'config'), XDG_CACHE_HOME=str(root/'cache'),
                SPONSORSCOUT_DATA_DIR=str(root/'data'), SPONSORSCOUT_DB_PATH=str(root/'data/check.db'))
            os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
            from PySide6.QtCore import QSettings, QCoreApplication, QEvent
            from PySide6.QtWidgets import QApplication, QTabWidget
            QSettings.setDefaultFormat(QSettings.IniFormat)
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(root/'settings'))
            QSettings.setPath(QSettings.IniFormat, QSettings.SystemScope, str(root/'system-settings'))
            from sponsorscout.ui.app import SponsorScoutApp
            from sponsorscout.ui.tabs import assisted_ingestion
            # Override only the current named-settings consumer in this explicit
            # test process; normal application startup retains its native settings.
            assisted_ingestion.QSettings = _private_ini_settings
            from sponsorscout.ui.async_load import AsyncLoader
            from sponsorscout.ui.style import build_light_palette, build_qss
            class SmokeWindow(SponsorScoutApp):
                def _check_first_run(self):
                    # No welcome prompt, scanning or browser setup in this mode.
                    pass
            app = QApplication(['SponsorScout packaging GUI check'])
            app.setStyle('Fusion')
            app.setPalette(build_light_palette())
            app.setStyleSheet(build_qss())
            window = SmokeWindow()
            window.show()
            tabs = window.findChild(QTabWidget)
            if tabs is None or tabs.count() != 8:
                raise RuntimeError('Expected all eight application tabs')
            for index in range(tabs.count()):
                tabs.setCurrentIndex(index)
                app.processEvents()
            deadline = time.monotonic() + 15
            while any(loader.busy for loader in window.findChildren(AsyncLoader)):
                app.processEvents()
                if time.monotonic() > deadline:
                    raise RuntimeError('GUI background initialization did not finish')
                time.sleep(.01)
            while window.isVisible():
                window.close()
                app.processEvents()
                if time.monotonic() > deadline:
                    raise RuntimeError('GUI could not close cleanly')
                time.sleep(.01)
            if hasattr(window, '_scan_refresh_timer') and window._scan_refresh_timer.isActive():
                window._scan_refresh_timer.stop()
            for loader in window.findChildren(AsyncLoader):
                loader.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            del window
            gc.collect()
            _self_check_write('Eight-tab Qt GUI startup/close: OK (isolated data, no scan/browser)')
        return 0
    except Exception as exc:
        _self_check_write(f'GUI packaging check FAILED: {type(exc).__name__}: {exc}')
        _self_check_write(traceback.format_exc())
        return 1
